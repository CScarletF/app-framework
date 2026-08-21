#!/usr/bin/env python3
"""
setup.py -- brings this app from freshly-scaffolded to running, in one
command: build + start containers, sync each module's schema, verify
the app responds.

Works for every app scaffolded from this framework as-is. An app that
needs something beyond this sequence gets its own custom setup script
instead of this one being extended with special cases -- keep this file
generic.

USAGE:
    python setup.py
    python setup.py --apply-schema     # also runs each module's schema.sql
    python setup.py --skip-sync        # bring up containers only, skip schema sync
"""

import argparse
import json
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent


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


# --- segment: container lifecycle -----------------------------------------

def build_and_start_containers(conf: dict):
    compose_file = PROJECT_ROOT / conf["compose_file"]
    print(f"Building and starting containers ({compose_file})...")
    subprocess.run(["docker", "compose", "-f", str(compose_file), "build"], check=True)
    subprocess.run(["docker", "compose", "-f", str(compose_file), "up", "-d"], check=True)
    time.sleep(3)  # give gunicorn a moment to bind before the health check


# --- segment: schema sync --------------------------------------------------

def sync_all_modules(conf: dict, apply_schema: bool):
    modules_dir = PROJECT_ROOT / conf["modules_dir"]
    sync_script = PROJECT_ROOT / conf["core_dir"] / "backend" / "sync_tables.py"

    module_names = [p.name for p in modules_dir.iterdir()
                     if p.is_dir() and (p / "schema.sql").exists()]

    if not module_names:
        print("No modules with schema.sql found, skipping sync.")
        return

    for name in module_names:
        cmd = [sys.executable, str(sync_script), f"--module={name}"]
        if apply_schema:
            cmd.append("--apply-schema")
        print(f"Syncing module: {name}")
        result = subprocess.run(cmd)
        if result.returncode != 0:
            print(f"WARNING: sync failed for module '{name}'. The app may not "
                  f"work until the database is reachable and this is re-run.",
                  file=sys.stderr)


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
    parser = argparse.ArgumentParser(description="Bring this app up: containers, schema sync, verify.")
    parser.add_argument("--apply-schema", action="store_true",
                         help="Apply each module's schema.sql before reflecting it")
    parser.add_argument("--skip-sync", action="store_true", help="Skip the schema sync step entirely")
    args = parser.parse_args()

    conf = load_setup_conf()

    build_and_start_containers(conf)

    if not args.skip_sync:
        sync_all_modules(conf, args.apply_schema)
    else:
        print("Skipping schema sync (--skip-sync).")

    verify_setup(conf)


if __name__ == "__main__":
    main()
