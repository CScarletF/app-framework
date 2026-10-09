#!/usr/bin/env python3
"""
scaffold.py -- builds a new app repo from this framework's core + a
chosen set of modules.

No merge step: each selected module folder is copied in as-is, and
app.py discovers whatever's physically present under modules/ at
runtime. Table-name collisions are still caught, but at app startup
(app.py), not here -- scaffold.py's only job is copying files.

AUTH: authentication is active only if the app_user module is among the
selected modules. Leaving it out is allowed (the app then runs with NO
login at all), so scaffold.py prints a warning instead of refusing.

USAGE:
    python scaffold.py --out=../equipment-tracker
        (no --modules given -> reads default.conf)

    python scaffold.py --modules=app_user,equipment,inventory --out=../logistics-b
        (explicit list REPLACES default.conf entirely, no merging)
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
MODULES_DIR = PROJECT_ROOT / "modules"
CORE_DIR = PROJECT_ROOT / "core"

AUTH_MODULE = "app_user"

ROOT_FILES_TO_COPY = [
    "requirements.txt", ".env.example", ".gitignore",
    "setup.conf", "setup.py", "permissions.conf",
]


def resolve_module_list(explicit: str | None) -> list[str]:
    if explicit:
        return [m.strip() for m in explicit.split(",") if m.strip()]

    default_conf_path = PROJECT_ROOT / "default.conf"
    if not default_conf_path.exists():
        print("ERROR: no --modules given and default.conf not found.", file=sys.stderr)
        sys.exit(1)

    try:
        conf = json.loads(default_conf_path.read_text())
    except json.JSONDecodeError as e:
        print(f"ERROR: default.conf is not valid JSON: {e}", file=sys.stderr)
        sys.exit(1)

    return conf.get("modules", [])


def validate_modules(names: list[str]):
    missing = [n for n in names if not (MODULES_DIR / n).is_dir()]
    if missing:
        print(f"ERROR: unknown module(s), no matching folder under modules/: {', '.join(missing)}",
              file=sys.stderr)
        sys.exit(1)

    stubs = [n for n in names if n.startswith("_")]
    if stubs:
        print(f"ERROR: {', '.join(stubs)} is a stub folder (e.g. _template), not a real module.",
              file=sys.stderr)
        sys.exit(1)


def copy_core(out_dir: Path):
    shutil.copytree(CORE_DIR, out_dir / "core")


def copy_modules(names: list[str], out_dir: Path):
    dest_modules = out_dir / "modules"
    dest_modules.mkdir(parents=True, exist_ok=True)
    for name in names:
        shutil.copytree(MODULES_DIR / name, dest_modules / name)


def write_frontend_manifest(names: list[str], out_dir: Path):
    manifest_path = out_dir / "core" / "frontend" / "modules.json"
    manifest_path.write_text(json.dumps({"modules": names}, indent=4) + "\n")


def copy_root_files(out_dir: Path):
    for filename in ROOT_FILES_TO_COPY:
        src = PROJECT_ROOT / filename
        if src.exists():
            shutil.copy2(src, out_dir / filename)


def main():
    parser = argparse.ArgumentParser(description="Scaffold a new app from this framework.")
    parser.add_argument("--modules", help="Comma-separated module names. Omit to use default.conf.")
    parser.add_argument("--out", required=True, help="Output directory for the new app (must not exist)")
    args = parser.parse_args()

    out_dir = Path(args.out).resolve()
    if out_dir.exists():
        print(f"ERROR: output directory already exists: {out_dir}", file=sys.stderr)
        sys.exit(1)

    module_names = resolve_module_list(args.modules)
    if not module_names:
        print("ERROR: no modules selected (empty list).", file=sys.stderr)
        sys.exit(1)
    validate_modules(module_names)

    out_dir.mkdir(parents=True)
    copy_core(out_dir)
    copy_modules(module_names, out_dir)
    write_frontend_manifest(module_names, out_dir)
    copy_root_files(out_dir)

    print(f"Scaffolded app at {out_dir} with modules: {', '.join(module_names)}")
    if AUTH_MODULE not in module_names:
        print(f"\nWARNING: '{AUTH_MODULE}' was not selected. This app will run WITHOUT "
              f"authentication: every request, including writes and deletes, will be open. "
              f"Re-scaffold with '{AUTH_MODULE}' in --modules to enable login.", file=sys.stderr)
    print("\nNext steps:")
    print(f"  cd {out_dir}")
    print("  cp .env.example .env   # then fill in real DB credentials")
    if AUTH_MODULE in module_names:
        print("  # create the app_user table first (infrastructure repo: sudo scripts/setup.sh)")
    print("  python setup.py         # auth env, containers, schema sync, admin seed, verify")


if __name__ == "__main__":
    main()
