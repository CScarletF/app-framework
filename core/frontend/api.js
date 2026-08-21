// api.js -- thin wrapper around fetch() for the generic CRUD API.
// Every function here works against ANY table; table-specific behavior
// belongs in a module's module.json config, never in here.

const Api = {
    async list(table) {
        const res = await fetch(`/api/${table}`);
        return res.json();
    },

    async get(table, id) {
        const res = await fetch(`/api/${table}/${id}`);
        return res.json();
    },

    async create(table, data) {
        const res = await fetch(`/api/${table}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        return { ok: res.ok, status: res.status, body: await res.json() };
    },

    async update(table, id, data) {
        const res = await fetch(`/api/${table}/${id}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        return { ok: res.ok, status: res.status, body: await res.json() };
    },

    async remove(table, id) {
        const res = await fetch(`/api/${table}/${id}`, { method: 'DELETE' });
        return { ok: res.ok, status: res.status, body: await res.json() };
    },
};
