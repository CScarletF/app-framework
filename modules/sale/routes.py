"""
routes.py -- sale module's carve-out beyond generic CRUD.

Sale and sale_item rows are never created via the generic CRUD POST --
only through /api/sale/checkout below, which atomically: locks the
involved product rows, validates stock, inserts the sale, inserts each
line item with a SNAPSHOTTED unit price (not a live join to product.price
-- a later price change must never rewrite past sales), and decrements
stock. All in one transaction, so a failure partway through leaves
nothing committed.

Row locking (with_for_update) on the product rows, not just the
CHECK(stock_quantity >= 0) constraint, is deliberate: without it, two
concurrent checkouts for the last unit could both read stock_quantity=1,
both pass their own quantity check, and only the second UPDATE would be
caught by the CHECK constraint -- by which point the first sale/sale_item
rows are already committed for stock that was never actually available.
Locking makes the second checkout wait, re-read the now-updated stock,
and fail cleanly with a normal "insufficient stock" response instead.

/api/sale/<id>/items is a read-only join, used by the frontend's detail
("View") screen -- crud.py's generic show() only returns the sale row
itself, never its line items.
"""

from flask import Blueprint, jsonify, request
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy import Table


def _json_error(message: str, code: int):
    response = jsonify({"error": message})
    response.status_code = code
    return response


def build_routes(tables: dict[str, Table], engine: Engine) -> Blueprint:
    sale = tables["sale"]
    sale_item = tables["sale_item"]
    product = tables["product"]
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

        with engine.begin() as conn:
            product_ids = [entry["product_id"] for entry in items]
            locked_products = conn.execute(
                select(product.c.id, product.c.price, product.c.stock_quantity, product.c.name)
                .where(product.c.id.in_(product_ids))
                .with_for_update()
            ).mappings().all()
            products_by_id = {p["id"]: p for p in locked_products}

            missing = [pid for pid in product_ids if pid not in products_by_id]
            if missing:
                return _json_error(f"Unknown product_id(s): {missing}", 422)

            total = 0
            for entry in items:
                p = products_by_id[entry["product_id"]]
                if p["stock_quantity"] < entry["quantity"]:
                    return _json_error(
                        f"Insufficient stock for '{p['name']}': "
                        f"requested {entry['quantity']}, have {p['stock_quantity']}",
                        409,
                    )
                total += float(p["price"]) * entry["quantity"]

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
                conn.execute(
                    product.update()
                    .where(product.c.id == p["id"])
                    .values(stock_quantity=p["stock_quantity"] - entry["quantity"])
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
            items = conn.execute(
                select(sale_item.c.product_id, sale_item.c.quantity)
                .where(sale_item.c.sale_id == sale_id)
            ).mappings().all()

            if not items:
                existing = conn.execute(select(sale.c.id).where(sale.c.id == sale_id)).first()
                if existing is None:
                    return _json_error("Not found", 404)

            for item in items:
                conn.execute(
                    product.update()
                    .where(product.c.id == item["product_id"])
                    .values(stock_quantity=product.c.stock_quantity + item["quantity"])
                )

            conn.execute(sale_item.delete().where(sale_item.c.sale_id == sale_id))
            result = conn.execute(sale.delete().where(sale.c.id == sale_id).returning(sale.c.id))
            deleted = result.first()

        if deleted is None:
            return _json_error("Not found", 404)
        return jsonify({"voided": True, "restocked_items": len(items)})
    return bp