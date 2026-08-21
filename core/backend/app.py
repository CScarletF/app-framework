"""
app.py -- Flask app factory.

Deliberately no framework-level module registry file to keep in sync --
whatever module folders physically exist under modules/ at runtime ARE
the active module set. A scaffolded app only receives the module folders
it asked for, so "what's in modules/" and "what's active" are always the
same thing by construction. This is what replaces the old tables.php
merge step entirely -- there's nothing to merge, each module is fully
self-contained in its own folder.
"""

import importlib.util
import json
import sys
from pathlib import Path

from flask import Flask, jsonify

from db import get_engine
from crud import build_module_blueprint

# Two levels up from core/backend/ is the project root, where modules/ lives.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODULES_DIR = PROJECT_ROOT / "modules"


def _load_table_object(module_dir: Path):
    """Dynamically imports a module's generated table_core.py and pulls
    out its `table` object, without needing modules/ to be an importable
    Python package. Failing loudly here (rather than skipping a module
    silently) is deliberate -- a missing table_core.py almost always
    means sync_tables.py hasn't been run yet for that module, and a
    silent skip would just turn into a confusing 404 later."""
    core_file = module_dir / "table_core.py"
    if not core_file.exists():
        raise RuntimeError(
            f"{module_dir.name}: table_core.py not found. Run "
            f"`python core/backend/sync_tables.py --module={module_dir.name}` "
            f"first (this generates it from the live database schema)."
        )

    spec = importlib.util.spec_from_file_location(f"{module_dir.name}_table_core", core_file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.table


def create_app() -> Flask:
    app = Flask(__name__)
    engine = get_engine()

    seen_tables = {}  # table_name -> module_name, to catch duplicate-table collisions loudly

    for module_dir in sorted(p for p in MODULES_DIR.iterdir() if p.is_dir()):
        if module_dir.name.startswith("_"):
            continue  # e.g. modules/_template -- a scaffold stub, never a real module
        table_json_path = module_dir / "table.json"
        if not table_json_path.exists():
            continue  # not a module folder (e.g. a stray file), skip

        config = json.loads(table_json_path.read_text())
        table_name = config["table"]

        if table_name in seen_tables:
            raise RuntimeError(
                f"Duplicate table name '{table_name}' claimed by both "
                f"modules/{seen_tables[table_name]} and modules/{module_dir.name}. "
                f"Rename one of them before starting the app."
            )
        seen_tables[table_name] = module_dir.name

        table = _load_table_object(module_dir)
        app.register_blueprint(build_module_blueprint(module_dir.name, table, config, engine))

    @app.get("/api/_health")
    def health():
        # Deliberately does NOT touch the database -- this endpoint is for
        # setup.py's verify step to confirm the app process itself is up,
        # independent of whether the DB/schema sync has happened yet.
        return jsonify({"status": "ok", "modules": list(seen_tables.values())})

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
