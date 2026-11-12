# app-framework

Reusable base for internal logistics/business apps. This repo is
**framework and module catalog only** -- actual products (e.g. an
equipment tracker, or a store point-of-sale build) live in their own
repos, built by `scaffold.py`.

## Structure

```
core/                   never forked or copied per-app
  backend/               Flask app.py, db.py, crud.py, auth.py, audit.py,
                         export.py, sync_tables.py, seed_admin.py
  frontend/               generic HTML/CSS/JS engine, no per-app logic
  docker/                 Dockerfile, nginx conf, docker-compose.yml

modules/                one folder per module, pick-and-choose per app
  equipment/
    table.json            backend validation config (required fields, allowed values)
    schema.sql             source of truth for table structure
    table_core.py           AUTO-GENERATED -- do not hand-edit, see below
    frontend/module.json     nav placement + list/form config
    frontend/module.css      optional, only if the module needs custom styling
  app_user/               login accounts; its presence turns authentication on
  audit_log/              the Auditing module: append-only record of every mutation
  _template/              copy this to start a new module

default.conf             which modules a fresh scaffold gets if --modules isn't given
setup.conf                paths/locations setup.py needs (not secrets -- those are in .env)
permissions.conf          per-deployment role permissions (see Authentication)
scaffold.py                builds a new app repo from core/ + selected modules
setup.py                   brings a scaffolded app from copied-files to running
```

## Building a new app

```
python scaffold.py --modules=app_user,audit_log,equipment --out=../equipment-tracker
cd ../equipment-tracker
cp .env.example .env    # fill in real DB credentials only
# create the module tables (see "Database prerequisites" below)
python setup.py
```

`setup.py` is the single bring-up command. In order it:

1. If `modules/app_user` is present: generates `SECRET_KEY` in `.env` when
   blank or shorter than 32 characters, and appends any missing auth
   variables with defaults. Nothing about auth is edited by hand.
2. Builds the Docker images.
3. Runs `sync_tables.py` for every module (reflects the live table into
   the generated `table_core.py`). Uses `docker compose run --rm`, not
   `exec`: a newly added module has no `table_core.py` yet and `app.py`
   refuses to boot without it, so the app container cannot be running at
   this point.
4. Aborts without touching containers if the `app_user` table cannot be
   reflected.
5. Seeds the admin account from `.env` if no admin exists.
6. Recreates the containers (`up -d --force-recreate`) so `.env` changes
   take effect, then runs the health check.

Flags: `--apply-schema` also applies each `schema.sql` before reflecting
(works only if the DB role may `CREATE TABLE`), `--skip-sync` skips schema
sync and seeding.

Apps that need something beyond this get their own custom setup script --
`setup.py` itself stays generic.

If the image build fails with `401 Unauthorized` from `auth.docker.io`, the
host has a stale Docker Hub credential. The base image is public:
`docker logout` and re-run. To skip the rebuild entirely, the code is
bind-mounted, so regenerating one module's table file is enough:
`docker compose -f core/docker/docker-compose.yml run --rm app python
core/backend/sync_tables.py --module=<name>`, then recreate the containers.

### Database prerequisites

In the HA deployment the app's DB role (`webapp_app`) is deliberately
unable to create tables. Tables are created, and `webapp_app` granted
scoped privileges on them, by the companion infrastructure repo
(`autobase-postgresql`): `sudo scripts/setup.sh` runs the
`webapp_postgres` role, which discovers every `modules/<name>/schema.sql`
and needs no change when a module is added. Run it before `python
setup.py` whenever a module is added. The role reads the module list from
the app checkout on the app host, so the module folder must be copied
there first. Changes to an EXISTING table are never applied by that role --
they need a one-off migration script (see `drop_stock_check.sql` in the
infrastructure repo for the pattern). The audit columns on the six original
tables were added this way (`add_audit_columns.sql`, run by
`scripts/migrate_audit_columns.sh`), and so were the void columns on `sale`
(`add_void_columns.sql`, run by `scripts/migrate_void_columns.sh`). Run a
migration BEFORE `python setup.py`, since `setup.py` reflects the live table.

## Authentication

Authentication is enabled when `modules/app_user` is present, and
`scaffold.py` refuses to scaffold an app without it: every module's table
carries `created_by`/`updated_by` columns referencing `app_user`, so the
tables could not be created otherwise. (`app.py` itself still runs
unauthenticated if the module is absent, for hand-assembled setups.)

### Model

