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
                const val = c.relation ? (lookups[c.key][String(raw)] ?? raw ?? '') : (raw ?? '');
                return `<td>${val}</td>`;
            }).join('');
            return `<tr>${cells}<td>
                <button data-action="edit" data-id="${row.id}">Edit</button>
                <button data-action="delete" data-id="${row.id}">Delete</button>
            </td></tr>`;
        }).join('');

        // Generic, config-driven action buttons (e.g. Export to Excel) --
        // plain links to a backend endpoint, no module-specific JS needed.
        const actions = (config.actions ?? [])
            .map(a => `<a class="module-action" href="${a.href}">${a.label}</a>`)
            .join('');

        content.innerHTML = `
            <h2 class="hero-title">${config.label}</h2>
            <button id="add-new">+ New</button>
            ${actions}
            <table><thead><tr>${header}<th></th></tr></thead><tbody>${body}</tbody></table>
        `;

        content.querySelector('#add-new').addEventListener('click', () => this._renderForm(config));
        content.querySelectorAll('[data-action="edit"]').forEach(btn => {
            btn.addEventListener('click', async () => {
                const row = await Api.get(config.table, btn.dataset.id);
                this._renderForm(config, row);
            });
        });
        content.querySelectorAll('[data-action="delete"]').forEach(btn => {
            btn.addEventListener('click', async () => {
                if (!confirm('Delete this row?')) return;
                await Api.remove(config.table, btn.dataset.id);
                this.showModule(config.table);
            });
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