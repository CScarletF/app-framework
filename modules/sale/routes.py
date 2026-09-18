"""
routes.py -- sale module's carve-out beyond generic CRUD.

Sale and sale_item rows are never created via the generic CRUD POST --
only through /api/sale/checkout below, which atomically: resolves each
sold item through recipe (a menu item decrements its ingredients, a
plain item decrements itself), aggregates the resulting stock impact per
underlying product, locks those rows, validates stock, inserts the sale,
inserts each line item with a SNAPSHOTTED unit price (not a live join to
product.price -- a later price change must never rewrite past sales),
and decrements stock. All in one transaction, so a failure partway
through leaves nothing committed.

Row locking (with_for_update) on the product rows, not just the (now
removed) CHECK(stock_quantity >= 0) constraint, is deliberate: without
it, two concurrent checkouts for the last unit could both read
stock_quantity=1, both pass their own quantity check -- by which point
both sale/sale_item rows are already committed for stock that was never
actually available. Locking makes the second checkout wait, re-read the
now-updated stock, and fail cleanly with a normal "insufficient stock"
response instead. Locking covers every product touched by ANY cart
line's recipe, not just the directly-sold products -- two different
drinks sharing an ingredient in one cart must not race each other
either.

OVERRIDE: an item may set "override": true, meaning the cashier has
confirmed the system's stock count is wrong and physical stock still
exists. This skips the sufficiency check for whatever underlying
product(s) that item resolves to (itself if plain, its ingredients if
recipe-based) and allows stock_quantity to go negative -- the DB's
stock_quantity >= 0 CHECK constraint has been dropped for this reason
(see drop_stock_check.sql). LIMITATION: override is resolved at the same
aggregated level as the normal stock check -- if two cart lines share an
underlying product and only one is marked override, the whole combined
shortfall for that product is allowed through, not just the overridden
line's share. This mirrors the pre-existing aggregation behavior of
required_decrement itself.

/api/sale/<id>/items is a read-only join, used by the frontend's detail
("View") screen -- crud.py's generic show() only returns the sale row
itself, never its line items.

/api/sale/<id>/void reverses checkout's stock effects before deleting the
sale (sale_item.sale_id -> sale(id) is ON DELETE RESTRICT -- a bare
delete would fail, and even if it didn't, it would never give back the
stock checkout took). It re-resolves each sale_item's product through the
CURRENT recipe table, the same way checkout does, since no snapshot of
"what a recipe looked like at sale time" is stored anywhere -- unlike
unit_price_at_sale, which IS snapshotted. If a recipe is edited between a
sale and its void, the restock follows the edited recipe, not the
original one. Accepted limitation; a full fix would mean snapshotting
resolved ingredient decrements into sale_item at checkout time, which is
a schema change beyond what void's current shape covers.
"""

from flask import Blueprint, jsonify, request
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy import Table


def _json_error(message: str, code: int):
    response = jsonify({"error": message})
    response.status_code = code
    return response


def _resolve_stock_impact(conn, recipe, items):
    """Given [{product_id, quantity}, ...], returns {product_id: total_qty_to_decrement}.
    A product_id with recipe rows contributes to its ingredients instead
    of itself; a plain product_id contributes to itself. Aggregated
    across all items so two cart lines sharing an ingredient combine into
    one combined requirement, not two independently-checked partials.
    """
    if not items:
        return {}

    product_ids = [entry["product_id"] for entry in items]

    recipe_rows = conn.execute(
        select(recipe.c.menu_item_product_id, recipe.c.ingredient_product_id, recipe.c.quantity_required)
        .where(recipe.c.menu_item_product_id.in_(product_ids))
    ).mappings().all()

    recipes_by_menu_item = {}
    for r in recipe_rows:
        recipes_by_menu_item.setdefault(r["menu_item_product_id"], []).append(r)

    impact = {}
    for entry in items:
        pid = entry["product_id"]
        qty = entry["quantity"]
        ingredients = recipes_by_menu_item.get(pid)
        if ingredients:
            for ing in ingredients:
                impact[ing["ingredient_product_id"]] = (
                    impact.get(ing["ingredient_product_id"], 0)
                    + ing["quantity_required"] * qty
                )
        else:
            impact[pid] = impact.get(pid, 0) + qty

    return impact