```
request
  -> GET/HEAD/OPTIONS                      -> allowed, no login (reads are open)
  -> mutating (POST/PUT/DELETE)
       -> X-Requested-With header missing  -> 403
       -> login or logout endpoint         -> allowed
       -> no valid session                 -> 401
       -> user re-read from the DB         -> missing/inactive/password changed -> 401
       -> permission declared by the view  -> role lacks it -> 403
       -> view has no declared permission  -> admin only (fail closed)
       -> handler
```

All of this lives in one `before_request` hook (`core/backend/auth.py`).
Handlers only declare what they need (`@requires(table, action)`); they
never check roles themselves.

### Roles and actions

Two roles, fixed: `admin` and `staff`. Actions: `create`, `update`,
`delete`, `void`, `override`.

| Request | Action |
|---|---|
| `POST /api/<table>` | create |
| `PUT /api/<table>/<id>` | update |
| `DELETE /api/<table>/<id>` | delete |
| `POST /api/sale/checkout` | create on `sale` |
| `POST /api/sale/<id>/void` | void on `sale` |
| `DELETE /api/sale/<id>/remove` | delete on `sale` (voided sales only) |
| checkout item with `"override": true` | override on `sale` (checked in addition to create) |

Default policy: admin has every action, staff has `create` only. This
simulates only senior staff holding authority to edit, delete or void.
Consequences worth knowing: staff cannot release an assignment (setting
`released_at` is an update), cannot edit products or recipes (a recipe
edit is delete plus create), cannot void or delete a sale, and cannot
override stock at checkout.

### Permission layers

Per role, the first layer that names the role wins:

```
1. permissions.conf  "modules".<table>    per-deployment override
2. table.json        "permissions"        module default
3. permissions.conf  "default"            framework-wide default
4. built-in                               admin everything, staff nothing
```

`permissions.conf` example -- let staff void sales on this deployment:

```json
{
    "default": {
        "admin": ["create", "update", "delete", "void", "override"],
        "staff": ["create"]
    },
    "modules": {
        "sale": { "staff": ["create", "void"] }
    }
}
```

The file is read once at startup; restart the app after editing. A
typo'd table name, role or action stops startup with an explicit error
rather than silently falling back to the default grant. Permissions are
not secrets, so the file is committed; protection comes from server-side
enforcement and from the app exposing no API that can write it.

Invariants regardless of configuration:

- `app_user`: staff have no actions, admin always keeps create and update,
  and delete is disabled for everyone (users are deactivated, never
  deleted, so attribution columns stay valid).
- `audit_log`: no role has any action on it. It is written only by server
  code (see Auditing).

### Sessions

- Flask signed-cookie session, no extra dependency. Cookie: `app_session`,
  HttpOnly, SameSite=Lax. `Secure` is off by default because the app is
  served over plain HTTP on a LAN; set `SESSION_COOKIE_SECURE=true` behind
  HTTPS.
- Sliding expiry: the lifetime restarts on every request
  (`SESSION_LIFETIME_MINUTES`).
- `SECRET_KEY` must be set (minimum 32 characters) and identical across
  gunicorn workers, which is why it lives in `.env`.
- The user is re-read from the database on every mutating request, so
  deactivation and role changes apply immediately. The cookie also carries
  a keyed fingerprint of the password hash: changing a password logs out
  every other session of that user.
- CSRF: every mutating request must carry `X-Requested-With:
  app-framework`. A cross-site page cannot set a custom header without a
  CORS preflight, which this app never grants. `api.js` adds it
  automatically; any other client (curl, scripts) must send it.

### Login protection and passwords

- Lockout after `LOGIN_MAX_ATTEMPTS` consecutive failures (default 5) for
  `LOGIN_LOCKOUT_MINUTES` (default 15); a locked login returns 429. State
  is stored in `app_user` (`failed_login_count`, `locked_until`), not in
  memory, because the app runs several gunicorn workers.
- Unknown username and wrong password return the same message and take
  comparable time.
- Passwords are hashed with Werkzeug (`generate_password_hash`).
  `PASSWORD_MIN_LENGTH` (default 8) applies to every password set through
  the app. The seed account bypasses it by design.
- The database refuses to demote or deactivate the last active admin
  (trigger on `app_user`, surfaces as a 409).

### Seeding

