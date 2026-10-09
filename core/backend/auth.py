"""
auth.py -- authentication, authorization, and admin seeding. Framework
level: nothing here knows about any module except the auth table itself.

Enabled only when modules/app_user is present (app.py calls init_auth,
which returns False and does nothing otherwise), so scaffolds that don't
ship app_user keep working unauthenticated.

MODEL
- Reads (GET/HEAD/OPTIONS) never require login.
- Every mutating request passes one before_request hook (_build_enforcer):
    1. CSRF header check (custom header; cross-site requests can't set it
       without a CORS preflight, which this app never grants).
    2. Login/logout are exempt from steps 3-5.
    3. Session user is re-read from the DB on EVERY mutating request, so
       deactivation, role changes, and password changes take effect
       immediately instead of when the cookie expires.
    4. The view function's declared permission is checked against the
       user's role. A mutating route with NO declared permission is
       admin-only (fail closed), so a new routes.py endpoint can never
       ship open by accident.
- Roles: admin, staff. Actions: create, update, delete, void, override.
  Generic CRUD maps POST/PUT/DELETE to create/update/delete; custom routes
  declare their action with @requires(table, action).

PERMISSION LAYERS (consulted per role, first layer naming that role wins)
    permissions.conf  modules.<table>   per-deployment override
    table.json        "permissions"     module default
    permissions.conf  default           framework-wide default
    HARD_DEFAULT                        admin everything, staff nothing
Hard invariants on the auth table regardless of config: staff have no
actions on it, admin always keeps create and update.
"""

import hashlib
import hmac
import json
import os
from datetime import timedelta
from pathlib import Path

from flask import Blueprint, current_app, g, jsonify, request, session
from sqlalchemy import Table, case, func, insert, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

AUTH_TABLE = "app_user"
ROLES = ("admin", "staff")
ACTIONS = ("create", "update", "delete", "void", "override")
HARD_DEFAULT = {"admin": list(ACTIONS), "staff": []}

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
CSRF_HEADER = "X-Requested-With"
CSRF_VALUE = "app-framework"
EXEMPT_ENDPOINTS = {"auth_routes.login", "auth_routes.logout"}
AUTHENTICATED = ("*", "authenticated")  # any logged-in user, any role

MIN_SECRET_LENGTH = 32
MAX_PASSWORD_LENGTH = 1024
MAX_USERNAME_LENGTH = 200

INVALID_CREDENTIALS = "Invalid username or password"
LOCKED_MESSAGE = "Too many failed attempts. Account temporarily locked, try again later."

# Verified against when the username doesn't exist, so "unknown user" and
# "wrong password" cost the same time.
_DUMMY_HASH = generate_password_hash("not-a-real-password")

_state = {
    "enabled": False,
    "secret": "",
    "permissions": {},
    "max_attempts": 5,
    "lock_minutes": 15,
    "min_password": 8,
}


# --- segment: small helpers ----------------------------------------------

def _json_error(message: str, code: int):
    response = jsonify({"error": message})
    response.status_code = code
    return response


