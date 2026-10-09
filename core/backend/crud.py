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

Optional table.json keys handled here:
  required, allowed_values, distinct_pairs, sort   (unchanged)
  hashed_fields    -- columns whose incoming value is password-hashed
                      before storage (auth.hash_password, which enforces
                      the length policy) and which are REMOVED from every
                      response: list, show, create, update, distinct.
  readonly_fields  -- columns the client may never write (stripped from
                      payloads silently).
  disable_delete   -- DELETE answers 405 for this table.

Always read-only for clients, on every table: id, created_at, updated_at,
created_by, updated_by. created_by/updated_by are filled from the
logged-in session (auth.audit_values) whenever the column exists, with no
per-module config.

Authorization is NOT checked in this file's handlers. Each mutating
handler only DECLARES its action via @requires; the single before_request
hook in auth.py enforces it (and also guarantees the user is logged in).
"""

from flask import Blueprint, jsonify, request
from sqlalchemy import Boolean, Table, delete, insert, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from auth import audit_values, hash_password, requires

ALWAYS_READONLY = {"id", "created_at", "updated_at", "created_by", "updated_by"}

_TRUE_STRINGS = {"true", "1", "on", "yes"}
_FALSE_STRINGS = {"false", "0", "off", "no"}


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
    for field_a, field_b in config.get("distinct_pairs", []):
        if field_a in payload and field_b in payload and payload[field_a] == payload[field_b]:
            return f"{field_a} and {field_b} must be different", 422
    return None, None


def _coerce_bool(value):
    """HTML <select> fields submit the strings "true"/"false"; SQLAlchemy's
    Boolean type rejects strings outright, so coerce here."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _TRUE_STRINGS:
            return True
        if lowered in _FALSE_STRINGS:
            return False
    raise ValueError("expected true or false")


def _build_columns(payload: dict, table: Table, config: dict):
    """Filters a payload down to writable real columns, hashing and
    coercing as needed. Returns (columns, error_message)."""
    blocked = ALWAYS_READONLY | set(config.get("readonly_fields", []))
    hashed = set(config.get("hashed_fields", []))
    column_names = table.c.keys()

    columns = {}
    for key, value in payload.items():
        if key not in column_names or key in blocked:
            continue
        if key in hashed:
            try:
                value = hash_password(value)
            except ValueError as e:
                return None, str(e)
        elif isinstance(table.c[key].type, Boolean) and value is not None:
            try:
                value = _coerce_bool(value)
            except ValueError:
                return None, f"Invalid value for {key}: expected true or false"
        columns[key] = value
    return columns, None


def _public(row, hidden: set) -> dict:
    return {k: v for k, v in dict(row).items() if k not in hidden}


def build_module_blueprint(module_name: str, table: Table, config: dict, engine: Engine) -> Blueprint:
    """One Blueprint per module, mounted at /api/<table_name>. Keeping
    each module on its own Blueprint (rather than one giant shared
    blueprint) means a broken module can be traced straight back to its
    own file, not buried inside a shared route table."""

    table_name = config["table"]
    hidden = set(config.get("hashed_fields", []))
    bp = Blueprint(f"crud_{module_name}", __name__)

    sort_columns = [c for c in config.get("sort", []) if c in table.c.keys()]
    order_by_clause = [table.c[c] for c in sort_columns] if sort_columns else [table.c.id.desc()]

    @bp.get(f"/api/{table_name}")
    def index():
        with engine.connect() as conn:
            rows = conn.execute(select(table).order_by(*order_by_clause)).mappings().all()
        return jsonify([_public(r, hidden) for r in rows])

    @bp.get(f"/api/{table_name}/distinct/<column>")
    def distinct_values(column):
        # Hashed columns answer exactly like nonexistent ones, so this
        # endpoint can never be used to read them.
        if column not in table.c.keys() or column in hidden:
            return _json_error(f"Unknown column: {column}", 404)
        with engine.connect() as conn:
            values = conn.execute(
                select(table.c[column])
                .distinct()
                .where(table.c[column].isnot(None))
                .order_by(table.c[column])
            ).scalars().all()
        return jsonify(values)

    @bp.get(f"/api/{table_name}/<int:row_id>")
    def show(row_id):
        with engine.connect() as conn:
            row = conn.execute(select(table).where(table.c.id == row_id)).mappings().first()
        if row is None:
            return _json_error("Not found", 404)
        return jsonify(_public(row, hidden))

    @bp.post(f"/api/{table_name}")
    @requires(table_name, "create")
    def create():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return _json_error("Invalid JSON", 400)

        message, code = _validate(payload, config)
        if message:
            return _json_error(message, code)

        columns, error = _build_columns(payload, table, config)
        if error:
            return _json_error(error, 422)
        columns.update(audit_values(table, creating=True))

        try:
            with engine.begin() as conn:
                result = conn.execute(insert(table).values(**columns).returning(table))
                row = result.mappings().first()
        except IntegrityError as e:
            return _json_error(f"Constraint violation: {e.orig.diag.message_primary}", 409)

        return jsonify(_public(row, hidden)), 201

    @bp.put(f"/api/{table_name}/<int:row_id>")
    @requires(table_name, "update")
    def update_row(row_id):
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return _json_error("Invalid JSON", 400)

        message, code = _validate(payload, config, is_update=True)
        if message:
            return _json_error(message, code)

        columns, error = _build_columns(payload, table, config)
        if error:
            return _json_error(error, 422)
        if not columns:
            return _json_error("No valid fields to update", 400)
        columns.update(audit_values(table, creating=False))

        try:
            with engine.begin() as conn:
                result = conn.execute(
                    update(table).where(table.c.id == row_id).values(**columns).returning(table)
                )
                row = result.mappings().first()
        except IntegrityError as e:
            return _json_error(f"Constraint violation: {e.orig.diag.message_primary}", 409)

        if row is None:
            return _json_error("Not found", 404)
        return jsonify(_public(row, hidden))

    @bp.delete(f"/api/{table_name}/<int:row_id>")
    @requires(table_name, "delete")
    def delete_row(row_id):
        if config.get("disable_delete"):
            return _json_error("Delete is disabled for this table", 405)

        with engine.begin() as conn:
            result = conn.execute(
                delete(table).where(table.c.id == row_id).returning(table.c.id)
            )
            deleted = result.first()

        if deleted is None:
            return _json_error("Not found", 404)
        return jsonify({"deleted": True})

    return bp
