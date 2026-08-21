"""
db.py -- builds one shared SQLAlchemy Core engine for the whole app.

We use Core, not the ORM: query building and connection pooling, without
model classes. Table structure itself lives in each module's schema.sql
and generated table_core.py, not here -- this file only knows how to
connect, nothing about what tables exist.
"""

import os
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

# .env sits at the project root, two levels up from this file
# (core/backend/db.py -> core/backend -> core -> project root).
_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
if _ENV_PATH.exists():
    load_dotenv(_ENV_PATH)

_engine: Engine | None = None


def build_database_url() -> str:
    """Assemble a SQLAlchemy connection URL from the same DB_* vars the
    old PHP .env used, so .env.example doesn't need to change shape."""
    host = os.getenv("DB_HOST")
    port = os.getenv("DB_PORT")
    name = os.getenv("DB_NAME")
    user = os.getenv("DB_USER")
    password = os.getenv("DB_PASSWORD", "")
    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{name}"


def get_engine() -> Engine:
    """Singleton engine, same intent as Database::getConnection() in the
    old PHP version -- one shared connection pool for the app's lifetime,
    not a new connection per request."""
    global _engine
    if _engine is None:
        _engine = create_engine(build_database_url(), pool_pre_ping=True)
    return _engine
