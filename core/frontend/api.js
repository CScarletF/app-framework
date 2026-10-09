// api.js -- thin wrapper around fetch() for the generic CRUD API.
// Every function here works against ANY table; table-specific behavior
// belongs in a module's module.json config, never in here.
//
// Every mutating request goes through Api.request, which:
//   - sends the X-Requested-With header the backend requires (CSRF defense),
//   - parses the body defensively (a proxy error page is not JSON),
//   - fires Api.onUnauthorized on HTTP 401, unless the caller opts out
//     (login itself must not trigger the "session lost" flow).
// Reads (list/get) stay plain GETs: they never need login.

const Api = {
    onUnauthorized: null,   // set by app.js

    async request(method, url, data, { skipUnauthorized = false } = {}) {
        const headers = { 'X-Requested-With': 'app-framework' };
        const options = { method, headers };
        if (data !== undefined) {
            headers['Content-Type'] = 'application/json';
            options.body = JSON.stringify(data);
        }

        const res = await fetch(url, options);
        let body;
        try {
            body = await res.json();
        } catch (_) {
            body = { error: `Request failed (HTTP ${res.status})` };
        }

        if (res.status === 401 && !skipUnauthorized && this.onUnauthorized) {
            this.onUnauthorized();
        }
        return { ok: res.ok, status: res.status, body };
    },

    async list(table) {
        const res = await fetch(`/api/${table}`);
        return res.json();
    },

    async get(table, id) {
        const res = await fetch(`/api/${table}/${id}`);
        return res.json();
    },

    create(table, data) {
        return this.request('POST', `/api/${table}`, data);
    },

    update(table, id, data) {
        return this.request('PUT', `/api/${table}/${id}`, data);
    },

    remove(table, id) {
        return this.request('DELETE', `/api/${table}/${id}`);
    },

    // For custom module endpoints (checkout, void, auth).
    post(url, data, options) {
        return this.request('POST', url, data ?? {}, options);
    },

    del(url, options) {
        return this.request('DELETE', url, undefined, options);
    },
};