`core/backend/seed_admin.py` (called by `setup.py`) creates the account
from `ADMIN_USERNAME` / `ADMIN_PASSWORD` only if NO admin exists. It never
modifies an existing account, so editing `.env` afterward changes nothing;
change passwords inside the app. Defaults are `admin` / `admin`: change the
password immediately after first login.

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `SECRET_KEY` | generated by `setup.py` | session signing key, min 32 chars |
| `SESSION_LIFETIME_MINUTES` | 480 | idle session lifetime |
| `SESSION_COOKIE_SECURE` | false | true only when served over HTTPS |
| `ADMIN_USERNAME` | admin | seed account name |
| `ADMIN_PASSWORD` | admin | seed account password |
| `LOGIN_MAX_ATTEMPTS` | 5 | failures before lockout |
| `LOGIN_LOCKOUT_MINUTES` | 15 | lockout duration |
| `PASSWORD_MIN_LENGTH` | 8 | minimum length for app-set passwords |

### Auth endpoints

| Endpoint | Purpose |
|---|---|
| `POST /api/auth/login` | `{username, password}`; 401 invalid, 429 locked |
| `POST /api/auth/logout` | clears the session |
| `GET /api/auth/me` | `{authenticated, username, role, permissions}`; `permissions` maps table to the role's allowed actions (the frontend uses it to hide buttons) |
| `POST /api/auth/password` | `{current_password, new_password}`, any logged-in user; wrong current password answers 403, not 401 |

### Frontend behavior

The bottom-left of the sidebar shows the user area: "Log in / Read-only"
when anonymous, otherwise avatar, username and role, with a menu for Change
password and Log out. Buttons the role may not use are not rendered. This
is cosmetic; the backend enforces everything. A 401 on any mutation opens
the login dialog as an overlay, so the current view (e.g. a half-built
cart) is preserved. If `/api/auth/me` does not exist (no `app_user`
module), the user area is hidden and all buttons render.

All data values are HTML-escaped (`esc()` in `app.js`) before entering a
template. Never interpolate raw data into `innerHTML`.

Audit display is automatic and needs no per-module config. Any list whose
rows carry `created_by` gets **Created** and **Updated** columns (username
over timestamp; `-` when the author is NULL). Every row has a read-only
**View** button (open to everyone, since reads are open) showing all form
fields and columns plus a Created By / Created At / Updated By / Updated At
block; modules with a custom detail screen (`sale`) get the same block
there. Grouped modules (`recipe`) show the earliest creation and the latest
update among the group's rows. Usernames are resolved from the open
`GET /api/app_user`. The audit columns are derived from the rows, so an
empty list shows none until its first row exists.

## Auditing

The `audit_log` module (nav label "Auditing") is an append-only record of
every mutation. `core/backend/audit.py` provides `record()`, which writes
one row inside the SAME transaction as the change it describes, so a
change and its log entry commit or roll back together: there is never a
change without an entry, or an entry without a change. Logging is active
only when `modules/audit_log` is present; without it `record()` is a no-op
and the app runs as before.

### What is logged

| Source | Entries |
|---|---|
| generic create / update / delete (`crud.py`) | `create` / `update` / `delete` on that table |
| password change (`POST /api/auth/password`) | `update` on `app_user`, "changed own password (value hidden)" |
| checkout | `create` on `sale` (one entry summarising the lines; `sale_item` rows are not logged individually), one `override` entry per overridden line, one `update` on `product` per stock change |
| void | `void` on `sale`, one `update` on `product` per restock |
| sale delete (`/remove`) | `delete` on `sale` |

Not logged: reads, login and logout, the admin seed, and failed or
rejected requests (a 403 or 409 changes nothing, so it has no entry).

### Columns

`id`, `username`, `action` (`create`/`update`/`delete`/`void`/`override`),
`table_name`, `row_id`, `detail`, `created_at`.

- `username` is TEXT resolved from the session at the time of the action,
  not a reference to `app_user`, so a deactivated or renamed account never
  corrupts historical readability. With authentication disabled it is
  `anonymous`.
- `detail` is a short free-form summary. Creates and deletes read
  "created product #12" / "deleted product #12". Updates read the
  difference between the stored row and the returned row, e.g. "changed
  price 8000 -> 9000; name Tea -> Black tea", skipping the system-maintained
  columns (`id`, `created_at`, `updated_at`, `created_by`, `updated_by`).
  A checkout override reads "Override on product/item X". Stock changes
  read "changed stock_quantity 10 -> 8 (checkout, sale #5)".
- Values of `hashed_fields` columns are never written: a changed password
  reads "password_hash (value hidden)". Detail is truncated at 500
  characters and any single value at 80.
