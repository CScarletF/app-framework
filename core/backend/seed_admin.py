#!/usr/bin/env python3
"""
seed_admin.py -- creates the initial admin account from .env's
ADMIN_USERNAME / ADMIN_PASSWORD, ONLY if no admin exists yet.

Idempotent and safe to re-run: an existing admin is never modified, so
editing .env afterward changes nothing. Run by setup.py after the schema
sync; can also be run by hand inside the app container:

    python core/backend/seed_admin.py

Requires modules/app_user/table_core.py (run sync_tables.py first).
"""

import importlib.util
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from db import get_engine
from auth import seed_admin

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CORE_FILE = PROJECT_ROOT / "modules" / "app_user" / "table_core.py"


def main() -> int:
    if not CORE_FILE.exists():
        print("ERROR: modules/app_user/table_core.py not found. Run "
              "`python core/backend/sync_tables.py --module=app_user` first.",
              file=sys.stderr)
        return 1

    spec = importlib.util.spec_from_file_location("app_user_table_core", CORE_FILE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    try:
        result = seed_admin(get_engine(), mod.table)
    except Exception as e:
        print(f"ERROR: seeding failed: {e}", file=sys.stderr)
        return 1

    if result == "exists":
        print("Admin already exists, nothing seeded.")
        return 0

    username = (os.getenv("ADMIN_USERNAME") or "admin").strip()
    print(f"Seeded admin account '{username}'.")
    if not os.getenv("ADMIN_PASSWORD") or os.getenv("ADMIN_PASSWORD") == "admin":
        print("WARNING: seeded with the default password 'admin'. Change it "
              "inside the app after first login.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
