#!/usr/bin/env python3
"""
setup.py -- brings this app from freshly-scaffolded to running, in one
command: provision auth settings in .env, build images, sync each
module's schema, seed the admin account, (re)start containers, verify the
app responds.

Works for every app scaffolded from this framework as-is. An app that
needs something beyond this sequence gets its own custom setup script
instead of this one being extended with special cases -- keep this file
generic.

AUTH: when modules/app_user is present, this script also
  - generates SECRET_KEY in .env if it is blank or shorter than 32 chars,
  - appends any missing auth variables (with defaults) to .env,
  - seeds the .env admin account if no admin exists yet.
So no auth setup step is manual.

ORDER MATTERS: schema sync and seeding use `docker compose run --rm`, not
`exec`. A newly added module has no table_core.py until sync runs, and
app.py refuses to boot without it -- so the app container cannot be
running yet at that point. Containers are (re)created last, with
--force-recreate, because compose does not reliably re-read .env changes
into an already-created container.

The app_user TABLE itself must already exist: the app's DB role cannot
CREATE TABLE. Create it via the companion infrastructure repo
(`sudo scripts/setup.sh`, the webapp_postgres Ansible role) first.

USAGE:
    python setup.py
    python setup.py --apply-schema     # also runs each module's schema.sql
    python setup.py --skip-sync        # skip schema sync and seeding
"""

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent

AUTH_MODULE = "app_user"
MIN_SECRET_LENGTH = 32
ENV_LINE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")

# Appended to .env only when the key is entirely absent -- an existing
# value, even a blank one, is never overwritten (SECRET_KEY aside).
AUTH_ENV_DEFAULTS = {
    "SESSION_LIFETIME_MINUTES": "480",
    "SESSION_COOKIE_SECURE": "false",
    "ADMIN_USERNAME": "admin",
    "ADMIN_PASSWORD": "admin",
    "LOGIN_MAX_ATTEMPTS": "5",
    "LOGIN_LOCKOUT_MINUTES": "15",
    "PASSWORD_MIN_LENGTH": "8",
}


# --- segment: config loading ---------------------------------------------

def load_setup_conf() -> dict:
    conf_path = PROJECT_ROOT / "setup.conf"
    if not conf_path.exists():
        print("ERROR: setup.conf not found at project root.", file=sys.stderr)
        sys.exit(1)
    try:
        return json.loads(conf_path.read_text())
    except json.JSONDecodeError as e:
        print(f"ERROR: setup.conf is not valid JSON: {e}", file=sys.stderr)
        sys.exit(1)


def compose_cmd(conf: dict, *args: str) -> list:
    compose_file = PROJECT_ROOT / conf["compose_file"]
    return ["docker", "compose", "-f", str(compose_file), *args]


# --- segment: .env provisioning ---------------------------------------------

def auth_module_present(conf: dict) -> bool:
    return (PROJECT_ROOT / conf["modules_dir"] / AUTH_MODULE).is_dir()


def ensure_auth_env() -> bool:
    """Makes sure .env carries every auth setting. Returns True if the
    file was modified (the containers then need a forced recreate)."""
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        print("ERROR: .env not found. Run `cp .env.example .env` and fill in "
              "the DB credentials first.", file=sys.stderr)
        sys.exit(1)

    lines = env_path.read_text().splitlines()
    line_index = {}
    for i, line in enumerate(lines):
        match = ENV_LINE.match(line)
        if match:
            line_index[match.group(1)] = i

    changed = False

    secret_idx = line_index.get("SECRET_KEY")
    current_secret = lines[secret_idx].split("=", 1)[1].strip() if secret_idx is not None else ""
    if len(current_secret) < MIN_SECRET_LENGTH:
        new_line = f"SECRET_KEY={secrets.token_hex(32)}"
        if secret_idx is None:
            lines.append(new_line)
        else:
            lines[secret_idx] = new_line
        changed = True
        print("Generated SECRET_KEY in .env (was missing or shorter than "
              f"{MIN_SECRET_LENGTH} characters).")

    missing = [(k, v) for k, v in AUTH_ENV_DEFAULTS.items() if k not in line_index]
    if missing:
        lines.append("")
        lines.append("# --- Authentication (added by setup.py) ---")
        for key, value in missing:
            lines.append(f"{key}={value}")
        changed = True
        print(f"Added missing auth settings to .env: {', '.join(k for k, _ in missing)}")

    if changed:
        env_path.write_text("\n".join(lines) + "\n")
        try:
            os.chmod(env_path, 0o600)
        except OSError:
            pass  # e.g. unsupported filesystem; not fatal

    return changed


