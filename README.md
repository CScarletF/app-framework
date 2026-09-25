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

### Optional `table.json` validation key: `distinct_pairs`

```json
"distinct_pairs": [["menu_item_product_id", "ingredient_product_id"]]
```

Generic cross-field validation, enforced in `crud.py`'s shared
`_validate`: rejects a create/update if the two named fields hold the
same value. Used by `recipe` to stop a product being listed as its own
ingredient. Backed by a DB-level `CHECK` too (belt and suspenders --
the app-level check gives a clean `422` with a clear message; the DB
constraint is the actual guarantee if something ever bypasses the API).

### Generic constraint-violation handling in `crud.py`

`create()` and `update_row()` both wrap their SQL in `try/except
IntegrityError`, returning a clean `409` with Postgres's own
`message_primary` text (e.g. `duplicate key value violates unique
constraint "recipe_unique_pair"`) instead of letting an unhandled
`IntegrityError` bubble up as a raw `500`. This is framework-level, not
module-specific -- any module's unique/FK/check constraint violation
now surfaces cleanly to the frontend's error area rather than crashing
the request. Added after `recipe`'s `recipe_unique_pair` constraint (see
below) exposed the gap: the generic multi-row "multi-add" UI submits
several independent creates in sequence, so a single bad row needs to
fail cleanly without taking the whole request down.

### Optional `frontend/module.json` keys for multi-row parent+children editing

- `"multi_add"` (bool) -- for a table that's fundamentally "one parent
  value plus many child rows" (e.g. `recipe`: one menu item, many
  ingredients). Changes the list view entirely: rows are grouped by the
  parent field into one displayed row per parent (all children listed
  inline), "+ New" opens a multi-row add screen (`_renderMultiAdd`)
  instead of the single-record form, and each group's "Edit" reopens
  that same screen pre-filled with the whole set -- editing a group
  always means re-specifying it in full (existing rows are deleted, then
  the new set is created), never patching one child row in place. Each
  child row is its own independent `POST` to the generic create
  endpoint, not one atomic multi-row operation, since the rows have no
  transactional relationship to each other. Implies `hide_add`'s
  suppression of the plain single-record form; no need to set both.
- `"multi_add_parent_field"` -- `{ key, label, relation: { table,
  label_field } }`, describing the one field shared by the whole group
  (e.g. `menu_item_product_id` on `recipe`).
- `"multi_add_child_relation"` -- `{ table, label_field }` for the
  child rows' relation lookup (e.g. `product` for `recipe`'s
  ingredients).
- `"multi_add_child_key"` -- the child rows' own relation field name
  (e.g. `ingredient_product_id`).
- `"multi_add_quantity_key"` / `"multi_add_quantity_label"` -- the
  numeric field each child row carries alongside its relation (e.g.
  `quantity_required` / "Quantity Required").
- `"add_button_label"` -- overrides the default "+ New" text on any
  module's add button (used together with `multi_add` and
  `cart_checkout`, which already had their own hardcoded labels before
  this was generalized).

### Optional `frontend/module.json` keys: read-only report views

- `"report_view"` (bool) -- for a module with no backing table of its
  own, existing purely to aggregate other tables' data (e.g.
  `reporting`). Renders no add/edit/delete affordances at all. Fetches
  from `"report_endpoint"` instead of `/api/<table>`, since a report-view
  module has no generic CRUD blueprint to fetch from.
- `"report_endpoint"` -- the URL the report view fetches from.
- `"report_filters"` (array, e.g. `["start_date", "end_date"]`) --
  renders one date `<input>` per named filter plus an "Apply" button;
  the filters are sent as query-string parameters and the view re-fetches
  on Apply rather than filtering client-side, since the aggregation
  itself happens in SQL.
- `"report_summary_key"` / `"report_summary_label"` -- if set, sums that
  numeric column across every returned row into a footer total (e.g.
  total revenue across all rows currently shown).

### Cart stock override

`_renderCart`'s cart lines compare the requested quantity against the
sold product's own `stock_quantity`. When a line exceeds it, an
"Override" button appears on that line only -- pressing it flags that
line, changes the warning to a neutral note ("stock will go negative"),
and allows checkout to proceed for it. Checkout is blocked client-side
if any overstock line has NOT been overridden, with a clear list of
which product(s) still need resolving.

The override flag is sent to `/api/sale/checkout` as `items[].override:
true`. Backend behavior and its one real limitation are documented in
`sale/routes.py`'s module docstring (see below) -- summarized: override
is resolved through the same per-underlying-product aggregation the
normal stock check already uses, so if two cart lines share an
ingredient and only one is marked override, the whole combined shortfall
for that ingredient is allowed through, not just the overridden line's
share.

