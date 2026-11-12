"""
audit.py -- the Auditing module's writer. Framework level: nothing here
knows about any module except the audit_log table itself.

Every mutation in the app goes through record(), called INSIDE the same
transaction as the change it describes, so a change and its log entry
commit or roll back together: there is never a change without an entry
or an entry without a change.

Active only when modules/audit_log is present: app.py calls init_audit,
which finds the table among the loaded ones. Without it record() is a
no-op, so scaffolds that do not ship audit_log keep working.

audit_log stores the username as TEXT (resolved from the session at the
time of the action), not a user_id reference, so a deactivated or renamed
account never corrupts historical readability. Values of hashed fields
are never written: a changed password is logged as "(value hidden)".

audit_log is append-only: its table.json sets "read_only" (crud.py
registers no create/update/delete routes for it), auth.py grants no role
any action on it, and its schema.sql blocks UPDATE, DELETE and TRUNCATE
with triggers. Only record() ever inserts.

Actions: create, update, delete, void, override. Reads and logins are not
logged.
"""

from datetime import datetime
from decimal import Decimal

from flask import g

AUDIT_TABLE = "audit_log"
DETAIL_MAX = 500
VALUE_MAX = 80

# Columns the system maintains itself. They change on every write, so
# listing them in a diff would only add noise.
SYSTEM_COLUMNS = {"id", "created_at", "updated_at", "created_by", "updated_by"}

_state = {"table": None}


def init_audit(tables: dict) -> bool:
    """Finds the audit_log table among the loaded ones. Returns False
    (and record() stays a no-op) when the module is not present."""
    _state["table"] = tables.get(AUDIT_TABLE)
    return _state["table"] is not None


def audit_enabled() -> bool:
    return _state["table"] is not None


def _username() -> str:
    user = getattr(g, "user", None)
    return user["username"] if user else "anonymous"


def _fmt(value) -> str:
    if value is None or value == "":
        return "(empty)"
    if isinstance(value, Decimal):
        text = format(value.normalize(), "f")
    elif isinstance(value, datetime):
        text = value.isoformat(sep=" ", timespec="minutes")
    else:
        text = str(value)
    return text if len(text) <= VALUE_MAX else text[: VALUE_MAX - 3] + "..."


def describe_changes(before: dict, after: dict, hidden: set) -> str:
    """Summary of what an UPDATE changed, e.g. "changed price 8000 -> 9000".
    `before` and `after` are both rows as read from the database, so
    values compare in the same types. Hashed columns report only that they
    changed."""
    parts = []
    for key in after.keys():
        if key in SYSTEM_COLUMNS:
            continue
        old, new = before.get(key), after[key]
        if old == new:
            continue
        if key in hidden:
            parts.append(f"{key} (value hidden)")
        else:
            parts.append(f"{key} {_fmt(old)} -> {_fmt(new)}")
    return ("changed " + "; ".join(parts)) if parts else "no field changes"


def record(conn, action: str, table_name: str, row_id, detail: str | None = None):
    """Appends one audit_log row on `conn`, the caller's open transaction.
    Does nothing when the audit_log module is not present."""
    table = _state["table"]
    if table is None:
        return
    conn.execute(
        table.insert().values(
            username=_username(),
            action=action,
            table_name=table_name,
            row_id=row_id,
            detail=detail[:DETAIL_MAX] if detail else None,
        )
    )
