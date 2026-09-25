"""
routes.py -- reporting module. No table.json/schema.sql: this module
owns no data of its own, only aggregates existing sale/sale_item/product
data. app.py's second pass registers any module's routes.py regardless
of whether it also has a table.json, which is what makes a table-less
module like this possible without any change to app.py itself.

GET /api/reporting/sales-summary[?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD]
groups sale_item by (product_id, unit_price_at_sale) across ALL sales
(optionally date-filtered against sale.sold_at) -- e.g. "3x fish sold at
10000" and "2x fish sold at 12000" (a price change mid-period) surface
as two separate rows, since they're genuinely different transactions at
different prices, not one blended average. Grouping on unit_price_at_sale
(the snapshotted price, not product.price) is deliberate for the same
reason sale_item snapshots it in the first place: a later price change
must not blend or reinterpret historical sales.

start_date is inclusive from midnight; end_date is inclusive through the
end of that calendar day (implemented as < end_date + 1 day, not <=
end_date, since sold_at is a timestamp, not a bare date).
"""

from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request
from sqlalchemy import select, func
from sqlalchemy.engine import Engine
from sqlalchemy import Table


def _json_error(message: str, code: int):
    response = jsonify({"error": message})
    response.status_code = code
    return response


def _parse_date_arg(value: str, field_name: str):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date(), None
    except ValueError:
        return None, (f"{field_name} must be in YYYY-MM-DD format", 422)


def build_routes(tables: dict[str, Table], engine: Engine) -> Blueprint:
    sale = tables["sale"]
    sale_item = tables["sale_item"]
    product = tables["product"]
    bp = Blueprint("reporting_routes", __name__)

    @bp.get("/api/reporting/sales-summary")
    def sales_summary():
        start_date_str = request.args.get("start_date")
        end_date_str = request.args.get("end_date")

        conditions = []

        if start_date_str:
            start_date, error = _parse_date_arg(start_date_str, "start_date")
            if error:
                return _json_error(*error)
            conditions.append(sale.c.sold_at >= start_date)

        if end_date_str:
            end_date, error = _parse_date_arg(end_date_str, "end_date")
            if error:
                return _json_error(*error)
            conditions.append(sale.c.sold_at < end_date + timedelta(days=1))

        query = (
            select(
                sale_item.c.product_id,
                product.c.name.label("product_name"),
                sale_item.c.unit_price_at_sale,
                func.sum(sale_item.c.quantity).label("total_quantity"),
                func.sum(sale_item.c.quantity * sale_item.c.unit_price_at_sale).label("total_revenue"),
                func.count(func.distinct(sale_item.c.sale_id)).label("sale_count"),
            )
            .select_from(sale_item)
            .join(product, product.c.id == sale_item.c.product_id)
            .join(sale, sale.c.id == sale_item.c.sale_id)
            .group_by(sale_item.c.product_id, product.c.name, sale_item.c.unit_price_at_sale)
            .order_by(product.c.name, sale_item.c.unit_price_at_sale)
        )

        for condition in conditions:
            query = query.where(condition)

        with engine.connect() as conn:
            rows = conn.execute(query).mappings().all()

        return jsonify([dict(r) for r in rows])

    return bp