def _int_env(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        raise RuntimeError(f"{name} must be an integer, got '{raw}'")
    if value < minimum:
        raise RuntimeError(f"{name} must be >= {minimum}, got {value}")
    return value


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _verify(stored_hash: str, plain: str) -> bool:
    try:
        return check_password_hash(stored_hash, plain)
    except ValueError:
        return False  # malformed/unsupported stored hash -> never authenticates


def hash_password(plain) -> str:
    """Policy-enforcing hash. Used by crud.py for every hashed_fields
    column and by the change-password route. Raises ValueError (crud maps
    it to 422). The seed account deliberately bypasses this policy."""
    if not isinstance(plain, str):
        raise ValueError("Password must be a string")
    if len(plain) < _state["min_password"]:
        raise ValueError(f"Password must be at least {_state['min_password']} characters")
    if len(plain) > MAX_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at most {MAX_PASSWORD_LENGTH} characters")
    return generate_password_hash(plain)


def _fingerprint(password_hash: str) -> str:
    """Keyed fingerprint of the stored hash, kept in the session cookie.
    A password change alters the hash, so every other live session for
    that user stops matching and is rejected."""
    return hmac.new(
        _state["secret"].encode(), password_hash.encode(), hashlib.sha256
    ).hexdigest()[:32]


# --- segment: public helpers for crud.py and routes.py ---------------------

def auth_enabled() -> bool:
    return _state["enabled"]


def current_user():
    return getattr(g, "user", None)


def current_user_id():
    user = current_user()
    return user["id"] if user else None


def audit_values(table: Table, creating: bool) -> dict:
    """created_by/updated_by values for the session user, limited to the
    columns that actually exist on `table`. Empty when auth is disabled or
    there is no user in the request, and harmless on a table that has not
    gained the audit columns yet. Used by crud.py and by custom routes."""
    user_id = current_user_id()
    if user_id is None:
        return {}
    names = table.c.keys()
    values = {}
    if "updated_by" in names:
        values["updated_by"] = user_id
    if creating and "created_by" in names:
        values["created_by"] = user_id
    return values


def can(role: str, table_name: str, action: str) -> bool:
    role_sets = _state["permissions"].get(table_name)
    if role_sets is None:
        return role == "admin"  # unknown table -> admin only
    return action in role_sets.get(role, set())


def can_current(table_name: str, action: str) -> bool:
    """For checks inside a handler (e.g. checkout's per-item override).
    True when auth is disabled, since there is then no notion of a user."""
    if not _state["enabled"]:
        return True
    user = current_user()
    return user is not None and can(user["role"], table_name, action)


def requires(table_name: str, action: str):
    """Declares the permission a mutating view needs. Apply it directly
    under the route decorator:
        @bp.post("/api/x")
        @requires("x", "create")
        def handler(): ...
    The hook reads this marker; it does not wrap the function."""
    def mark(fn):
        fn._required_permission = (table_name, action)
        return fn
    return mark


def authenticated_only(fn):
    fn._required_permission = AUTHENTICATED
    return fn


def _permissions_for_role(role: str) -> dict:
    return {
        table_name: sorted(role_sets.get(role, set()))
        for table_name, role_sets in _state["permissions"].items()
    }


# --- segment: permission loading --------------------------------------------

def _validate_role_map(role_map, source: str):
    if not isinstance(role_map, dict):
        raise RuntimeError(f"{source}: must be an object mapping role -> list of actions")
    for role, actions in role_map.items():
        if role not in ROLES:
            raise RuntimeError(f"{source}: unknown role '{role}' (allowed: {', '.join(ROLES)})")
        if not isinstance(actions, list) or any(a not in ACTIONS for a in actions):
            raise RuntimeError(
                f"{source}: actions for '{role}' must be a list drawn from {', '.join(ACTIONS)}"
            )


def _load_permission_conf(project_root: Path):
    path = project_root / "permissions.conf"
    if not path.exists():
        return {}, {}
    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise RuntimeError(f"permissions.conf is not valid JSON: {e}")
    if not isinstance(raw, dict):
        raise RuntimeError("permissions.conf: top level must be an object")

    conf_default = raw.get("default", {})
    conf_modules = raw.get("modules", {})
    _validate_role_map(conf_default, "permissions.conf: default")
    if not isinstance(conf_modules, dict):
        raise RuntimeError("permissions.conf: 'modules' must be an object")
    for name, role_map in conf_modules.items():
        _validate_role_map(role_map, f"permissions.conf: modules.{name}")
    return conf_default, conf_modules


def _build_permissions(table_configs: dict, project_root: Path) -> dict:
    conf_default, conf_modules = _load_permission_conf(project_root)

    # A typo'd table name would silently fall through to the default
    # grant, so fail loudly instead.
    unknown = [name for name in conf_modules if name not in table_configs]
    if unknown:
        raise RuntimeError(
            f"permissions.conf: unknown table(s) in 'modules': {', '.join(unknown)}"
        )

    result = {}
    for table_name, config in table_configs.items():
        json_layer = config.get("permissions")
        if json_layer is not None:
            _validate_role_map(json_layer, f"modules/{table_name}/table.json: permissions")

        layers = [conf_modules.get(table_name), json_layer, conf_default, HARD_DEFAULT]
        role_sets = {}
        for role in ROLES:
            for layer in layers:
                if layer is not None and role in layer:
                    role_sets[role] = set(layer[role])
                    break

        if config.get("disable_delete"):
            for actions in role_sets.values():
                actions.discard("delete")

        if table_name == AUTH_TABLE:
            role_sets["staff"] = set()
            role_sets["admin"] |= {"create", "update"}

        result[table_name] = role_sets
    return result


# --- segment: session user + enforcement hook -----------------------------------

def _load_session_user(engine: Engine, user_table: Table):
    uid = session.get("uid")
    if uid is None:
        return None
    with engine.connect() as conn:
        row = conn.execute(
            select(
                user_table.c.id,
                user_table.c.username,
                user_table.c.role,
                user_table.c.active,
                user_table.c.password_hash,
            ).where(user_table.c.id == uid)
        ).mappings().first()

    if row is None or not row["active"]:
        session.clear()
        return None
    if not hmac.compare_digest(session.get("pwf", ""), _fingerprint(row["password_hash"])):
        session.clear()
        return None
    return {"id": row["id"], "username": row["username"], "role": row["role"]}


def _build_enforcer(engine: Engine, user_table: Table):
    def enforce():
        # endpoint is None for unmatched routes/methods; Flask answers
        # those with 404/405 and nothing is mutated.
        if request.method in SAFE_METHODS or request.endpoint is None:
            return None

        if request.headers.get(CSRF_HEADER) != CSRF_VALUE:
            return _json_error("Missing or invalid request header", 403)

        if request.endpoint in EXEMPT_ENDPOINTS:
            return None

        user = _load_session_user(engine, user_table)
        if user is None:
            return _json_error("Login required", 401)
        g.user = user

        view = current_app.view_functions.get(request.endpoint)
        required = getattr(view, "_required_permission", None)

        if required == AUTHENTICATED:
            return None
        if required is None:
            allowed = user["role"] == "admin"  # undeclared -> fail closed
        else:
            allowed = can(user["role"], required[0], required[1])
        if not allowed:
            return _json_error("Forbidden", 403)
        return None

    return enforce


# --- segment: auth routes ---------------------------------------------------------

def _build_blueprint(engine: Engine, user_table: Table) -> Blueprint:
    bp = Blueprint("auth_routes", __name__)
    t = user_table

    def register_failure(user_id: int):
        # Single atomic UPDATE: right-hand sides see the OLD count, so a
        # lock engages on the Nth failure and the counter restarts at 0
        # (a fresh set of attempts once the lock expires).
        reaches_limit = t.c.failed_login_count + 1 >= _state["max_attempts"]
        with engine.begin() as conn:
            conn.execute(
                update(t)
                .where(t.c.id == user_id)
                .values(
                    failed_login_count=case(
                        (reaches_limit, 0), else_=t.c.failed_login_count + 1
                    ),
                    locked_until=case(
                        (reaches_limit, func.now() + timedelta(minutes=_state["lock_minutes"])),
                        else_=t.c.locked_until,
                    ),
                )
            )

    @bp.post("/api/auth/login")
    def login():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return _json_error("Invalid JSON", 400)

        username = payload.get("username")
        password = payload.get("password")
        if (not isinstance(username, str) or not isinstance(password, str)
                or not username.strip() or not password):
            return _json_error("username and password are required", 422)
        username = username.strip()
        if len(username) > MAX_USERNAME_LENGTH or len(password) > MAX_PASSWORD_LENGTH:
            return _json_error(INVALID_CREDENTIALS, 401)

        with engine.connect() as conn:
            row = conn.execute(
                select(
                    t.c.id,
                    t.c.password_hash,
                    t.c.role,
                    t.c.active,
                    t.c.failed_login_count,
                    t.c.locked_until,
                    (t.c.locked_until > func.now()).label("locked"),
                ).where(t.c.username == username)
            ).mappings().first()

        if row is None:
            _verify(_DUMMY_HASH, password)
            return _json_error(INVALID_CREDENTIALS, 401)

        # Usernames are already readable via GET /api/app_user (reads are
        # open by design), so reporting the lock does not leak anything new.
        if row["locked"]:
            return _json_error(LOCKED_MESSAGE, 429)

        if not _verify(row["password_hash"], password):
            register_failure(row["id"])
            return _json_error(INVALID_CREDENTIALS, 401)

        if not row["active"]:
            return _json_error(INVALID_CREDENTIALS, 401)

        if row["failed_login_count"] > 0 or row["locked_until"] is not None:
            with engine.begin() as conn:
                conn.execute(
                    update(t).where(t.c.id == row["id"])
                    .values(failed_login_count=0, locked_until=None)
                )

        session.clear()  # drops any pre-login session state
        session.permanent = True
        session["uid"] = row["id"]
        session["pwf"] = _fingerprint(row["password_hash"])
        return jsonify({"username": username, "role": row["role"]})

    @bp.post("/api/auth/logout")
    def logout():
        session.clear()
        return jsonify({"ok": True})

    @bp.get("/api/auth/me")
    def me():
        user = _load_session_user(engine, t)
        if user is None:
            return jsonify({"authenticated": False})
        return jsonify({
            "authenticated": True,
            "username": user["username"],
            "role": user["role"],
            "permissions": _permissions_for_role(user["role"]),
        })

    @bp.post("/api/auth/password")
    @authenticated_only
    def change_password():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return _json_error("Invalid JSON", 400)

        current = payload.get("current_password")
        new = payload.get("new_password")
        if not isinstance(current, str) or not isinstance(new, str) or not current or not new:
            return _json_error("current_password and new_password are required", 422)

        user = g.user
        with engine.connect() as conn:
            stored = conn.execute(
                select(t.c.password_hash).where(t.c.id == user["id"])
            ).scalar_one()

        # 403, not 401: the frontend treats 401 as "session gone" and
        # opens the login view, which is wrong for a mistyped password.
        if not _verify(stored, current):
            register_failure(user["id"])
            return _json_error("Current password is incorrect", 403)
        if hmac.compare_digest(current, new):
            return _json_error("New password must differ from the current one", 422)

        try:
            new_hash = hash_password(new)
        except ValueError as e:
            return _json_error(str(e), 422)

        with engine.begin() as conn:
            conn.execute(
                update(t).where(t.c.id == user["id"])
                .values(password_hash=new_hash, updated_by=user["id"])
            )
        session["pwf"] = _fingerprint(new_hash)  # keep THIS session alive
        return jsonify({"ok": True})

    return bp


# --- segment: seeding ----------------------------------------------------------------

def seed_admin(engine: Engine, user_table: Table) -> str:
    """Creates the .env-defined admin only if NO admin exists yet.
    Returns 'created' or 'exists'. Never touches an existing account, so
    changing .env afterward does not change any password. Safe against
    concurrent runs: the UNIQUE(username) constraint makes the loser a
    no-op."""
    username = (os.getenv("ADMIN_USERNAME") or "admin").strip()
    password = os.getenv("ADMIN_PASSWORD") or "admin"

    with engine.begin() as conn:
        existing = conn.execute(
            select(user_table.c.id).where(user_table.c.role == "admin").limit(1)
        ).first()
        if existing is not None:
            return "exists"
        try:
            conn.execute(
                insert(user_table).values(
                    username=username,
                    password_hash=generate_password_hash(password),
                    role="admin",
                    active=True,
                )
            )
        except IntegrityError:
            return "exists"
    return "created"


# --- segment: entry point --------------------------------------------------------------

def init_auth(app, engine: Engine, tables: dict, table_configs: dict, project_root: Path) -> bool:
    """Wires session config, permissions, the auth blueprint, and the
    enforcement hook. Returns False (and does nothing) when the app_user
    module isn't present."""
    user_table = tables.get(AUTH_TABLE)
    if user_table is None:
        return False

    secret = os.getenv("SECRET_KEY", "")
    if len(secret) < MIN_SECRET_LENGTH:
        raise RuntimeError(
            f"SECRET_KEY must be set in .env and be at least {MIN_SECRET_LENGTH} characters. "
            f"Run `python setup.py` to generate one."
        )

    _state["secret"] = secret
    _state["max_attempts"] = _int_env("LOGIN_MAX_ATTEMPTS", 5)
    _state["lock_minutes"] = _int_env("LOGIN_LOCKOUT_MINUTES", 15)
    _state["min_password"] = _int_env("PASSWORD_MIN_LENGTH", 8)

    app.config.update(
        SECRET_KEY=secret,
        SESSION_COOKIE_NAME="app_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=_bool_env("SESSION_COOKIE_SECURE", False),
        PERMANENT_SESSION_LIFETIME=timedelta(minutes=_int_env("SESSION_LIFETIME_MINUTES", 480)),
    )

    _state["permissions"] = _build_permissions(table_configs, project_root)
    app.register_blueprint(_build_blueprint(engine, user_table))
    app.before_request(_build_enforcer(engine, user_table))
    _state["enabled"] = True
    return True
