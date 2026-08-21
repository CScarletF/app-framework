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
        this._renderList(config, rows);
    },

    _renderList(config, rows) {
        const content = document.getElementById('content');
        const cols = config.columns;

        const header = cols.map(c => `<th>${c.label}</th>`).join('');
        const body = rows.map(row => {
            const cells = cols.map(c => `<td>${row[c.key] ?? ''}</td>`).join('');
            return `<tr>${cells}<td>
                <button data-action="edit" data-id="${row.id}">Edit</button>
                <button data-action="delete" data-id="${row.id}">Delete</button>
            </td></tr>`;
        }).join('');

        content.innerHTML = `
            <h2>${config.label}</h2>
            <button id="add-new">+ New</button>
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

    _renderForm(config, existing = null) {
        const content = document.getElementById('content');
        const fields = config.form.map(f => {
            const value = existing?.[f.key] ?? '';
            if (f.type === 'select') {
                const opts = f.options.map(o =>
                    `<option value="${o}" ${o === value ? 'selected' : ''}>${o}</option>`
                ).join('');
                return `<label>${f.label}<select name="${f.key}">${opts}</select></label>`;
            }
            if (f.type === 'textarea') {
                return `<label>${f.label}<textarea name="${f.key}">${value}</textarea></label>`;
            }
            return `<label>${f.label}<input name="${f.key}" type="${f.type}" value="${value}"></label>`;
        }).join('');

        content.innerHTML = `
            <h2>${existing ? 'Edit' : 'New'} ${config.label}</h2>
            <form id="module-form">
                ${fields}
                <div class="error" id="form-error"></div>
                <button type="submit">Save</button>
                <button type="button" id="cancel">Cancel</button>
            </form>
        `;

        content.querySelector('#cancel').addEventListener('click', () => this.showModule(config.table));

        content.querySelector('#module-form').addEventListener('submit', async (e) => {
            e.preventDefault();
            const data = Object.fromEntries(new FormData(e.target).entries());
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
