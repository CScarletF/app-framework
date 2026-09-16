// app.js -- the generic engine. Modules are pure config (module.json);
// this file is the only place that knows HOW to turn that config into a
// nav entry, a list view, and a form. A module never writes its own
// render function -- if a module ever needs one, that's a deliberate
// exception, not the normal path.
//
// MODIFIED:
//   - showModule / _renderList are now async and resolve `relation`
//     columns (e.g. assignment.equipment_id) to a display label via a
//     lookup fetched from the related module's table.
//   - _renderForm renders `type: "relation"` fields as a populated
//     <select>, optionally scoped to a `relation.available_endpoint`
//     (e.g. only unassigned equipment) for NEW records. Editing an
//     existing record always includes its current value even if that
//     value is no longer "available", so the select never silently
//     drops the current selection.
//   - _renderList renders an optional `config.actions` array as plain
//     link-buttons above the table (e.g. an Export to Excel button) --
//     generic, config-driven, no module-specific JS needed.
//   - _renderList's status column renders as a dot+label badge
//     (dark-theme design) instead of plain text.
//   - Both <h2> headings carry the "hero-title" class (dark-theme design).
//   - _renderForm wires table.json's `required` flag to the native HTML
//     `required` attribute -- browser shows its own "please fill in this
//     field" popup, no custom validation JS needed.
//   - The submit handler strips empty-string fields from the payload
//     before sending -- an untouched optional <input> always submits ""
//     via FormData, which most Postgres column types (timestamptz,
//     integer) reject outright. Omitting the key entirely lets the DB
//     apply its own default/NULL instead.