def build_routes(tables: dict[str, Table], engine: Engine) -> Blueprint:
    sale = tables["sale"]
    sale_item = tables["sale_item"]
    product = tables["product"]
    recipe = tables["recipe"]
    bp = Blueprint("sale_routes", __name__)

    @bp.post("/api/sale/checkout")
    def checkout():
        payload = request.get_json(silent=True)
        if payload is None:
            return _json_error("Invalid JSON", 400)

        items = payload.get("items")
        payment_method = payload.get("payment_method")

        if not items or not isinstance(items, list):
            return _json_error("items must be a non-empty array", 422)
        if not payment_method:
            return _json_error("Missing required field: payment_method", 422)

        for entry in items:
            if "product_id" not in entry or "quantity" not in entry:
                return _json_error("Each item needs product_id and quantity", 422)
            if not isinstance(entry["quantity"], int) or entry["quantity"] <= 0:
                return _json_error("quantity must be a positive integer", 422)
            if "override" in entry and not isinstance(entry["override"], bool):
                return _json_error("override must be a boolean", 422)

        with engine.begin() as conn:
            requested_product_ids = [entry["product_id"] for entry in items]
            required_decrement = _resolve_stock_impact(conn, recipe, items)

            override_items = [entry for entry in items if entry.get("override")]
            override_pids = set(_resolve_stock_impact(conn, recipe, override_items).keys())

            all_product_ids = set(required_decrement.keys()) | set(requested_product_ids)
            locked_products = conn.execute(
                select(product.c.id, product.c.price, product.c.stock_quantity, product.c.name)
                .where(product.c.id.in_(all_product_ids))
                .with_for_update()
            ).mappings().all()
            products_by_id = {p["id"]: p for p in locked_products}

            missing = [pid for pid in requested_product_ids if pid not in products_by_id]
            if missing:
                return _json_error(f"Unknown product_id(s): {missing}", 422)

            for pid, needed in required_decrement.items():
                if pid in override_pids:
                    continue
                p = products_by_id.get(pid)
                if p is None:
                    return _json_error(f"Unknown ingredient product_id: {pid}", 422)
                if p["stock_quantity"] < needed:
                    return _json_error(
                        f"Insufficient stock for '{p['name']}': "
                        f"requested {needed}, have {p['stock_quantity']}",
                        409,
                    )

            total = sum(
                float(products_by_id[entry["product_id"]]["price"]) * entry["quantity"]
                for entry in items
            )

            sale_row = conn.execute(
                sale.insert().values(total=total, payment_method=payment_method).returning(sale)
            ).mappings().first()

            for entry in items:
                p = products_by_id[entry["product_id"]]
                conn.execute(
                    sale_item.insert().values(
                        sale_id=sale_row["id"],
                        product_id=p["id"],
                        quantity=entry["quantity"],
                        unit_price_at_sale=p["price"],
                    )
                )

            for pid, needed in required_decrement.items():
                conn.execute(
                    product.update()
                    .where(product.c.id == pid)
                    .values(stock_quantity=product.c.stock_quantity - needed)
                )

        return jsonify(dict(sale_row)), 201

    @bp.get("/api/sale/<int:sale_id>/items")
    def sale_items(sale_id):
        with engine.connect() as conn:
            rows = conn.execute(
                select(
                    sale_item.c.quantity,
                    sale_item.c.unit_price_at_sale,
                    product.c.name.label("product_name"),
                )
                .join(product, product.c.id == sale_item.c.product_id)
                .where(sale_item.c.sale_id == sale_id)
                .order_by(product.c.name)
            ).mappings().all()
        return jsonify([dict(r) for r in rows])

    @bp.delete("/api/sale/<int:sale_id>/void")
    def void_sale(sale_id):
        with engine.begin() as conn:
            sold_items = conn.execute(
                select(sale_item.c.product_id, sale_item.c.quantity)
                .where(sale_item.c.sale_id == sale_id)
            ).mappings().all()

            if not sold_items:
                existing = conn.execute(select(sale.c.id).where(sale.c.id == sale_id)).first()
                if existing is None:
                    return _json_error("Not found", 404)

            restock = _resolve_stock_impact(
                conn, recipe,
                [{"product_id": i["product_id"], "quantity": i["quantity"]} for i in sold_items],
            )

            for pid, amount in restock.items():
                conn.execute(
                    product.update()
                    .where(product.c.id == pid)
                    .values(stock_quantity=product.c.stock_quantity + amount)
                )

            conn.execute(sale_item.delete().where(sale_item.c.sale_id == sale_id))
            result = conn.execute(sale.delete().where(sale.c.id == sale_id).returning(sale.c.id))
            deleted = result.first()

        if deleted is None:
            return _json_error("Not found", 404)
        return jsonify({"voided": True, "restocked_items": len(sold_items)})

    return bp
