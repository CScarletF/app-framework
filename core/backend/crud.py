"""
crud.py -- generic CRUD route builder.

This is the Python equivalent of the old CrudController.php: one set of
list/show/create/update/delete functions that work against ANY table,
driven entirely by each module's table.json (required fields, allowed
values) and generated table_core.py (actual column/type info). No module
should ever need custom Python here for plain CRUD -- if a module needs
real business logic beyond CRUD, that's the one case that earns its own
route file.

Same safety pattern as before: the table object itself is only ever
built from a module folder that's been explicitly loaded by app.py (never
from user input), and all values go through SQLAlchemy's parameter
binding -- never raw string interpolation.
"""

from flask import Blueprint, jsonify, request
from sqlalchemy import select, insert, update, delete
from sqlalchemy.engine import Engine
from sqlalchemy import Table


def _json_error(message: str, code: int):
    response = jsonify({"error": message})
    response.status_code = code
    return response


def _validate(payload: dict, config: dict, is_update: bool = False):
    if not is_update:
        for field in config.get("required", []):
            if field not in payload or payload[field] in (None, ""):
                return f"Missing required field: {field}", 422

    for field, allowed in config.get("allowed_values", {}).items():
        if field in payload and payload[field] not in allowed:
            return (
                f"Invalid value for {field}: must be one of {', '.join(allowed)}",
                422,
            )
    return None, None


def build_module_blueprint(module_name: str, table: Table, config: dict, engine: Engine) -> Blueprint:
    """One Blueprint per module, mounted at /api/<table_name>. Keeping
    each module on its own Blueprint (rather than one giant shared
    blueprint) means a broken module can be traced straight back to its
    own file, not buried inside a shared route table."""

    table_name = config["table"]
    bp = Blueprint(f"crud_{module_name}", __name__)

    @bp.get(f"/api/{table_name}")
    def index():
        with engine.connect() as conn:
            rows = conn.execute(select(table).order_by(table.c.id.desc())).mappings().all()
        return jsonify([dict(r) for r in rows])

    @bp.get(f"/api/{table_name}/<int:row_id>")
    def show(row_id):
        with engine.connect() as conn:
            row = conn.execute(select(table).where(table.c.id == row_id)).mappings().first()
        if row is None:
            return _json_error("Not found", 404)
        return jsonify(dict(row))

    @bp.post(f"/api/{table_name}")
    def create():
        payload = request.get_json(silent=True)
        if payload is None:
            return _json_error("Invalid JSON", 400)

        message, code = _validate(payload, config)
        if message:
            return _json_error(message, code)

        columns = {k: v for k, v in payload.items() if k in table.c.keys()}

        with engine.begin() as conn:
            result = conn.execute(insert(table).values(**columns).returning(table))
            row = result.mappings().first()

        return jsonify(dict(row)), 201

    @bp.put(f"/api/{table_name}/<int:row_id>")
    def update_row(row_id):
        payload = request.get_json(silent=True)
        if payload is None:
            return _json_error("Invalid JSON", 400)

        message, code = _validate(payload, config, is_update=True)
        if message:
            return _json_error(message, code)

        columns = {k: v for k, v in payload.items() if k in table.c.keys()}
        if not columns:
            return _json_error("No valid fields to update", 400)

        with engine.begin() as conn:
            result = conn.execute(
                update(table).where(table.c.id == row_id).values(**columns).returning(table)
            )
            row = result.mappings().first()

        if row is None:
            return _json_error("Not found", 404)
        return jsonify(dict(row))

    @bp.delete(f"/api/{table_name}/<int:row_id>")
    def delete_row(row_id):
        with engine.begin() as conn:
            result = conn.execute(
                delete(table).where(table.c.id == row_id).returning(table.c.id)
            )
            deleted = result.first()

        if deleted is None:
            return _json_error("Not found", 404)
        return jsonify({"deleted": True})

    return bp
