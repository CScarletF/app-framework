# app-framework

Reusable base for internal logistics/business apps. This repo is
**framework and module catalog only** -- actual products (e.g. an
equipment tracker, or a store point-of-sale build) live in their own
repos, built by `scaffold.py`.

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
3. Apply the schema and grant the app's DB role scoped privileges on the
   new table (and its `id` sequence) -- either by hand, or via the
   companion Postgres/Ansible repo's `webapp_postgres` role, which
   discovers every module's `schema.sql` automatically and needs no
   change to itself when a module is added.
4. Run `python core/backend/sync_tables.py --module=<name> --apply-schema`
   against a real Postgres instance to generate `table_core.py`. If the
   table already exists (step 3 already applied it), this step reflects
   it without re-applying the schema.
5. Add `<name>` to `default.conf` if it should ship by default, and to
   `core/frontend/modules.json` if it should appear in the running app's
   nav.

A module is CRUD-only config -- no custom Python or JS needed unless it
requires real business logic beyond list/create/update/delete, in which
case that's a deliberate, separate addition to that module's folder (a
`routes.py`, per `assignment`'s and `sale`'s pattern below).

### Optional `table.json` keys

- `"sort": ["col_a", "col_b"]` -- if present, `GET /api/<table>` orders
  results by these columns in order (ascending), instead of the default
  `id desc`. A module with no `"sort"` key keeps the default ordering
  unchanged -- this is additive, not a breaking change to existing
  modules. Useful for tables where insertion order isn't a meaningful
  browsing order (e.g. `product`, sorted by category then name, vs.
  `sale`, which keeps the default `id desc` since most-recent-first is
  the right order for a transaction log).

### Generic distinct-values endpoint

Every module's CRUD blueprint automatically exposes:

```
GET /api/<table>/distinct/<column>
```

Returns a sorted JSON array of that column's distinct non-null values.
Framework-level -- not something a module opts into or configures, it's
available for any real column on any registered table. Powers the
`"type": "combo"` form field below without hand-rolling a per-module
lookup route each time. A module doesn't have to use it to have it
available.

### Optional `frontend/module.json` keys

Beyond the original `table`/`label`/`order`/`default`/`columns`/`form`/
`actions` shape:

- `"hide_add"` (bool) -- suppresses the generic "+ New" button and its
  single-record create form. Used by modules whose rows should never be
  created through the generic single-table POST (e.g. `sale`, which is
  only ever created atomically through a checkout endpoint -- see
  `cart_checkout` below).
- `"cart_checkout"` (bool) -- overrides `hide_add`'s button suppression
  with a "+ New Sale" button that opens a cart UI instead of the generic
  form (`_renderCart` in `app.js`): a browsable product list, an
  add-to-cart flow with quantity +/-, a running total, and a payment
  method selector. Submits the whole cart in one call to
  `checkout_endpoint`. Currently used by `sale`; the cart UI itself is
  custom frontend work (`_renderCart`), not something a module's config
  alone can reproduce for a different shape of transaction.
- `"checkout_endpoint"` -- the URL `cart_checkout`'s submit button POSTs
  to, as `{ payment_method, items: [{ product_id, quantity }, ...] }`.
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
- `"void_endpoint"` -- if present, a row's "Delete" button calls this
  URL template (`{id}` substituted) via `DELETE` instead of the generic
  `Api.remove`. Used by `sale`, whose deletion must reverse the
  checkout's side effects (see `sale`'s `routes.py` below) rather than
  just removing a row -- the button label stays "Delete" in the UI since
  the effect (the row disappears) is the same from the user's
  perspective; only what happens underneath differs.

### Optional `columns`/`detail_summary` entry keys

- `"format": "number"` -- renders the value with thousands separators
  and two decimal places (`Number(val).toLocaleString('en-US', ...)`,
  e.g. `11500.00` → `11,500.00`). Applies at display time only; edit
  forms still show/submit the raw unformatted value, since a `<input
  type="number">` shouldn't contain commas.

### Optional `form` field `type` values

Beyond the original `text`/`number`/`textarea`/`select`/`relation`:

- `"combo"` -- renders as a plain `<input>` backed by a `<datalist>`
  populated from `GET /api/<table>/distinct/<field_key>` (see above).
  Suggests previously-entered values but still accepts a new typed
  value -- native browser behavior, no extra JS needed per field. Used
  by `product`'s `category`, since category vocabularies vary per
  deployment (a grocery app's categories look nothing like a cafe's) and
  shouldn't be a hardcoded `select` list the way `equipment`'s
  `category` is.

## Modules currently in the catalog