const App = {
    modules: [],   // [{ config, cssLoaded }]
    activeTable: null,

    async init() {
        const manifest = await fetch('modules.json').then(r => r.json());

        for (const name of manifest.modules) {
            const config = await fetch(`modules/${name}/frontend/module.json`).then(r => r.json());
            this.modules.push({ name, config });
            await this._maybeLoadCss(name);
        }

        // Sort by each module's own declared `order` -- nav order is a
        // property of the module, not of whatever sequence scaffold.py
        // happened to copy files in.
        this.modules.sort((a, b) => (a.config.order ?? 0) - (b.config.order ?? 0));

        this._buildNav();

        const defaultModule =
            this.modules.find(m => m.config.default) ?? this.modules[0];
        if (defaultModule) this.showModule(defaultModule.config.table);
    },

    async _maybeLoadCss(name) {
        // Not every module has one -- a 404 here is expected and silent
        // by design (module.css is optional per module).
        try {
            const res = await fetch(`modules/${name}/frontend/module.css`, { method: 'HEAD' });
            if (res.ok) {
                const link = document.createElement('link');
                link.rel = 'stylesheet';
                link.href = `modules/${name}/frontend/module.css`;
                document.head.appendChild(link);
            }
        } catch (_) { /* no module.css, that's fine */ }
    },

    _buildNav() {
        const nav = document.getElementById('nav');
        nav.innerHTML = '';
        for (const { config } of this.modules) {
            const a = document.createElement('a');
            a.href = '#';
            a.textContent = config.label;
            a.dataset.table = config.table;
            a.addEventListener('click', (e) => {
                e.preventDefault();
                this.showModule(config.table);
            });
            nav.appendChild(a);
        }
    },

    _setActiveNav(table) {
        document.querySelectorAll('#nav a').forEach(a => {
            a.classList.toggle('active', a.dataset.table === table);
        });
    },

    async showModule(table) {
        this.activeTable = table;
        this._setActiveNav(table);
        const { config } = this.modules.find(m => m.config.table === table);
        const rows = await Api.list(table);
        await this._renderList(config, rows);
    },

    async _renderList(config, rows) {
        const content = document.getElementById('content');
        const cols = config.columns;

        // Relation columns (e.g. equipment_id) need a lookup from id -> label
        // before rendering. Fetched once per render, not per row.
        const lookups = {};
        for (const c of cols.filter(c => c.relation)) {
            const items = await Api.list(c.relation.table);
            lookups[c.key] = Object.fromEntries(
                items.map(i => [String(i.id), i[c.relation.label_field]])
            );
        }

        const header = cols.map(c => `<th>${c.label}</th>`).join('');
        const body = rows.map(row => {
            const cells = cols.map(c => {
                const raw = row[c.key];
                if (c.key === 'status') {
                    const isActive = raw === 'in_use';
                    return `<td><span class="status ${isActive ? 'status-active' : ''}">${raw ?? ''}</span></td>`;
                }
                let val = c.relation ? (lookups[c.key][String(raw)] ?? raw ?? '') : (raw ?? '');
                if (c.format === 'number' && val !== '') {
                    val = Number(val).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
                }
                return `<td>${val}</td>`;
            }).join('');
            const primaryAction = config.detail_view
                ? `<button data-action="view" data-id="${row.id}">View</button>`
                : `<button data-action="edit" data-id="${row.id}">Edit</button>`;
            return `<tr>${cells}<td>${primaryAction}
                <button data-action="delete" data-id="${row.id}">Delete</button>
            </td></tr>`;
        }).join('');

        // Generic, config-driven action buttons (e.g. Export to Excel) --
        // plain links to a backend endpoint, no module-specific JS needed.
        const actions = (config.actions ?? [])
            .map(a => `<a class="module-action" href="${a.href}">${a.label}</a>`)
            .join('');

        const addButtonLabel = config.cart_checkout ? '+ New Sale' : '+ New';
        const showAddButton = config.cart_checkout || !config.hide_add;

        content.innerHTML = `
            <h2 class="hero-title">${config.label}</h2>
            ${showAddButton ? `<button id="add-new">${addButtonLabel}</button>` : ''}
            ${actions}
            <table><thead><tr>${header}<th></th></tr></thead><tbody>${body}</tbody></table>
        `;

        if (showAddButton) {
            content.querySelector('#add-new').addEventListener('click', () => {
                if (config.cart_checkout) {
                    this._renderCart(config);
                } else {
                    this._renderForm(config);
                }
            });
        }
        content.querySelectorAll('[data-action="view"]').forEach(btn => {
            btn.addEventListener('click', async () => {
                const row = await Api.get(config.table, btn.dataset.id);
                this._renderDetail(config, row);
            });
        });
        content.querySelectorAll('[data-action="edit"]').forEach(btn => {
            btn.addEventListener('click', async () => {
                const row = await Api.get(config.table, btn.dataset.id);
                this._renderForm(config, row);
            });
        });
        content.querySelectorAll('[data-action="delete"]').forEach(btn => {
            btn.addEventListener('click', async () => {
                if (!confirm('Delete this row?')) return;
                if (config.void_endpoint) {
                    await fetch(config.void_endpoint.replace('{id}', btn.dataset.id), { method: 'DELETE' });
                } else {
                    await Api.remove(config.table, btn.dataset.id);
                }
                this.showModule(config.table);
            });
        });
    },

    async _renderDetail(config, row) {
        const content = document.getElementById('content');
        const endpoint = config.detail_endpoint.replace('{id}', row.id);
        const items = await fetch(endpoint).then(r => r.json());

        const summaryRows = (config.detail_summary ?? []).map(f => {
            let val = row[f.key] ?? '';
            if (f.format === 'number' && val !== '') {
                val = Number(val).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
            }
            return `<tr><th>${f.label}</th><td>${val}</td></tr>`;
        }).join('');

        const itemRows = items.map(i => {
            const subtotal = (i.quantity * i.unit_price_at_sale).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
            const unitPrice = Number(i.unit_price_at_sale).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
            return `<tr><td>${i.product_name}</td><td>${i.quantity}</td><td>${unitPrice}</td><td>${subtotal}</td></tr>`;
        }).join('');

        content.innerHTML = `
            <h2 class="hero-title">${config.label} Detail</h2>
            <table>${summaryRows}</table>
            <h2 class="hero-title">Items</h2>
            <table>
                <thead><tr><th>Product</th><th>Qty</th><th>Unit Price</th><th>Subtotal</th></tr></thead>
                <tbody>${itemRows}</tbody>
            </table>
            <button id="back">Back</button>
        `;

        content.querySelector('#back').addEventListener('click', () => this.showModule(config.table));
    },
    async _renderCart(config) {
        const content = document.getElementById('content');
        const products = await Api.list('product');
        const cart = {}; // product_id (string) -> { product, quantity }
        const paymentMethods = config.payment_methods ?? ['cash'];
        const fmt = (n) => Number(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

        const renderProductList = () => {
            const rows = products.map(p => `
                <tr>
                    <td>${p.category}</td>
                    <td>${p.name}</td>
                    <td>${fmt(p.price)}</td>
                    <td>${p.stock_quantity}</td>
                    <td><button data-add="${p.id}">Add</button></td>
                </tr>
            `).join('');
            return `<table><thead><tr><th>Category</th><th>Name</th><th>Price</th><th>Stock</th><th></th></tr></thead><tbody>${rows}</tbody></table>`;
        };

        const renderCartTable = () => {
            const entries = Object.values(cart);
            const rows = entries.map(e => `
                <tr>
                    <td>${e.product.name}</td>
                    <td>
                        <button data-decrement="${e.product.id}">-</button>
                        ${e.quantity}
                        <button data-increment="${e.product.id}">+</button>
                    </td>
                    <td>${fmt(e.product.price)}</td>
                    <td>${fmt(e.product.price * e.quantity)}</td>
                    <td><button data-remove="${e.product.id}">Remove</button></td>
                </tr>
            `).join('');
            const total = entries.reduce((sum, e) => sum + e.product.price * e.quantity, 0);
            return `
                <table><thead><tr><th>Product</th><th>Qty</th><th>Unit Price</th><th>Subtotal</th><th></th></tr></thead>
                <tbody>${rows}</tbody></table>
                <p><strong>Total: ${fmt(total)}</strong></p>
            `;
        };

        const refreshCart = () => {
            content.querySelector('#cart-area').innerHTML = renderCartTable();
            wireCartButtons();
        };

        const wireCartButtons = () => {
            content.querySelectorAll('[data-increment]').forEach(btn => {
                btn.addEventListener('click', () => {
                    cart[btn.dataset.increment].quantity += 1;
                    refreshCart();
                });
            });
            content.querySelectorAll('[data-decrement]').forEach(btn => {
                btn.addEventListener('click', () => {
                    const id = btn.dataset.decrement;
                    cart[id].quantity -= 1;
                    if (cart[id].quantity <= 0) delete cart[id];
                    refreshCart();
                });
            });
            content.querySelectorAll('[data-remove]').forEach(btn => {
                btn.addEventListener('click', () => {
                    delete cart[btn.dataset.remove];
                    refreshCart();
                });
            });
        };

        content.innerHTML = `
            <h2 class="hero-title">New Sale</h2>
            <h2 class="hero-title">Products</h2>
            ${renderProductList()}
            <h2 class="hero-title">Cart</h2>
            <div id="cart-area">${renderCartTable()}</div>
            <label>Payment Method
                <select id="payment-method">
                    ${paymentMethods.map(m => `<option value="${m}">${m}</option>`).join('')}
                </select>
            </label>
            <div class="error" id="cart-error"></div>
            <button id="checkout">Checkout</button>
            <button type="button" id="cancel">Cancel</button>
        `;

        content.querySelectorAll('[data-add]').forEach(btn => {
            btn.addEventListener('click', () => {
                const id = btn.dataset.add;
                if (cart[id]) {
                    cart[id].quantity += 1;
                } else {
                    cart[id] = { product: products.find(p => String(p.id) === id), quantity: 1 };
                }
                refreshCart();
            });
        });

        content.querySelector('#cancel').addEventListener('click', () => this.showModule(config.table));

        content.querySelector('#checkout').addEventListener('click', async () => {
            const items = Object.values(cart).map(e => ({ product_id: e.product.id, quantity: e.quantity }));
            if (items.length === 0) {
                content.querySelector('#cart-error').textContent = 'Cart is empty';
                return;
            }
            const payment_method = content.querySelector('#payment-method').value;

            const res = await fetch(config.checkout_endpoint, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ payment_method, items }),
            });
            const body = await res.json();

            if (!res.ok) {
                content.querySelector('#cart-error').textContent = body.error ?? 'Checkout failed';
                return;
            }
            this.showModule(config.table);
        });
    },
    async _renderForm(config, existing = null) {
        const content = document.getElementById('content');

        const fields = await Promise.all(config.form.map(async f => {
            const value = existing?.[f.key] ?? '';
            const req = f.required ? 'required' : '';

            if (f.type === 'relation') {
                // New record: use available_endpoint if the module config
                // declares one (e.g. only unassigned equipment). Editing an
                // existing record: always use the full table, then guarantee
                // the current value is present even if it wouldn't appear in
                // the "available" set -- otherwise the select silently drops
                // the current selection.
                const endpoint = (!existing && f.relation.available_endpoint)
                    ? f.relation.available_endpoint
                    : `/api/${f.relation.table}`;
                let items = await fetch(endpoint).then(r => r.json());

                if (existing && value && !items.some(i => String(i.id) === String(value))) {
                    const current = await Api.get(f.relation.table, value);
                    items = [current, ...items];
                }

                const opts = items.map(i =>
                    `<option value="${i.id}" ${String(i.id) === String(value) ? 'selected' : ''}>${i[f.relation.label_field]}</option>`
                ).join('');
                return `<label>${f.label}<select name="${f.key}" ${req}>${opts}</select></label>`;
            }
                        if (f.type === 'combo') {
                const distinctValues = await fetch(`/api/${config.table}/distinct/${f.key}`).then(r => r.json());
                const datalistId = `datalist-${f.key}`;
                const opts = distinctValues.map(v => `<option value="${v}"></option>`).join('');
                return `<label>${f.label}
                    <input name="${f.key}" type="text" value="${value}" list="${datalistId}" ${req}>
                    <datalist id="${datalistId}">${opts}</datalist>
                </label>`;
            }
            if (f.type === 'select') {
                const opts = f.options.map(o =>
                    `<option value="${o}" ${o === value ? 'selected' : ''}>${o}</option>`
                ).join('');
                return `<label>${f.label}<select name="${f.key}" ${req}>${opts}</select></label>`;
            }
            if (f.type === 'textarea') {
                return `<label>${f.label}<textarea name="${f.key}" ${req}>${value}</textarea></label>`;
            }
            return `<label>${f.label}<input name="${f.key}" type="${f.type}" value="${value}" ${req}></label>`;
        }));

        content.innerHTML = `
            <h2 class="hero-title">${existing ? 'Edit' : 'New'} ${config.label}</h2>
            <form id="module-form">
                ${fields.join('')}
                <div class="error" id="form-error"></div>
                <button type="submit">Save</button>
                <button type="button" id="cancel">Cancel</button>
            </form>
        `;

        content.querySelector('#cancel').addEventListener('click', () => this.showModule(config.table));

        content.querySelector('#module-form').addEventListener('submit', async (e) => {
            e.preventDefault();
            const raw = Object.fromEntries(new FormData(e.target).entries());
            // Empty strings from untouched optional inputs aren't valid values
            // for most Postgres column types (timestamptz, integer, etc.) --
            // omit them entirely rather than sending "" and letting the DB
            // reject the insert.
            const data = Object.fromEntries(
                Object.entries(raw).filter(([, v]) => v !== '')
            );

            const result = existing
                ? await Api.update(config.table, existing.id, data)
                : await Api.create(config.table, data);

            if (!result.ok) {
                content.querySelector('#form-error').textContent = result.body.error ?? 'Save failed';
                return;
            }
            this.showModule(config.table);
        });
    },
};

App.init();