- `audit_log` has no `created_by`/`updated_by` and no `updated_at`: rows
  are never updated and `username` is the attribution.

### Append-only enforcement

Four independent layers:

1. `table.json` sets `"read_only": true`: `crud.py` registers only the GET
   routes, so POST/PUT/DELETE answer 405.
2. `auth.py` gives no role any action on `audit_log`, regardless of
   `permissions.conf` or the module's `table.json`.
3. `schema.sql` blocks UPDATE, DELETE and TRUNCATE with triggers. The app
   role is granted UPDATE/DELETE like every other module table (the
   `webapp_postgres` role grants uniformly), so the trigger, not the grant,
   is what enforces it.
4. Only `audit.record()` ever inserts.

A Postgres superuser can still drop the triggers: this protects against
application bugs and the app role, not against a database administrator.

### Reading it

Reads are open by design, so anyone who can reach the app can read the
log, including usernames and amounts. The Auditing screen lists newest
first, shows the time to the minute (the full timestamp is stored), and has
a read-only View per entry. It is included in the Excel export like every
other table.

## Adding a module

1. Copy `modules/_template/` to `modules/<name>/`.
2. Fill in `table.json`, `schema.sql`, `frontend/module.json`. Keep the
   `created_by`/`updated_by` columns from the template: `crud.py` fills
   them from the session automatically.
3. Create the table and grant the app's DB role scoped privileges -- copy
   the module folder to the app host, then run the infrastructure repo's
   `webapp_postgres` role (see Database prerequisites).
4. Run `python core/backend/sync_tables.py --module=<name>` (or just `python
   setup.py`) to generate `table_core.py`.
5. Add `<name>` to `default.conf` if it should ship by default, and to
   `core/frontend/modules.json` if it should appear in the running app's
   nav.
6. Decide permissions: the framework default (staff create-only) applies
   unless the module's `table.json` has a `permissions` key or
   `permissions.conf` has a `modules` entry.

A module is CRUD-only config -- no custom Python or JS needed unless it
requires real business logic beyond list/create/update/delete, in which
case that's a deliberate, separate addition to that module's folder (a
`routes.py`, per `assignment`'s and `sale`'s pattern below). Every
mutating route in a `routes.py` MUST declare its permission with
`@requires(table, action)` (from `auth`) directly under the route
decorator; an undeclared mutating route is admin-only. Every mutation in a
`routes.py` SHOULD call `audit.record(conn, action, table, row_id,
detail)` inside its own transaction, so it appears in the Auditing module
(`from audit import record`).

### Optional `table.json` keys

- `"sort": ["col_a", "col_b"]` -- if present, `GET /api/<table>` orders
  results by these columns in order (ascending), instead of the default
  `id desc`. A module with no `"sort"` key keeps the default ordering
  unchanged. Useful for tables where insertion order isn't a meaningful
  browsing order (e.g. `product`, sorted by category then name, vs.
  `sale`, which keeps the default `id desc` since most-recent-first is
  the right order for a transaction log).
- `"distinct_pairs": [["field_a", "field_b"]]` -- generic cross-field
  validation in `crud.py`'s shared `_validate`: rejects a create/update if
  the two named fields hold the same value. Used by `recipe` to stop a
  product being listed as its own ingredient. Backed by a DB-level
  `CHECK` too (the app-level check gives a clean `422`; the DB constraint
  is the actual guarantee).
- `"hashed_fields": ["password_hash"]` -- incoming values are
  password-hashed (with the length policy) before storage, and these
  columns are removed from EVERY response: list, show, create/update
  returns, the distinct endpoint (which answers 404 for them), and the
  Excel export. Used by `app_user`. They are also never written to the
  audit log.
- `"readonly_fields": ["col"]` -- columns the client may never write;
  silently stripped from payloads. Used by `sale` for `voided_at` and
  `voided_by`.
- `"disable_delete": true` -- `DELETE` answers 405 and `delete` is removed
  from every role's permissions for the table.
- `"read_only": true` -- only the GET routes are registered; create,
  update and delete do not exist for the table (405). For tables written
  solely by server code. Used by `audit_log`.
- `"permissions": {"admin": [...], "staff": [...]}` -- module default
  permissions (see Permission layers).

Always read-only for clients on every table, regardless of config: `id`,
`created_at`, `updated_at`, `created_by`, `updated_by`. `created_by` and
`updated_by` are filled from the session on any table that has those
columns, with no per-module configuration.

### Generic distinct-values endpoint

Every module's CRUD blueprint automatically exposes:

```
GET /api/<table>/distinct/<column>
```

Returns a sorted JSON array of that column's distinct non-null values.
Framework-level -- not something a module opts into or configures,
available for any real column on any registered table except hashed
columns. Powers the `"type": "combo"` form field below.

### Optional `frontend/module.json` keys

Beyond the original `table`/`label`/`order`/`default`/`columns`/`form`/
`actions` shape:

- `"hide_add"` (bool) -- suppresses the generic "+ New" button and its
  single-record create form. Used by modules whose rows should never be
  created through the generic single-table POST (e.g. `sale`, which is
  only ever created atomically through a checkout endpoint -- see
  `cart_checkout` below, and `audit_log`, which is never created by a
  client).
- `"cart_checkout"` (bool) -- overrides `hide_add`'s button suppression
  with a "+ New Sale" button that opens a cart UI instead of the generic
  form (`_renderCart` in `app.js`): a browsable product list, an
  add-to-cart flow with quantity +/-, a running total, and a payment
  method selector. Submits the whole cart in one call to
  `checkout_endpoint`. Currently used by `sale`; the cart UI itself is
  custom frontend work (`_renderCart`), not something a module's config
  alone can reproduce for a different shape of transaction.
- `"checkout_endpoint"` -- the URL `cart_checkout`'s submit button POSTs
  to, as `{ payment_method, items: [{ product_id, quantity, override? }, ...] }`.
- `"payment_methods"` (array of strings) -- populates the cart's payment
  method `<select>`.
- `"detail_view"` (bool) -- swaps a row's "Edit" button for "View", which
  calls `detail_endpoint` and renders a read-only summary + related-items
  screen (`_renderDetail`) instead of the generic edit form. Used by
  `sale`, since editing a past sale's fields directly would leave its
  line items and the stock effects they caused silently inconsistent.
- `"detail_endpoint"` -- URL template for the detail view's related-data
  fetch, with `{id}` substituted for the row's id (e.g.
  `/api/sale/{id}/items`).
- `"detail_summary"` -- array of `{ key, label, format? }`, the same
  shape as `columns`, rendered as a label/value table at the top of the
  detail view before the related-items table.

Keep-and-tag voiding (all three `void_*` keys and `purge_endpoint` work
together; used by `sale`):

- `"void_endpoint"` -- URL template (`{id}` substituted) called with `POST`
  by a row's "Void" button, governed by the `void` permission. The
  endpoint must not delete the row.
- `"void_flag_field"` -- the row column that is non-null once voided
  (`voided_at`). An active row shows Void; a voided row gets a "voided" tag
  next to its first column and no Void button. Setting it together with
  `void_endpoint` is what switches the list into void mode.
- `"void_by_field"` -- the column holding who voided it (`voided_by`, the
  default), shown with the voided time on the detail screen.
- `"purge_endpoint"` -- URL template called with `DELETE` by a voided
  row's "Delete" button, governed by the `delete` permission. Active rows
  never offer Delete.

### Optional `columns`/`detail_summary` entry keys

- `"format": "number"` -- renders the value with thousands separators
  and two decimal places (`Number(val).toLocaleString('en-US', ...)`,
  e.g. `11500.00` -> `11,500.00`). Applies at display time only; edit
  forms still show/submit the raw unformatted value, since a `<input
  type="number">` shouldn't contain commas.
- `"format": "datetime"` -- renders a timestamp as `YYYY-MM-DD HH:mm` in
  the browser's local time. Also applied automatically to `datetime-local`
  form fields on the View screen.

### Optional `form` field `type` values

Beyond the original `text`/`number`/`textarea`/`select`/`relation`:

- `"combo"` -- renders as a plain `<input>` backed by a `<datalist>`
  populated from `GET /api/<table>/distinct/<field_key>`. Suggests
  previously-entered values but still accepts a new typed value. Used by
  `product`'s `category`, since category vocabularies vary per deployment
  and shouldn't be a hardcoded `select` list.
- `"password"` -- a password `<input>`, never prefilled. On edit, leaving
  it blank keeps the current value (empty fields are dropped on submit).
  Pair with `hashed_fields` in `table.json`.

