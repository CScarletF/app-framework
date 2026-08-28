"""
routes.py -- equipment module's export-to-Excel route.

Not a CRUD verb, so it doesn't belong in crud.py (same reasoning as
assignment/routes.py's availability query). Generates the .xlsx in
memory -- no temp files on disk, no cleanup to worry about.
"""

from io import BytesIO
from flask import Blueprint, send_file
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy import Table
from openpyxl import Workbook


def build_routes(tables: dict[str, Table], engine: Engine) -> Blueprint:
    equipment = tables["equipment"]
    bp = Blueprint("equipment_routes", __name__)

    @bp.get("/api/equipment/export/xlsx")
    def export_xlsx():
        with engine.connect() as conn:
            rows = conn.execute(select(equipment).order_by(equipment.c.id)).mappings().all()

        wb = Workbook()
        ws = wb.active
        ws.title = "Equipment"

        columns = list(equipment.columns.keys())
        ws.append(columns)

        for row in rows:
            # str() on everything -- datetimes and None need to be
            # excel-safe values, not raise on openpyxl's stricter typing.
            ws.append([str(row[c]) if row[c] is not None else "" for c in columns])

        buffer = BytesIO()
        wb.save(buffer)
        buffer.seek(0)

        return send_file(
            buffer,
            as_attachment=True,
            download_name="equipment_export.xlsx",
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    return bp