**This required a real schema change, not just an application-level
skip**: `product.stock_quantity` originally had `CHECK (stock_quantity
>= 0)`, which unconditionally rejects any UPDATE that would take it
negative regardless of what application code decides to allow. The
constraint was dropped (via a one-off migration, see the companion
Postgres/Ansible repo's `drop_stock_check.sql`) and removed from
`modules/product/schema.sql` so fresh installs don't recreate it. A
product's `stock_quantity` going negative is now a legitimate,
deliberate state -- it means "the system's count was wrong and a
manager confirmed physical stock still existed," not a bug.

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
- **`recipe`** -- ties a menu-item `product` to its ingredient
  `product`(s) with a `quantity_required` per ingredient, enabling cafe
  support: a sold "menu item" decrements its ingredients' stock instead
  of its own, resolved at checkout time (see `sale/routes.py` below). A
  plain grocery item with no `recipe` rows keeps decrementing itself
  directly -- the same table serves both venue types without a type
  flag, since checkout simply checks whether `recipe` rows exist for
  whatever was sold. `menu_item_product_id` and `ingredient_product_id`
  both point at `product`, guarded against self-reference by a
  `distinct_pairs` entry in `table.json` plus a DB `CHECK`, and against
  duplicate ingredient entries per menu item by a DB `UNIQUE` constraint
  (`recipe_unique_pair`). Uses `multi_add` (see above) for its
  list/create/edit UI, since one menu item naturally has many
  ingredients and they're edited as one set, not as independent rows.
- **`reporting`** -- read-only, no backing table of its own; the
  framework's first module to rely entirely on `app.py`'s existing
  two-pass loader registering a bare `routes.py` with no accompanying
  `table.json`. `GET /api/reporting/sales-summary[?start_date=&end_date=]`
  groups `sale_item` by `(product_id, unit_price_at_sale)`, joined to
  `sale` (for date filtering against `sold_at`) and `product` (for the
  display name). Grouping on the *snapshotted* price rather than the
  product's current price means a price change mid-period surfaces as
  two separate summary rows, not a blended average -- the same reasoning
  `sale_item` itself is snapshotted for. Uses `report_view` (see above)
  on the frontend.

### `sale`'s `routes.py` -- the framework's first multi-table transactional module

Three endpoints beyond generic CRUD, all atomic (single `engine.begin()`
transaction each). All three share one helper, `_resolve_stock_impact`,
which takes `[{product_id, quantity}, ...]` and returns
`{underlying_product_id: total_qty}` -- resolving each sold item through
`recipe` (a menu item's requirement cascades onto its ingredients,
aggregated across every cart line so two items sharing an ingredient
combine into one combined figure) or, for a plain item with no `recipe`
rows, onto itself directly.

- **`POST /api/sale/checkout`** -- takes `{ payment_method, items:
  [{product_id, quantity, override?}, ...] }`. Resolves stock impact via
  `_resolve_stock_impact`, then row-locks (`with_for_update`) every
  underlying product involved -- directly-sold items (for pricing) and
  every ingredient pulled in via `recipe` (for stock) -- in one pass, so
  two concurrent checkouts racing for the same unit (whether sold
  directly or as a shared ingredient) can't both pass their own stock
  check before either commits; the second waits, re-reads the
  now-updated stock, and fails cleanly with `409`. An item with
  `"override": true` skips the sufficiency check for whatever it
  resolves to (see the cart override section above for the limitation
  this carries). On success: inserts the `sale` row, inserts one
  `sale_item` per line (snapshotting `product.price` into
  `unit_price_at_sale` -- the price actually sold, not the underlying
  ingredient's price), and decrements each underlying product's
  `stock_quantity` (now allowed to go negative when overridden, since
  the `>= 0` CHECK was dropped). Any failure partway through rolls back
  the whole transaction.
- **`GET /api/sale/<id>/items`** -- read-only join of `sale_item` to
  `product` (for the product name), used by the frontend's `detail_view`
  screen. `crud.py`'s generic `show()` only ever returns the `sale` row
  itself, never its line items.
- **`DELETE /api/sale/<id>/void`** -- the reverse of checkout: re-resolves
  each sold line through `_resolve_stock_impact` again (against `recipe`
  as it currently stands -- no snapshot of what a recipe looked like at
  sale time exists, unlike `unit_price_at_sale`; if a recipe changes
  between a sale and its void, the restock follows the edited recipe,
  documented as an accepted limitation in the module's docstring), adds
  the resolved quantities back onto `stock_quantity`, then deletes the
  `sale_item` rows and the `sale` row, all in one transaction. This
  exists because `sale_item.sale_id` references `sale(id)` with the
  default `RESTRICT` behavior -- a plain `DELETE FROM sale` fails with a
  foreign-key violation (by design; a safety net against ever deleting a
  sale while orphaning its line items), and even if it didn't fail, a
  bare delete would never give back the stock checkout took. `void` is
  the only correct way to "delete" a sale.

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
  stock-limit logic living in JS. The one client-side check that does
  exist -- blocking checkout submission while an unresolved overstock
  line has no override -- is a UX convenience to avoid a round-trip for
  an error the user could already see, not a substitute for the
  server-side check.
- **A DB-level CHECK constraint is a real design decision, not a
  default to leave alone.** `product.stock_quantity >= 0` was originally
  correct (stock should never go negative) and was deliberately dropped
  once "override" became a real requirement -- a business rule change,
  not a bug fix. The lesson generalized: a CHECK constraint enforces an
  invariant the application currently believes is always true, and
  changing that invariant means an actual migration against the live
  table (see the companion Postgres/Ansible repo), not just editing
  `schema.sql` for future installs.
- **Any module's constraint violation should surface as a clean error,
  not a stack trace.** `crud.py`'s generic `create()`/`update_row()` now
  catch `IntegrityError` uniformly, rather than each module needing its
  own try/except for its own constraints. This matters more once a
  module (like `recipe`) can be edited through a multi-row UI that fires
  several independent creates per save -- one bad row needs to fail
  cleanly without an unhandled exception taking the whole request (and,
  transitively, the gunicorn worker's response) down.