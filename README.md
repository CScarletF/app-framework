# app-framework

Reusable base for internal logistics/business apps. This repo is
**framework and module catalog only** -- actual products (e.g. an
equipment tracker) live in their own repos, built by `scaffold.py`.

## Structure

```
core/                   never forked or copied per-app
  backend/               Flask app.py, db.py, crud.py, sync_tables.py
  frontend/               generic HTML/CSS/JS engine, no per-app logic
  docker/                 Dockerfile, nginx conf, docker-compose.yml

modules/                one folder per module, pick-and-choose per app
  equipment/
    table.json            backend validation config (required fields, allowed values)
    schema.sql             source of truth for table structure
    table_core.py           AUTO-GENERATED -- do not hand-edit, see below
    frontend/module.json     nav placement + list/form config
    frontend/module.css      optional, only if the module needs custom styling
  _template/              copy this to start a new module

default.conf             which modules a fresh scaffold gets if --modules isn't given
setup.conf                paths/locations setup.py needs (not secrets -- those are in .env)
scaffold.py                builds a new app repo from core/ + selected modules
setup.py                   brings a scaffolded app from copied-files to running
```

## Building a new app

```
python scaffold.py --modules=equipment --out=../equipment-tracker
cd ../equipment-tracker
cp .env.example .env    # fill in real DB credentials
python setup.py --apply-schema
```

`setup.py` builds and starts the Docker containers, applies + reflects
each module's schema into a generated `table_core.py`, and verifies the
app responds. Apps that need something beyond this get their own custom
setup script -- `setup.py` itself stays generic.

## Adding a module

1. Copy `modules/_template/` to `modules/<name>/`.
2. Fill in `table.json`, `schema.sql`, `frontend/module.json`.
3. Run `python core/backend/sync_tables.py --module=<name> --apply-schema`
   against a real Postgres instance to generate `table_core.py`.
4. Add `<name>` to `default.conf` if it should ship by default, or pass
   `--modules=` explicitly when scaffolding.

A module is CRUD-only config -- no custom Python or JS needed unless it
requires real business logic beyond list/create/update/delete, in which
case that's a deliberate, separate addition to that module's folder.

## Key design decisions (do not re-litigate without reason)

- **Copy-based, not linked.** Scaffolding copies files; it does not use
  git submodules or a package dependency. A `core/` bugfix does not
  auto-propagate to already-scaffolded apps -- updates are deliberate
  and per-app, on purpose.
- **No merge step.** Whatever module folders physically exist under
  `modules/` at runtime are the active set. `app.py` discovers them by
  scanning the directory, not from a generated combined config.
- **`table_core.py` is generated, never hand-written.** `schema.sql` is
  the single source of truth for table structure; `sync_tables.py`
  reflects it from a live database into the generated file.
