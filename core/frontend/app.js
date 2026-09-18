// app.js -- the generic engine. Modules are pure config (module.json);
// this file is the only place that knows HOW to turn that config into a
// nav entry, a list view, and a form. A module never writes its own
// render function -- if a module ever needs one, that's a deliberate
// exception, not the normal path.

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

        this.modules.sort((a, b) => (a.config.order ?? 0) - (b.config.order ?? 0));

        this._buildNav();

        const defaultModule =
            this.modules.find(m => m.config.default) ?? this.modules[0];
        if (defaultModule) this.showModule(defaultModule.config.table);
    },

    async _maybeLoadCss(name) {
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
        // Grouped modules (multiple child rows per one parent, e.g.
        // recipe's many ingredients per menu item) get an entirely
        // different list rendering -- see _renderGrouped.
        if (config.multi_add) {
            return this._renderGrouped(config, rows);
        }

        const content = document.getElementById('content');
        const cols = config.columns;

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

        const actions = (config.actions ?? [])
            .map(a => `<a class="module-action" href="${a.href}">${a.label}</a>`)
            .join('');

        const addButtonLabel = config.add_button_label ?? (config.cart_checkout ? '+ New Sale' : '+ New');
        const showAddButton = config.cart_checkout || config.multi_add || !config.hide_add;

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
                } else if (config.multi_add) {
                    this._renderMultiAdd(config);
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

    // Groups rows by config.multi_add_parent_field.key (e.g. recipe rows
    // by menu_item_product_id) into one displayed row per parent, listing
    // every child (ingredient) inline. Edit reopens _renderMultiAdd
    // pre-filled with the whole group; Delete removes every row in the
    // group. There is no per-child-row edit/delete in this view --
    // editing a group always means re-specifying the whole set, per the
    // module's own semantics (a recipe's ingredient list isn't a
    // collection of independent facts, it's one definition).
    async _renderGrouped(config, rows) {
        const content = document.getElementById('content');
        const parentField = config.multi_add_parent_field;
        const childRelation = config.multi_add_child_relation;
        const childKey = config.multi_add_child_key;
        const qtyKey = config.multi_add_quantity_key;

        const parentItems = await Api.list(parentField.relation.table);
        const parentLookup = Object.fromEntries(
            parentItems.map(i => [String(i.id), i[parentField.relation.label_field]])
        );
        const childItems = await Api.list(childRelation.table);
        const childLookup = Object.fromEntries(
            childItems.map(i => [String(i.id), i[childRelation.label_field]])
        );

        const groups = {};
        for (const row of rows) {
            const key = String(row[parentField.key]);
            (groups[key] ??= []).push(row);
        }

        const bodyRows = Object.entries(groups).map(([parentId, groupRows]) => {
            const parentLabel = parentLookup[parentId] ?? parentId;
            const ingredientsText = groupRows.map(r => {
                const childLabel = childLookup[String(r[childKey])] ?? r[childKey];
                return `${childLabel} (${r[qtyKey]})`;
            }).join(', ');
            const ids = groupRows.map(r => r.id).join(',');
            return `<tr>
                <td>${parentLabel}</td>
                <td>${ingredientsText}</td>
                <td>
                    <button data-action="edit-group" data-parent="${parentId}" data-ids="${ids}">Edit</button>
                    <button data-action="delete-group" data-ids="${ids}">Delete</button>
                </td>
            </tr>`;
        }).join('');

        content.innerHTML = `
            <h2 class="hero-title">${config.label}</h2>
            <button id="add-new">${config.add_button_label ?? '+ New'}</button>
            <table>
                <thead><tr><th>${parentField.label}</th><th>Ingredients</th><th></th></tr></thead>
                <tbody>${bodyRows}</tbody>
            </table>
        `;

        content.querySelector('#add-new').addEventListener('click', () => this._renderMultiAdd(config));

        content.querySelectorAll('[data-action="edit-group"]').forEach(btn => {
            btn.addEventListener('click', () => {
                const parentId = btn.dataset.parent;
                const ids = btn.dataset.ids.split(',').map(s => Number(s));
                const groupRows = rows.filter(r => ids.includes(r.id));
                this._renderMultiAdd(config, { parentValue: parentId, rows: groupRows });
            });
        });

        content.querySelectorAll('[data-action="delete-group"]').forEach(btn => {
            btn.addEventListener('click', async () => {
                if (!confirm('Delete this recipe?')) return;
                const ids = btn.dataset.ids.split(',');
                await Promise.all(ids.map(id => Api.remove(config.table, id)));
                this.showModule(config.table);
            });
        });
    },

    // Add or replace a whole parent+children set in one screen (e.g. a
    // menu item and all of its ingredients). Each child row becomes one
    // POST to the generic create endpoint on submit -- these are
    // independent CRUD creates, not one atomic operation, since the
    // rows themselves have no transactional relationship to each other.
    // When editingGroup is passed, all of its existing rows are deleted
    // first, then the freshly-specified set is created -- editing always
    // means re-specifying the whole set, never patching one child row.
    async _renderMultiAdd(config, editingGroup = null) {
        const content = document.getElementById('content');
        const parentField = config.multi_add_parent_field;
        const childRelation = config.multi_add_child_relation;
        const childKey = config.multi_add_child_key;
        const qtyKey = config.multi_add_quantity_key;
        const qtyLabel = config.multi_add_quantity_label ?? 'Quantity';

        const parentOptions = await fetch(`/api/${parentField.relation.table}`).then(r => r.json());
        const childOptions = await fetch(`/api/${childRelation.table}`).then(r => r.json());

        const parentOpts = parentOptions.map(p => {
            const selected = editingGroup && String(p.id) === String(editingGroup.parentValue) ? 'selected' : '';
            return `<option value="${p.id}" ${selected}>${p[parentField.relation.label_field]}</option>`;
        }).join('');

        const childOptsHtml = (selectedValue) => childOptions.map(p =>
            `<option value="${p.id}" ${String(p.id) === String(selectedValue) ? 'selected' : ''}>${p[childRelation.label_field]}</option>`
        ).join('');

        let rowCount = 0;

        const addRow = (prefill = null) => {
            rowCount += 1;
            const childValue = prefill ? prefill[childKey] : null;
            const qtyValue = prefill ? prefill[qtyKey] : '';
            const rowHtml = `
                <div class="multi-add-row" data-row="${rowCount}">
                    <select data-child>${childOptsHtml(childValue)}</select>
                    <input type="number" data-qty placeholder="${qtyLabel}" min="1" value="${qtyValue}" required>
                    <button type="button" data-remove-row>Remove</button>
                </div>`;
            content.querySelector('#multi-add-rows').insertAdjacentHTML('beforeend', rowHtml);
            content.querySelector(`[data-row="${rowCount}"] [data-remove-row]`)
                .addEventListener('click', (e) => e.target.closest('.multi-add-row').remove());
        };

        content.innerHTML = `
            <h2 class="hero-title">${editingGroup ? 'Edit' : 'New'} ${config.label.replace(/s$/, '')}</h2>
            <label>${parentField.label}
                <select id="multi-add-parent" required>${parentOpts}</select>
            </label>
            <h2 class="hero-title">Ingredients</h2>
            <div id="multi-add-rows"></div>
            <button type="button" id="add-row">+ Add Ingredient</button>
            <div class="error" id="multi-add-error"></div>
            <button id="multi-add-submit">Save</button>
            <button type="button" id="cancel">Cancel</button>
        `;

        if (editingGroup && editingGroup.rows.length > 0) {
            editingGroup.rows.forEach(r => addRow(r));
        } else {
            addRow();
        }

        content.querySelector('#add-row').addEventListener('click', () => addRow());
        content.querySelector('#cancel').addEventListener('click', () => this.showModule(config.table));

        content.querySelector('#multi-add-submit').addEventListener('click', async () => {
            const parentValue = content.querySelector('#multi-add-parent').value;
            const rowEls = Array.from(content.querySelectorAll('.multi-add-row'));

            if (rowEls.length === 0) {
                content.querySelector('#multi-add-error').textContent = 'Add at least one ingredient';
                return;
            }

            if (editingGroup) {
                await Promise.all(editingGroup.rows.map(r => Api.remove(config.table, r.id)));
            }

            const errors = [];
            for (const rowEl of rowEls) {
                const childValue = rowEl.querySelector('[data-child]').value;
                const qtyValue = rowEl.querySelector('[data-qty]').value;
                if (!qtyValue) continue;

                const payload = {
                    [parentField.key]: parentValue,
                    [childKey]: childValue,
                    [qtyKey]: qtyValue,
                };
                const result = await Api.create(config.table, payload);
                if (!result.ok) {
                    errors.push(result.body.error ?? 'Save failed');
                }
            }

            if (errors.length > 0) {
                content.querySelector('#multi-add-error').textContent = errors.join('; ');
                return;
            }
            this.showModule(config.table);
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
        const cart = {}; // product_id (string) -> { product, quantity, override }
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
            const rows = entries.map(e => {
                const overStock = e.quantity > e.product.stock_quantity;
                const needsOverride = overStock && !e.override;
                let stockNote = '';
                if (needsOverride) {
                    stockNote = `<br><span class="error" style="display:block;">Only ${e.product.stock_quantity} in stock. <button data-override="${e.product.id}">Override</button></span>`;
                } else if (overStock && e.override) {
                    stockNote = `<br><span style="color:var(--accent);">Override applied -- stock will go negative</span>`;
                }
                return `
                    <tr>
                        <td>${e.product.name}${stockNote}</td>
                        <td>
                            <button data-decrement="${e.product.id}">-</button>
                            ${e.quantity}
                            <button data-increment="${e.product.id}">+</button>
                        </td>
                        <td>${fmt(e.product.price)}</td>
                        <td>${fmt(e.product.price * e.quantity)}</td>
                        <td><button data-remove="${e.product.id}">Remove</button></td>
                    </tr>
                `;
            }).join('');
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
            content.querySelectorAll('[data-override]').forEach(btn => {
                btn.addEventListener('click', () => {
                    cart[btn.dataset.override].override = true;
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
                    cart[id] = { product: products.find(p => String(p.id) === id), quantity: 1, override: false };
                }
                refreshCart();
            });
        });

        content.querySelector('#cancel').addEventListener('click', () => this.showModule(config.table));

        content.querySelector('#checkout').addEventListener('click', async () => {
            const entries = Object.values(cart);
            if (entries.length === 0) {
                content.querySelector('#cart-error').textContent = 'Cart is empty';
                return;
            }

            const unresolved = entries.filter(e => e.quantity > e.product.stock_quantity && !e.override);
            if (unresolved.length > 0) {
                content.querySelector('#cart-error').textContent =
                    `Resolve stock warnings before checkout: ${unresolved.map(e => e.product.name).join(', ')}`;
                return;
            }

            const items = entries.map(e => ({
                product_id: e.product.id,
                quantity: e.quantity,
                override: !!e.override,
            }));
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