Boolean columns accept the strings `"true"`/`"false"` from a `select`
field; `crud.py` coerces them (SQLAlchemy's Boolean type rejects strings).

### Generic constraint-violation handling in `crud.py`

`create()`, `update_row()` and `delete_row()` all wrap their SQL in
`try/except IntegrityError`, returning a clean `409` with Postgres's own
`message_primary` text (e.g. `duplicate key value violates unique
constraint "recipe_unique_pair"`, or a foreign-key violation when deleting
a product that a recipe still references) instead of a raw `500`.
Framework-level, not module-specific. The last-admin trigger on `app_user`
relies on this: it raises with `ERRCODE = 'check_violation'` so it surfaces
as a 409.

### Optional `frontend/module.json` keys for multi-row parent+children editing

- `"multi_add"` (bool) -- for a table that's fundamentally "one parent
  value plus many child rows" (e.g. `recipe`: one menu item, many
  ingredients). Changes the list view entirely: rows are grouped by the
  parent field into one displayed row per parent, "+ New" opens a
  multi-row add screen (`_renderMultiAdd`), and each group's "Edit"
  reopens that same screen pre-filled with the whole set -- editing a
  group always means re-specifying it in full (existing rows are deleted,
  then the new set is created; creation is skipped if any delete fails).
  This is not atomic: each child row is its own independent request.
  Editing a group needs both `create` and `delete`; deleting needs
  `delete`. Implies `hide_add`'s suppression of the plain form.
- `"multi_add_parent_field"` -- `{ key, label, relation: { table,
  label_field } }`, describing the one field shared by the whole group.
- `"multi_add_child_relation"` -- `{ table, label_field }` for the child
  rows' relation lookup.
- `"multi_add_child_key"` -- the child rows' own relation field name.
- `"multi_add_quantity_key"` / `"multi_add_quantity_label"` -- the
  numeric field each child row carries alongside its relation.
- `"add_button_label"` -- overrides the default "+ New" text on any
  module's add button.

### Optional `frontend/module.json` keys: read-only report views

- `"report_view"` (bool) -- for a module with no backing table of its
  own, existing purely to aggregate other tables' data (e.g.
  `reporting`). Renders no add/edit/delete affordances. Fetches from
  `"report_endpoint"` instead of `/api/<table>`.
- `"report_endpoint"` -- the URL the report view fetches from.
- `"report_filters"` (array, e.g. `["start_date", "end_date"]`) --
  renders one date `<input>` per named filter plus an "Apply" button;
  filters are sent as query-string parameters and the view re-fetches on
  Apply (the aggregation happens in SQL).
- `"report_summary_key"` / `"report_summary_label"` -- if set, sums that
  numeric column across every returned row into a footer total.

### Cart stock override

`_renderCart`'s cart lines compare the requested quantity against the
sold product's own `stock_quantity`. When a line exceeds it, an
"Override" button appears on that line -- only for roles holding the
`override` permission; other roles see a note that an admin must override
or the quantity must be reduced. Pressing it flags the line, changes the
warning to a neutral note ("stock will go negative"), and allows checkout
to proceed for it. Checkout is blocked client-side if any overstock line
has NOT been overridden.

The flag is sent to `/api/sale/checkout` as `items[].override: true`. The
backend rejects the whole checkout with 403 if the caller lacks `override`
-- never a silent downgrade to a non-override sale. Every overridden line
is recorded in the Auditing module as "Override on product/item X".
Backend behavior and its one real limitation are documented in
`sale/routes.py`'s module docstring: override is resolved through the same
per-underlying-product aggregation the normal stock check uses, so if two
cart lines share an ingredient and only one is marked override, the whole
combined shortfall for that ingredient is allowed through.

