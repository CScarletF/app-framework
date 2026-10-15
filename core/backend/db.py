"""
db.py -- builds one shared SQLAlchemy Core engine for the whole app.

We use Core, not the ORM: query building and connection pooling, without
model classes. Table structure itself lives in each module's schema.sql
and generated table_core.py, not here -- this file only knows how to
connect, nothing about what tables exist.

Pool sizing matters in this deployment: the app reaches Postgres through
HAProxy's "master" listener, which caps at 50 connections for ALL clients.
Each gunicorn worker is synchronous (one request at a time, so at most one
connection in use), hence the small defaults. Worst case per pod is
workers x (DB_POOL_SIZE + DB_MAX_OVERFLOW); keep replicas x that figure
well under 50. Tunable via .env / the cluster ConfigMap:
    DB_POOL_SIZE         persistent connections per worker (default 3)
    DB_MAX_OVERFLOW      extra short-lived connections per worker (default 2)
    DB_CONNECT_TIMEOUT   seconds to wait when connecting (default 5), so a
                         Patroni failover fails requests fast instead of
                         hanging a worker
"""

import os
from pathlib import Path
from urllib.parse import quote_plus
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

# .env sits at the project root, two levels up from this file
# (core/backend/db.py -> core/backend -> core -> project root). It is
# optional: in a container image there is no .env and the same variables
# arrive as real environment variables instead.
_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
if _ENV_PATH.exists():
    load_dotenv(_ENV_PATH)

_engine: Engine | None = None


def _int_env(name: str, default: int, minimum: int) -> int:
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


def build_database_url() -> str:
    """Assemble a SQLAlchemy connection URL from the same DB_* vars the
    old PHP .env used, so .env.example doesn't need to change shape.
    User and password are URL-quoted so characters such as @ or / in a
    password can't corrupt the URL."""
    host = os.getenv("DB_HOST")
    port = os.getenv("DB_PORT")
    name = os.getenv("DB_NAME")
    user = quote_plus(os.getenv("DB_USER") or "")
    password = quote_plus(os.getenv("DB_PASSWORD", ""))
    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{name}"


def get_engine() -> Engine:
    """Singleton engine, same intent as Database::getConnection() in the
    old PHP version -- one shared connection pool for the app's lifetime,
    not a new connection per request."""
    global _engine
    if _engine is None:
        _engine = create_engine(
            build_database_url(),
            pool_pre_ping=True,
            pool_size=_int_env("DB_POOL_SIZE", 3, 1),
            max_overflow=_int_env("DB_MAX_OVERFLOW", 2, 0),
            connect_args={"connect_timeout": _int_env("DB_CONNECT_TIMEOUT", 5, 1)},
        )
    return _engine