# --- segment: container lifecycle -----------------------------------------

def build_images(conf: dict):
    print(f"Building images ({conf['compose_file']})...")
    subprocess.run(compose_cmd(conf, "build"), check=True)


def start_containers(conf: dict):
    print("Starting containers (forced recreate so .env changes take effect)...")
    subprocess.run(compose_cmd(conf, "up", "-d", "--force-recreate"), check=True)
    time.sleep(3)  # give gunicorn a moment to bind before the health check


# --- segment: schema sync + seeding ------------------------------------------

def sync_all_modules(conf: dict, apply_schema: bool) -> list:
    """Returns the names of modules whose sync failed."""
    modules_dir = PROJECT_ROOT / conf["modules_dir"]

    module_names = sorted(
        p.name for p in modules_dir.iterdir()
        if p.is_dir() and not p.name.startswith("_") and (p / "schema.sql").exists()
    )

    if not module_names:
        print("No modules with schema.sql found, skipping sync.")
        return []

    failed = []
    for name in module_names:
        cmd = compose_cmd(
            conf, "run", "--rm", "-T", "app",
            "python", "core/backend/sync_tables.py", f"--module={name}",
        )
        if apply_schema:
            cmd.append("--apply-schema")
        print(f"Syncing module: {name}")
        result = subprocess.run(cmd)
        if result.returncode != 0:
            failed.append(name)
            print(f"WARNING: sync failed for module '{name}'. The app may not "
                  f"work until the database is reachable and this is re-run.",
                  file=sys.stderr)
    return failed


def seed_admin(conf: dict) -> bool:
    print("Seeding admin account (only if no admin exists)...")
    result = subprocess.run(compose_cmd(
        conf, "run", "--rm", "-T", "app", "python", "core/backend/seed_admin.py",
    ))
    return result.returncode == 0


# --- segment: verification --------------------------------------------------

def verify_setup(conf: dict):
    url = conf.get("health_check_url")
    if not url:
        print("No health_check_url in setup.conf, skipping verification.")
        return
    try:
        with urllib.request.urlopen(url, timeout=5) as res:
            body = json.loads(res.read())
            print(f"Health check OK: {body}")
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"WARNING: health check failed ({e}). Containers may still be starting -- "
              f"check `docker compose logs` if this persists.", file=sys.stderr)


# --- segment: main -----------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Bring this app up: auth env, images, schema sync, seed, containers, verify."
    )
    parser.add_argument("--apply-schema", action="store_true",
                         help="Apply each module's schema.sql before reflecting it")
    parser.add_argument("--skip-sync", action="store_true",
                         help="Skip schema sync and admin seeding")
    args = parser.parse_args()

    conf = load_setup_conf()
    auth_enabled = auth_module_present(conf)

    if auth_enabled:
        ensure_auth_env()

    build_images(conf)

    if not args.skip_sync:
        failed = sync_all_modules(conf, args.apply_schema)

        if AUTH_MODULE in failed:
            print(f"ERROR: could not reflect the '{AUTH_MODULE}' table, so the app "
                  f"cannot start with authentication. Make sure the table exists "
                  f"(create it through the infrastructure repo's webapp_postgres "
                  f"role: `sudo scripts/setup.sh`) and the database is reachable, "
                  f"then re-run `python setup.py`. Containers were left untouched.",
                  file=sys.stderr)
            sys.exit(1)

        if auth_enabled and not seed_admin(conf):
            print("WARNING: admin seeding failed. Without an admin account nobody "
                  "can log in. Fix the cause and re-run `python setup.py`.",
                  file=sys.stderr)
    else:
        print("Skipping schema sync and admin seeding (--skip-sync).")

    start_containers(conf)
    verify_setup(conf)


if __name__ == "__main__":
    main()