This required a real schema change: `product.stock_quantity` originally
had `CHECK (stock_quantity >= 0)`, which was dropped (one-off migration,
see the infrastructure repo's `drop_stock_check.sql`) and removed from
`modules/product/schema.sql`. A negative `stock_quantity` is now a
legitimate, deliberate state -- "the system's count was wrong and an
authorized user confirmed physical stock existed".

## Modules currently in the catalog

- **`app_user`** -- login accounts: `username` (unique, no surrounding
  whitespace), `password_hash` (never returned by any endpoint or the
  export), `role` (`admin`/`staff`), `active` (users are deactivated,
  never deleted), lockout state (`failed_login_count`, `locked_until`),
  and `created_by`/`updated_by`. Its presence enables authentication.
  Only admins may create or update users. A DB trigger prevents demoting
  or deactivating the last active admin.
- **`audit_log`** -- the Auditing module: append-only record of every
  mutation, written by server code only (see Auditing). Read-only in the
  UI and API; independent of `app_user` at the schema level, but only
  meaningful with it, since without authentication every entry is
  `anonymous`.
- **`equipment`** / **`assignment`** -- the original pair. `equipment`
  is plain CRUD with a hardcoded `category`/`status` vocabulary (stable
  across deployments, unlike `product`'s). `assignment` models a
  duration (`assigned_at`/`released_at`) and has a `routes.py` for its
  one cross-table need: `/api/equipment/available`, filtering the
  create-form's equipment dropdown to unassigned items. With the default
  policy, releasing an assignment is admin-only (it is an update).
- **`product`** -- a sellable catalog item (`name`, `category`, `price`,
  `stock_quantity`, `sku`). Plain CRUD, no `routes.py`. Uses `"sort":
  ["category", "name"]` and a `"combo"` field for `category`, plus
  `"format": "number"` on `price` in its list view.
- **`sale`** / **`sale_item`** -- a point-of-sale transaction and its
  line items. Neither is ever created via the generic single-table POST;
  see `sale`'s `routes.py` below. `sale_item` snapshots
  `unit_price_at_sale` at the time of sale rather than joining live to
  `product.price`, so a later price change never rewrites historical
  sales. `sale` carries nullable `voided_at`/`voided_by`: NULL means
  active. `sale`'s `frontend/module.json` uses `hide_add` +
  `cart_checkout`, `detail_view` + `detail_endpoint`, and the keep-and-tag
  void keys (`void_endpoint`, `void_flag_field`, `void_by_field`,
  `purge_endpoint`).
- **`recipe`** -- ties a menu-item `product` to its ingredient
  `product`(s) with a `quantity_required` per ingredient, enabling cafe
  support: a sold "menu item" decrements its ingredients' stock instead
  of its own, resolved at checkout time. A plain grocery item with no
  `recipe` rows keeps decrementing itself directly -- the same table
  serves both venue types without a type flag. Both product references
  are guarded against self-reference by a `distinct_pairs` entry plus a DB
  `CHECK`, and against duplicate ingredient entries by a DB `UNIQUE`
  constraint (`recipe_unique_pair`). Uses `multi_add` for its UI.
- **`reporting`** -- read-only, no backing table of its own; relies
  entirely on `app.py`'s two-pass loader registering a bare `routes.py`
  with no accompanying `table.json`. `GET
  /api/reporting/sales-summary[?start_date=&end_date=]` groups
  `sale_item` by `(product_id, unit_price_at_sale)`, so a price change
  mid-period surfaces as two separate summary rows, not a blended
  average. Voided sales are excluded. Uses `report_view` on the frontend.

### `sale`'s `routes.py` -- the framework's first multi-table transactional module

Four endpoints beyond generic CRUD, all atomic (single `engine.begin()`
transaction each, including their audit entries). Checkout and void share
one helper, `_resolve_stock_impact`, which takes `[{product_id, quantity},
...]` and returns `{underlying_product_id: total_qty}` -- resolving each
sold item through `recipe` or, for a plain item with no `recipe` rows,
onto itself directly.

- **`POST /api/sale/checkout`** (`create`; `override` additionally for
  override items) -- takes `{ payment_method, items:
  [{product_id, quantity, override?}, ...] }`. Resolves stock impact, then
  row-locks (`with_for_update`) every underlying product involved in one
  pass, so two concurrent checkouts racing for the same unit can't both
  pass their own stock check before either commits; the second waits,
  re-reads the now-updated stock, and fails cleanly with `409`. On
  success: inserts the `sale` row, one `sale_item` per line (snapshotting
  `product.price`), and decrements each underlying product's
  `stock_quantity`. `created_by`/`updated_by` are stamped on the sale, its
  line items, and every product whose stock changes. Audit entries: one
  `create` on the sale, one `override` per overridden line, one `update`
  per stock change. Any failure rolls back the whole transaction.
- **`GET /api/sale/<id>/items`** -- read-only join of `sale_item` to
  `product`, used by the frontend's `detail_view` screen.
- **`POST /api/sale/<id>/void`** (`void`) -- keep-and-tag: sets
  `voided_at` and `voided_by` on the sale and gives the stock back, but
  the `sale` and `sale_item` rows stay, so the sale remains visible
  tagged "voided" and its View screen still shows what was voided. The
  sale row is locked first, so a second void (or a concurrent one)
  answers `409` and stock can never be restored twice. Restock
  re-resolves each sold line through `_resolve_stock_impact` against
  `recipe` as it currently stands (no snapshot of a sale-time recipe
  exists; if a recipe changes between sale and void, the restock follows
  the edited recipe -- accepted limitation). Audit entries: one `void`,
  one `update` per restocked product. Reporting excludes voided sales.
- **`DELETE /api/sale/<id>/remove`** (`delete`) -- permanently deletes a
  sale, and ONLY a voided one (`409` otherwise). It deletes the
  `sale_item` rows first (`sale_item.sale_id` references `sale(id)` with
  the default `RESTRICT`) and never touches stock: the void already
  restored it. Audit entry: one `delete`. The generic `DELETE
  /api/sale/<id>` still exists but is not used by the UI.

The same keep, tag, then allow-delete pattern is the intended shape of the
future Refund feature.

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
- **CRUD routing stays generic even as it grows.** The sort key, the
  distinct-values endpoint, hashing, read-only fields, `read_only` tables,
  and the `hide_add`/`detail_view`/`cart_checkout`/void-mode config
  surface all live in `crud.py`'s shared blueprint builder or `app.js`'s
  shared renderer, not in any one module. A module only needs its own
  Python (a `routes.py`) for real cross-table or transactional business
  logic.
- **A cart/checkout UI is genuinely custom frontend work, not config.**
  `_renderCart` is the one piece of rendering logic that isn't purely
  config-driven -- a variable-length item list with live quantity
  adjustment doesn't fit the single-record form model.
- **Stock limits are enforced server-side.** The client-side overstock
  check is a UX convenience; `checkout`'s row-locked validation is the
  source of truth and returns `409`.
- **A DB-level CHECK constraint is a real design decision.**
  `product.stock_quantity >= 0` was correct and was deliberately dropped
  when "override" became a requirement. Changing such an invariant means
  a migration against the live table, not just editing `schema.sql`.
- **Any module's constraint violation surfaces as a clean error, not a
  stack trace** (generic `IntegrityError` handling in `crud.py`).
- **Authorization is declared, enforced centrally, and fails closed.**
  Handlers declare actions; one hook enforces them; an undeclared
  mutating route is admin-only; permission config errors stop startup
  instead of falling back to a grant.
- **The server re-reads the user on every mutating request.** Cookie
  state alone is never trusted for authorization, so deactivation, role
  changes and password changes take effect immediately.
- **Users are deactivated, never deleted,** so `created_by`/`updated_by`
  attribution stays valid.
- **Sensitive columns are hidden everywhere or nowhere.** A hashed column
  is stripped from list, show, write responses, the distinct endpoint and
  the (unauthenticated) export, and is never written to the audit log;
  hiding it from only some paths is a leak.
- **Voiding keeps the record.** A void tags the sale and restores stock,
  and only a voided sale can then be deleted. The record of a sale is
  never destroyed by a single click.
- **A change and its audit entry are one transaction.** `audit.record()`
  runs on the caller's open connection, never in a second transaction, so
  the log cannot disagree with the data.
- **The audit log stores usernames as text.** Attribution survives account
  deactivation or renaming; the log never depends on `app_user` rows
  staying as they were.
- **The audit log is append-only at the database, not only in code.**
  Application-level guards can be bypassed by a bug; the trigger cannot.

## Known limitations and pending work

- Reads are open by design, so `GET /api/app_user` returns usernames,
  roles, active flags and lock state, and `GET /api/audit_log` returns the
  whole audit trail, to anyone who can reach the app. Password hashes are
  never exposed.
- `created_by`/`updated_by` exist on every table but are nullable: rows
  created before authentication, or with it disabled, have no author.
  They are filled from the session by `crud.py` and `sale/routes.py`; a
  NULL author is displayed as `-`. Sales that predate void keep-and-tag
  were deleted by the old void and cannot be recovered.
- No list is paginated: every `GET /api/<table>` returns all rows. This
  matters most for Auditing, which only grows. Pagination would be a
  generic change to `crud.py` and `app.js`.
- Audit timestamps display to the minute; ordering within a minute follows
  `id`.
- The audit triggers can be dropped by a Postgres superuser.
- Not yet built: the Refund and Purchasing/Restock modules.
- The generic `DELETE /api/sale/<id>` is still registered; it fails on the
  `sale_item` foreign key when a sale has items, but would delete a sale
  that has none. The UI never calls it.
- The session cookie is not `Secure` by default (plain HTTP on a LAN).
- Lockout is temporary but can be triggered deliberately against a known
  username.
- Permission changes need an app restart.
- Recipe edits are delete-then-create and not atomic (and appear as
  separate entries in Auditing).
- Non-browser clients must send `X-Requested-With: app-framework` on every
  mutating request.