// app.js -- the generic engine. Modules are pure config (module.json);
// this file is the only place that knows HOW to turn that config into a
// nav entry, a list view, and a form. A module never writes its own
// render function -- if a module ever needs one, that's a deliberate
// exception, not the normal path.
//
// AUTH (active only if the backend exposes /api/auth/me):
//   - The bottom-left user area shows Log in, or the user with a menu
//     (Change password, Log out).
//   - /api/auth/me returns the role's allowed actions per table; buttons
//     the role may not use are not rendered. This is cosmetic only: the
//     backend enforces every permission on every mutating request.
//   - A 401 on any mutation (session expired) opens the login dialog as
//     an overlay, leaving the current view (e.g. a cart) intact.
//
// SECURITY: every value coming from data is HTML-escaped with esc()
// before it enters a template. Never interpolate raw data into innerHTML.

const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
));

const App = {
    modules: [],   // [{ config, cssLoaded }]
    activeTable: null,
    authEnabled: false,
    auth: { authenticated: false, username: null, role: null, permissions: {} },

    async init() {
        const manifest = await fetch('modules.json').then(r => r.json());

        for (const name of manifest.modules) {
            const config = await fetch(`modules/${name}/frontend/module.json`).then(r => r.json());
            this.modules.push({ name, config });
            await this._maybeLoadCss(name);
        }

        this.modules.sort((a, b) => (a.config.order ?? 0) - (b.config.order ?? 0));

        await this._loadAuth();
        Api.onUnauthorized = () => this._sessionLost();

        document.addEventListener('click', (e) => {
            const menu = document.getElementById('user-menu');
            if (menu && !menu.hidden && !e.target.closest('#user-area')) menu.hidden = true;
        });

        this._buildNav();
        this._renderUserArea();

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

    // --- auth state ---------------------------------------------------------

    async _loadAuth() {
        try {
            const res = await fetch('/api/auth/me');
            if (!res.ok) {          // 404: this app has no app_user module
                this.authEnabled = false;
                return;
            }
            const body = await res.json();
            this.authEnabled = true;
            this.auth = body.authenticated
                ? {
                    authenticated: true,
                    username: body.username,
                    role: body.role,
                    permissions: body.permissions ?? {},
                }
                : { authenticated: false, username: null, role: null, permissions: {} };
        } catch (_) {
            this.authEnabled = false;
        }
    },

    // Without auth every action is available (the backend is then open
    // too). With auth, only what /api/auth/me granted this role.
    can(table, action) {
        if (!this.authEnabled) return true;
        return (this.auth.permissions[table] ?? []).includes(action);
    },

    async _sessionLost() {
        await this._loadAuth();
        this._renderUserArea();
        this._showLoginModal('Login required or session expired. Log in, then retry your last action.',
            { preserveView: true });
    },

    // --- user area, login dialog, password change --------------------------------

    _renderUserArea() {
        const area = document.getElementById('user-area');
        if (!area) return;

        if (!this.authEnabled) {
            area.innerHTML = '';
            return;
        }

        if (!this.auth.authenticated) {
            area.innerHTML = `
                <button type="button" class="user-button" id="user-button">
                    <span class="user-avatar">?</span>
                    <span class="user-meta">
                        <span class="user-name">Log in</span>
                        <span class="user-role">Read-only</span>
                    </span>
                </button>`;
            area.querySelector('#user-button').addEventListener('click', () => this._showLoginModal());
            return;
        }

        area.innerHTML = `
            <div class="user-menu" id="user-menu" hidden>
                <button type="button" id="menu-password">Change password</button>
                <button type="button" id="menu-logout">Log out</button>
            </div>
            <button type="button" class="user-button" id="user-button">
                <span class="user-avatar">${esc(this.auth.username.charAt(0).toUpperCase())}</span>
                <span class="user-meta">
                    <span class="user-name">${esc(this.auth.username)}</span>
                    <span class="user-role">${esc(this.auth.role)}</span>
                </span>
            </button>`;

        const menu = area.querySelector('#user-menu');
        area.querySelector('#user-button').addEventListener('click', () => {
            menu.hidden = !menu.hidden;
        });
        area.querySelector('#menu-password').addEventListener('click', () => {
            menu.hidden = true;
            this._renderChangePassword();
        });
        area.querySelector('#menu-logout').addEventListener('click', () => {
            menu.hidden = true;
            this._logout();
        });
    },

    _showLoginModal(message = '', { preserveView = false } = {}) {
        if (document.getElementById('login-overlay')) return;

        const overlay = document.createElement('div');
        overlay.id = 'login-overlay';
        overlay.innerHTML = `
            <div class="modal-panel" role="dialog" aria-modal="true" aria-labelledby="login-title">
                <h2 class="hero-title" id="login-title">Log in</h2>
                ${message ? `<div class="notice">${esc(message)}</div>` : ''}
                <form id="login-form">
                    <label>Username
                        <input name="username" type="text" autocomplete="username" required>
                    </label>
                    <label>Password
                        <input name="password" type="password" autocomplete="current-password" required>
                    </label>
                    <div class="error" id="login-error"></div>
                    <div class="button-row">
                        <button type="submit">Log in</button>
                        <button type="button" id="login-cancel">Cancel</button>
                    </div>
                </form>
            </div>`;
        document.body.appendChild(overlay);

        function onKey(e) { if (e.key === 'Escape') close(); }
        function close() {
            overlay.remove();
            document.removeEventListener('keydown', onKey);
        }
        document.addEventListener('keydown', onKey);

        overlay.querySelector('#login-cancel').addEventListener('click', close);
        overlay.querySelector('input[name="username"]').focus();

        overlay.querySelector('#login-form').addEventListener('submit', async (e) => {
            e.preventDefault();
            const form = e.target;
            const errorEl = overlay.querySelector('#login-error');
            errorEl.textContent = '';

            const result = await Api.post(
                '/api/auth/login',
                {
                    username: form.elements.username.value,
                    password: form.elements.password.value,
                },
                { skipUnauthorized: true },
            );

            if (!result.ok) {
                errorEl.textContent = result.body.error ?? 'Login failed';
                form.elements.password.value = '';
                return;
            }

            close();
            await this._loadAuth();
            this._renderUserArea();
            // An explicit login re-renders so buttons match the new role.
            // A re-login after expiry keeps the current view untouched.
            if (!preserveView && this.activeTable) this.showModule(this.activeTable);
        });
    },

    async _logout() {
        await Api.post('/api/auth/logout', {}, { skipUnauthorized: true });
        await this._loadAuth();
        this._renderUserArea();
        if (this.activeTable) this.showModule(this.activeTable);
    },

    _renderChangePassword() {
        const content = document.getElementById('content');
        content.innerHTML = `
            <h2 class="hero-title">Change Password</h2>
            <form id="password-form">
                <label>Current password
                    <input name="current_password" type="password" autocomplete="current-password" required>
                </label>
                <label>New password
                    <input name="new_password" type="password" autocomplete="new-password" required>
                </label>
                <label>Confirm new password
                    <input name="confirm_password" type="password" autocomplete="new-password" required>
                </label>
                <div class="error" id="password-error"></div>
                <div class="notice" id="password-success" hidden></div>
                <div class="button-row">
                    <button type="submit">Save</button>
                    <button type="button" id="cancel">Back</button>
                </div>
            </form>
        `;

        content.querySelector('#cancel').addEventListener('click', () => {
            if (this.activeTable) this.showModule(this.activeTable);
        });

        content.querySelector('#password-form').addEventListener('submit', async (e) => {
            e.preventDefault();
            const form = e.target;
            const errorEl = content.querySelector('#password-error');
            const successEl = content.querySelector('#password-success');
            errorEl.textContent = '';
            successEl.hidden = true;

            const current = form.elements.current_password.value;
            const next = form.elements.new_password.value;
            if (next !== form.elements.confirm_password.value) {
                errorEl.textContent = 'New password and confirmation do not match';
                return;
            }

            const result = await Api.post('/api/auth/password', {
                current_password: current,
                new_password: next,
            });
            if (!result.ok) {
                errorEl.textContent = result.body.error ?? 'Password change failed';
                return;
            }
            form.reset();
            successEl.textContent = 'Password changed.';
            successEl.hidden = false;
        });
    },

    _readOnlyNotice() {
        return (this.authEnabled && !this.auth.authenticated)
            ? '<div class="notice">Read-only. Log in to make changes.</div>'
            : '';
    },

    // --- audit display (created_by / updated_by + timestamps) -------------------
    // Fully automatic: any list whose rows carry created_by gets Created and
    // Updated columns (username + time), with no per-module config.

    _fmtDateTime(value) {
        const d = new Date(value);
        if (Number.isNaN(d.getTime())) return String(value ?? '');
        const p = (n) => String(n).padStart(2, '0');
        return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
    },

    // id -> username. Reads are open, so this works logged out too; {} when
    // the app has no app_user module.
    async _userMap() {
        try {
            const res = await fetch('/api/app_user');
            if (!res.ok) return {};
            const users = await res.json();
            return Object.fromEntries(users.map(u => [String(u.id), u.username]));
        } catch (_) {
            return {};
        }
    },

    // A NULL author means the row predates authentication (or auth was off).
    _authorLabel(id, users) {
        if (id === null || id === undefined || id === '') return '-';
        return users[String(id)] ?? `#${id}`;
    },

    _auditCell(by, at, users) {
        const when = at ? this._fmtDateTime(at) : '';
        return `<td>${esc(this._authorLabel(by, users))}<span class="cell-sub">${esc(when)}</span></td>`;
    },

    // A grouped row (one recipe = several DB rows) shows the earliest
    // creation and the latest update among its rows.
    _groupAuditCells(groupRows, users) {
        const byTime = (key) => (a, b) => new Date(a[key]) - new Date(b[key]);
        const created = [...groupRows].sort(byTime('created_at'))[0];
        const updated = [...groupRows].sort(byTime('updated_at')).pop();
        return this._auditCell(created.created_by, created.created_at, users)
            + this._auditCell(updated.updated_by, updated.updated_at, users);
    },

    _auditRows(row, users) {
        return [
            ['Created By', this._authorLabel(row.created_by, users)],
            ['Created At', row.created_at ? this._fmtDateTime(row.created_at) : ''],
            ['Updated By', this._authorLabel(row.updated_by, users)],
            ['Updated At', row.updated_at ? this._fmtDateTime(row.updated_at) : ''],
        ].map(([label, val]) => `<tr><th>${esc(label)}</th><td>${esc(val)}</td></tr>`).join('');
    },

    // --- nav + module routing -------------------------------------------------

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
        if (config.report_view) {
            return this._renderReport(config);
        }
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

        // Void-style deletes (void_endpoint) are governed by the "void"
        // permission, plain deletes by "delete".
        const deleteAction = config.void_endpoint ? 'void' : 'delete';

        const hasAudit = rows.length > 0 && 'created_by' in rows[0];
        const users = hasAudit ? await this._userMap() : {};

        const header = cols.map(c => `<th>${esc(c.label)}</th>`).join('')
            + (hasAudit ? '<th>Created</th><th>Updated</th>' : '');
        const body = rows.map(row => {
            const cells = cols.map(c => {
                const raw = row[c.key];
                if (c.key === 'status') {
                    const isActive = raw === 'in_use';
                    return `<td><span class="status ${isActive ? 'status-active' : ''}">${esc(raw)}</span></td>`;
                }
                let val = c.relation ? (lookups[c.key][String(raw)] ?? raw ?? '') : (raw ?? '');
                if (c.format === 'number' && val !== '') {
                    val = Number(val).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
                } else if (c.format === 'datetime' && val !== '') {
                    val = this._fmtDateTime(val);
                }
                return `<td>${esc(val)}</td>`;
            }).join('');

            // Every row gets a read-only View (reads are open); Edit is
            // added for roles with update permission on modules that have
            // no custom detail screen.
            let buttons = `<button data-action="view" data-id="${esc(row.id)}">View</button>`;
            if (!config.detail_view && this.can(config.table, 'update')) {
                buttons += `<button data-action="edit" data-id="${esc(row.id)}">Edit</button>`;
            }
            if (this.can(config.table, deleteAction)) {
                buttons += `<button data-action="delete" data-id="${esc(row.id)}">Delete</button>`;
            }
            const auditCells = hasAudit
                ? this._auditCell(row.created_by, row.created_at, users)
                    + this._auditCell(row.updated_by, row.updated_at, users)
                : '';
            return `<tr>${cells}${auditCells}<td>${buttons}</td></tr>`;
        }).join('');

        const actions = (config.actions ?? [])
            .map(a => `<a class="module-action" href="${esc(a.href)}">${esc(a.label)}</a>`)
            .join('');

        const addButtonLabel = config.add_button_label ?? (config.cart_checkout ? '+ New Sale' : '+ New');
        const showAddButton =
            (config.cart_checkout || config.multi_add || !config.hide_add)
            && this.can(config.table, 'create');

        content.innerHTML = `
            <h2 class="hero-title">${esc(config.label)}</h2>
            ${this._readOnlyNotice()}
            ${showAddButton ? `<button id="add-new">${esc(addButtonLabel)}</button>` : ''}
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
                if (config.detail_view) {
                    this._renderDetail(config, row);
                } else {
                    this._renderView(config, row);
                }
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
                const result = config.void_endpoint
                    ? await Api.del(config.void_endpoint.replace('{id}', btn.dataset.id))
                    : await Api.remove(config.table, btn.dataset.id);
                if (!result.ok) {
                    if (result.status !== 401) alert(result.body.error ?? 'Delete failed');
                    return;
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
    // Editing a group deletes then recreates its rows, so it needs BOTH
    // the delete and create permissions; deleting needs delete.
    async _renderGrouped(config, rows) {
        const content = document.getElementById('content');
        const parentField = config.multi_add_parent_field;
        const childRelation = config.multi_add_child_relation;
        const childKey = config.multi_add_child_key;
        const qtyKey = config.multi_add_quantity_key;

        const canCreate = this.can(config.table, 'create');
        const canDelete = this.can(config.table, 'delete');
        const canEditGroup = canCreate && canDelete;

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

        const hasAudit = rows.length > 0 && 'created_by' in rows[0];
        const users = hasAudit ? await this._userMap() : {};

        const bodyRows = Object.entries(groups).map(([parentId, groupRows]) => {
            const parentLabel = parentLookup[parentId] ?? parentId;
            const ingredientsText = groupRows.map(r => {
                const childLabel = childLookup[String(r[childKey])] ?? r[childKey];
                return `${childLabel} (${r[qtyKey]})`;
            }).join(', ');
            const ids = groupRows.map(r => r.id).join(',');

            let buttons = '';
            if (canEditGroup) {
                buttons += `<button data-action="edit-group" data-parent="${esc(parentId)}" data-ids="${esc(ids)}">Edit</button>`;
            }
            if (canDelete) {
                buttons += `<button data-action="delete-group" data-ids="${esc(ids)}">Delete</button>`;
            }
            return `<tr>
                <td>${esc(parentLabel)}</td>
                <td>${esc(ingredientsText)}</td>
                ${hasAudit ? this._groupAuditCells(groupRows, users) : ''}
                <td>${buttons}</td>
            </tr>`;
        }).join('');

        content.innerHTML = `
            <h2 class="hero-title">${esc(config.label)}</h2>
            ${this._readOnlyNotice()}
            ${canCreate ? `<button id="add-new">${esc(config.add_button_label ?? '+ New')}</button>` : ''}
            <table>
                <thead><tr><th>${esc(parentField.label)}</th><th>Ingredients</th>${hasAudit ? '<th>Created</th><th>Updated</th>' : ''}<th></th></tr></thead>
                <tbody>${bodyRows}</tbody>
            </table>
        `;

        if (canCreate) {
            content.querySelector('#add-new').addEventListener('click', () => this._renderMultiAdd(config));
        }

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
                const results = await Promise.all(ids.map(id => Api.remove(config.table, id)));
                const failed = results.find(r => !r.ok);
                if (failed) {
                    if (failed.status !== 401) alert(failed.body.error ?? 'Delete failed');
                    return;
                }
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
    // If any delete fails, creation is NOT attempted (it would duplicate
    // the rows that survived).
    async _renderMultiAdd(config, editingGroup = null) {
        const content = document.getElementById('content');
        const parentField = config.multi_add_parent_field;
        const childRelation = config.multi_add_child_relation;
        const childKey = config.multi_add_child_key;
        const qtyKey = config.multi_add_quantity_key;
        const qtyLabel = config.multi_add_quantity_label ?? 'Quantity';

        const parentOptions = await Api.list(parentField.relation.table);
        const childOptions = await Api.list(childRelation.table);

        const parentOpts = parentOptions.map(p => {
            const selected = editingGroup && String(p.id) === String(editingGroup.parentValue) ? 'selected' : '';
            return `<option value="${esc(p.id)}" ${selected}>${esc(p[parentField.relation.label_field])}</option>`;
        }).join('');

        const childOptsHtml = (selectedValue) => childOptions.map(p =>
            `<option value="${esc(p.id)}" ${String(p.id) === String(selectedValue) ? 'selected' : ''}>${esc(p[childRelation.label_field])}</option>`
        ).join('');

        let rowCount = 0;

        const addRow = (prefill = null) => {
            rowCount += 1;
            const childValue = prefill ? prefill[childKey] : null;
            const qtyValue = prefill ? prefill[qtyKey] : '';
            const rowHtml = `
                <div class="multi-add-row" data-row="${rowCount}">
                    <select data-child>${childOptsHtml(childValue)}</select>
                    <input type="number" data-qty placeholder="${esc(qtyLabel)}" min="1" value="${esc(qtyValue)}" required>
                    <button type="button" data-remove-row>Remove</button>
                </div>`;
            content.querySelector('#multi-add-rows').insertAdjacentHTML('beforeend', rowHtml);
            content.querySelector(`[data-row="${rowCount}"] [data-remove-row]`)
                .addEventListener('click', (e) => e.target.closest('.multi-add-row').remove());
        };

        content.innerHTML = `
            <h2 class="hero-title">${editingGroup ? 'Edit' : 'New'} ${esc(config.label.replace(/s$/, ''))}</h2>
            <label>${esc(parentField.label)}
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
            const errorEl = content.querySelector('#multi-add-error');
            const parentValue = content.querySelector('#multi-add-parent').value;
            const rowEls = Array.from(content.querySelectorAll('.multi-add-row'));

            if (rowEls.length === 0) {
                errorEl.textContent = 'Add at least one ingredient';
                return;
            }

            if (editingGroup) {
                const deletions = await Promise.all(
                    editingGroup.rows.map(r => Api.remove(config.table, r.id))
                );
                const failed = deletions.find(d => !d.ok);
                if (failed) {
                    errorEl.textContent = failed.body.error ?? 'Could not replace the existing set';
                    return;
                }
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
                errorEl.textContent = errors.join('; ');
                return;
            }
            this.showModule(config.table);
        });
    },

    // Read-only cross-sale aggregation view (e.g. the reporting module).
    // Fetches from config.report_endpoint rather than /api/<table> -- a
    // report module owns no table of its own. Optional date-range
    // filters (config.report_filters) re-fetch on "Apply" rather than
    // client-side filtering, since the aggregation itself happens in
    // SQL. config.report_summary_key, if set, sums that column across
    // all returned rows into a footer total.
    async _renderReport(config) {
        const content = document.getElementById('content');
        const filters = config.report_filters ?? [];
        const fmt = (n) => Number(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

        const filterLabels = { start_date: 'From', end_date: 'To' };
        const filterInputsHtml = filters.map(f =>
            `<label>${esc(filterLabels[f] ?? f)} <input type="date" data-filter="${esc(f)}"></label>`
        ).join('');

        const fetchAndRender = async () => {
            const params = new URLSearchParams();
            filters.forEach(f => {
                const el = content.querySelector(`[data-filter="${f}"]`);
                if (el && el.value) params.set(f, el.value);
            });
            const url = params.toString() ? `${config.report_endpoint}?${params}` : config.report_endpoint;
            const rows = await fetch(url).then(r => r.json());

            const cols = config.columns;
            const header = cols.map(c => `<th>${esc(c.label)}</th>`).join('');
            const body = rows.map(row => {
                const cells = cols.map(c => {
                    let val = row[c.key] ?? '';
                    if (c.format === 'number' && val !== '') {
                        val = fmt(val);
                    }
                    return `<td>${esc(val)}</td>`;
                }).join('');
                return `<tr>${cells}</tr>`;
            }).join('');

            let summaryHtml = '';
            if (config.report_summary_key) {
                const total = rows.reduce((sum, r) => sum + Number(r[config.report_summary_key] ?? 0), 0);
                summaryHtml = `<p><strong>${esc(config.report_summary_label ?? 'Total')}: ${esc(fmt(total))}</strong></p>`;
            }

            content.querySelector('#report-table-area').innerHTML = `
                <table><thead><tr>${header}</tr></thead><tbody>${body}</tbody></table>
                ${summaryHtml}
            `;
        };

        content.innerHTML = `
            <h2 class="hero-title">${esc(config.label)}</h2>
            <div id="report-filters">
                ${filterInputsHtml}
                ${filters.length > 0 ? '<button id="report-apply">Apply</button>' : ''}
            </div>
            <div id="report-table-area"></div>
        `;

        if (filters.length > 0) {
            content.querySelector('#report-apply').addEventListener('click', fetchAndRender);
        }

        await fetchAndRender();
    },

    // Read-only record screen for modules without a custom detail view:
    // every form field and list column, then the audit block. Password
    // fields are never shown (and hashed columns never reach the client).
    async _renderView(config, row) {
        const content = document.getElementById('content');
        const users = await this._userMap();

        const fields = [];
        const seen = new Set();
        for (const f of config.form ?? []) {
            if (f.type === 'password') continue;
            fields.push({
                key: f.key,
                label: f.label,
                relation: f.relation,
                format: f.format ?? (f.type === 'datetime-local' ? 'datetime' : undefined),
            });
            seen.add(f.key);
        }
        for (const c of config.columns ?? []) {
            if (seen.has(c.key)) continue;
            fields.push({ key: c.key, label: c.label, relation: c.relation, format: c.format });
        }

        const lookups = {};
        for (const f of fields.filter(f => f.relation)) {
            const items = await Api.list(f.relation.table);
            lookups[f.key] = Object.fromEntries(
                items.map(i => [String(i.id), i[f.relation.label_field]])
            );
        }

        const bodyRows = fields.map(f => {
            const raw = row[f.key];
            let val = f.relation ? (lookups[f.key][String(raw)] ?? raw ?? '') : (raw ?? '');
            if (f.format === 'number' && val !== '') {
                val = Number(val).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
            } else if (f.format === 'datetime' && val !== '') {
                val = this._fmtDateTime(val);
            }
            return `<tr><th>${esc(f.label)}</th><td>${esc(val)}</td></tr>`;
        }).join('');
        const auditRows = 'created_by' in row ? this._auditRows(row, users) : '';

        content.innerHTML = `
            <h2 class="hero-title">${esc(config.label)} Detail</h2>
            <table>${bodyRows}${auditRows}</table>
            <button id="back">Back</button>
        `;

        content.querySelector('#back').addEventListener('click', () => this.showModule(config.table));
    },

    async _renderDetail(config, row) {
        const content = document.getElementById('content');
        const endpoint = config.detail_endpoint.replace('{id}', row.id);
        const items = await fetch(endpoint).then(r => r.json());
        const users = await this._userMap();

        const summaryRows = (config.detail_summary ?? []).map(f => {
            let val = row[f.key] ?? '';
            if (f.format === 'number' && val !== '') {
                val = Number(val).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
            } else if (f.format === 'datetime' && val !== '') {
                val = this._fmtDateTime(val);
            }
            return `<tr><th>${esc(f.label)}</th><td>${esc(val)}</td></tr>`;
        }).join('') + ('created_by' in row ? this._auditRows(row, users) : '');

        const itemRows = items.map(i => {
            const subtotal = (i.quantity * i.unit_price_at_sale).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
            const unitPrice = Number(i.unit_price_at_sale).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
            return `<tr><td>${esc(i.product_name)}</td><td>${esc(i.quantity)}</td><td>${unitPrice}</td><td>${subtotal}</td></tr>`;
        }).join('');

        content.innerHTML = `
            <h2 class="hero-title">${esc(config.label)} Detail</h2>
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
        const recipes = await Api.list('recipe');
        const cart = {}; // product_id (string) -> { product, quantity, override }
        const paymentMethods = config.payment_methods ?? ['cash'];
        const fmt = (n) => Number(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
        const canOverride = this.can(config.table, 'override');

        // productId -> [{ ingredient_product_id, quantity_required }, ...]
        const recipesByMenuItem = {};
        recipes.forEach(r => {
            const key = String(r.menu_item_product_id);
            (recipesByMenuItem[key] ??= []).push(r);
        });
        const productById = Object.fromEntries(products.map(p => [String(p.id), p]));

        // What a given quantity of productId ACTUALLY decrements --
        // its ingredients if it has recipe rows (a menu item), itself
        // otherwise. Mirrors sale/routes.py's _resolve_stock_impact,
        // but per cart LINE only (not aggregated across lines sharing
        // an ingredient) -- same simplification the backend's own
        // override handling already accepts, kept consistent here.
        const resolveLineImpact = (productId, quantity) => {
            const ingredients = recipesByMenuItem[String(productId)];
            if (ingredients && ingredients.length > 0) {
                return ingredients.map(r => ({
                    productId: r.ingredient_product_id,
                    needed: r.quantity_required * quantity,
                }));
            }
            return [{ productId, needed: quantity }];
        };

        // Returns the impact entries that exceed currently-known stock
        // for a cart line -- empty array means the line is fully
        // coverable as-is, whether it's a plain item or a recipe-based
        // one.
        const shortagesForLine = (e) => resolveLineImpact(e.product.id, e.quantity)
            .map(imp => {
                const p = productById[String(imp.productId)];
                const available = p ? p.stock_quantity : 0;
                return { name: p ? p.name : `#${imp.productId}`, needed: imp.needed, available };
            })
            .filter(s => s.needed > s.available);

        const renderProductList = () => {
            const rows = products.map(p => `
                <tr>
                    <td>${esc(p.category)}</td>
                    <td>${esc(p.name)}</td>
                    <td>${fmt(p.price)}</td>
                    <td>${esc(p.stock_quantity)}</td>
                    <td><button data-add="${esc(p.id)}">Add</button></td>
                </tr>
            `).join('');
            return `<table><thead><tr><th>Category</th><th>Name</th><th>Price</th><th>Stock</th><th></th></tr></thead><tbody>${rows}</tbody></table>`;
        };

        const renderCartTable = () => {
            const entries = Object.values(cart);
            const rows = entries.map(e => {
                const shortages = shortagesForLine(e);
                const needsOverride = shortages.length > 0 && !e.override;
                let stockNote = '';
                if (needsOverride) {
                    const detail = shortages.map(s => `${esc(s.name)}: need ${esc(s.needed)}, have ${esc(s.available)}`).join('; ');
                    const action = canOverride
                        ? `<button data-override="${esc(e.product.id)}">Override</button>`
                        : 'An admin must override or reduce the quantity.';
                    stockNote = `<br><span class="error" style="display:block;">Insufficient stock -- ${detail}. ${action}</span>`;
                } else if (shortages.length > 0 && e.override) {
                    stockNote = `<br><span style="color:var(--accent);">Override applied -- stock will go negative</span>`;
                }
                return `
                    <tr>
                        <td>${esc(e.product.name)}${stockNote}</td>
                        <td>
                            <button data-decrement="${esc(e.product.id)}">-</button>
                            ${e.quantity}
                            <button data-increment="${esc(e.product.id)}">+</button>
                        </td>
                        <td>${fmt(e.product.price)}</td>
                        <td>${fmt(e.product.price * e.quantity)}</td>
                        <td><button data-remove="${esc(e.product.id)}">Remove</button></td>
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
                    ${paymentMethods.map(m => `<option value="${esc(m)}">${esc(m)}</option>`).join('')}
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

            const unresolved = entries.filter(e => shortagesForLine(e).length > 0 && !e.override);
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

            const res = await Api.post(config.checkout_endpoint, { payment_method, items });

            if (!res.ok) {
                content.querySelector('#cart-error').textContent = res.body.error ?? 'Checkout failed';
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
                    `<option value="${esc(i.id)}" ${String(i.id) === String(value) ? 'selected' : ''}>${esc(i[f.relation.label_field])}</option>`
                ).join('');
                return `<label>${esc(f.label)}<select name="${esc(f.key)}" ${req}>${opts}</select></label>`;
            }
            if (f.type === 'combo') {
                const distinctValues = await fetch(`/api/${config.table}/distinct/${f.key}`).then(r => r.json());
                const datalistId = `datalist-${f.key}`;
                const opts = distinctValues.map(v => `<option value="${esc(v)}"></option>`).join('');
                return `<label>${esc(f.label)}
                    <input name="${esc(f.key)}" type="text" value="${esc(value)}" list="${esc(datalistId)}" ${req}>
                    <datalist id="${esc(datalistId)}">${opts}</datalist>
                </label>`;
            }
            if (f.type === 'select') {
                // String() on both sides: booleans from the API (true/false)
                // must match the "true"/"false" option strings, otherwise an
                // edit form silently preselects the first option.
                const opts = f.options.map(o =>
                    `<option value="${esc(o)}" ${String(o) === String(value) ? 'selected' : ''}>${esc(o)}</option>`
                ).join('');
                return `<label>${esc(f.label)}<select name="${esc(f.key)}" ${req}>${opts}</select></label>`;
            }
            if (f.type === 'textarea') {
                return `<label>${esc(f.label)}<textarea name="${esc(f.key)}" ${req}>${esc(value)}</textarea></label>`;
            }
            if (f.type === 'password') {
                // Never prefilled. On edit, leaving it blank keeps the
                // current password (empty fields are dropped on submit).
                const placeholder = existing ? 'Leave blank to keep current' : '';
                return `<label>${esc(f.label)}<input name="${esc(f.key)}" type="password" autocomplete="new-password" placeholder="${esc(placeholder)}" ${req}></label>`;
            }
            return `<label>${esc(f.label)}<input name="${esc(f.key)}" type="${esc(f.type)}" value="${esc(value)}" ${req}></label>`;
        }));

        content.innerHTML = `
            <h2 class="hero-title">${existing ? 'Edit' : 'New'} ${esc(config.label)}</h2>
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
                const errorEl = content.querySelector('#form-error');
                if (errorEl) errorEl.textContent = result.body.error ?? 'Save failed';
                return;
            }
            this.showModule(config.table);
        });
    },
};

App.init();