- **`equipment`** / **`assignment`** -- the original pair. `equipment`
  is plain CRUD with a hardcoded `category`/`status` vocabulary (stable
  across deployments, unlike `product`'s). `assignment` models a
  duration (`assigned_at`/`released_at`) and has a `routes.py` for its
  one cross-table need: `/api/equipment/available`, filtering the
  create-form's equipment dropdown to unassigned items.
- **`product`** -- a sellable catalog item (`name`, `category`, `price`,
  `stock_quantity`, `sku`). Plain CRUD, no `routes.py`. Uses `"sort":
  ["category", "name"]` and a `"combo"` field for `category`, plus
  `"format": "number"` on `price` in its list view.
- **`sale`** / **`sale_item`** -- a point-of-sale transaction and its
  line items. Neither is ever created via the generic single-table POST;
  see `sale`'s `routes.py` below. `sale_item` snapshots
  `unit_price_at_sale` at the time of sale rather than joining live to
  `product.price`, so a later price change never rewrites historical
  sales. `sale`'s `frontend/module.json` uses `hide_add` +
  `cart_checkout` (checkout replaces generic create), `detail_view` +
  `detail_endpoint` (View replaces Edit), and `void_endpoint` (Delete
  reverses checkout's stock effects instead of a bare row delete).

### `sale`'s `routes.py` -- the framework's first multi-table transactional module

Three endpoints beyond generic CRUD, all atomic (single `engine.begin()`
transaction each):

- **`POST /api/sale/checkout`** -- takes `{ payment_method, items:
  [{product_id, quantity}, ...] }`. Row-locks (`with_for_update`) every
  involved `product` row before validating stock, so two concurrent
  checkouts racing for the last unit can't both pass their own stock
  check before either commits -- the second waits, re-reads the
  now-updated stock, and fails cleanly with `409` instead of
  overselling. On success: inserts the `sale` row, inserts one
  `sale_item` per line (snapshotting `product.price` into
  `unit_price_at_sale`), and decrements each product's `stock_quantity`.
  Any failure partway through (unknown product, insufficient stock)
  rolls back the whole transaction -- no partial sale is ever left
  committed.
- **`GET /api/sale/<id>/items`** -- read-only join of `sale_item` to
  `product` (for the product name), used by the frontend's `detail_view`
  screen. `crud.py`'s generic `show()` only ever returns the `sale` row
  itself, never its line items.
- **`DELETE /api/sale/<id>/void`** -- the reverse of checkout: restores
  each line item's quantity back onto its product's `stock_quantity`,
  then deletes the `sale_item` rows and the `sale` row, all in one
  transaction. This exists because `sale_item.sale_id` references
  `sale(id)` with the default `RESTRICT` behavior -- a plain `DELETE
  FROM sale` fails with a foreign-key violation (by design; it's a
  safety net against ever deleting a sale while orphaning its line
  items), and even if it didn't fail, a bare delete would never give
  back the stock checkout took. `void` is the only correct way to
  "delete" a sale.

This is the framework's first module where CRUD genuinely isn't enough
even for creation, not just for one cross-table read (contrast with
`assignment`'s single extra route) -- worth keeping in mind as a
template for any future module with real transactional/inventory
semantics.

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
- **CRUD routing stays generic even as it grows.** The sort-order key,
  the distinct-values endpoint, and the `hide_add`/`detail_view`/
  `cart_checkout`/`void_endpoint` config surface all live in `crud.py`'s
  shared blueprint builder or `app.js`'s shared renderer, not in any one
  module -- the same principle as the original CRUD design: a module
  should only ever need its own Python (a `routes.py`) when it has real
  cross-table or transactional business logic (see `assignment`'s
  availability filter, or `sale`'s checkout/void), never for per-table
  ordering, lookup, or UI-affordance behavior.
- **A cart/checkout UI is genuinely custom frontend work, not config.**
  `_renderCart` in `app.js` is the one piece of rendering logic that
  isn't purely config-driven the way `_renderList`/`_renderForm`/
  `_renderDetail` are -- a variable-length item list with live quantity
  adjustment and a running total doesn't fit the single-record form
  model. `cart_checkout` in a module's config only toggles *which*
  built-in cart implementation is used and where it submits; it isn't a
  general mechanism for arbitrary custom UIs the way `columns`/`form`
  are for the generic list/form.
- **Stock limits are enforced server-side, not duplicated in the cart
  UI.** The cart lets a user increment a line's quantity past the
  product's currently-known stock; `checkout`'s row-locked validation is
  the actual source of truth and returns `409` if exceeded, surfaced in
  the cart's error area. This avoids a second, potentially stale copy of
  stock-limit logic living in JS.