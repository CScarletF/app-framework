-- audit_log: append-only record of every mutation (see core/backend/audit.py).
-- username is TEXT resolved at the time of the action, not a reference to
-- app_user, so a deactivated or renamed account never corrupts history.
-- No created_by/updated_by and no updated_at: rows are never updated, and
-- the username column IS the attribution.
CREATE TABLE audit_log (
    id serial PRIMARY KEY,
    username text NOT NULL,
    action text NOT NULL CHECK (action IN ('create', 'update', 'delete', 'void', 'override')),
    table_name text NOT NULL,
    row_id integer,
    detail text,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- Append-only guard: the database itself refuses to change or remove log
-- rows, regardless of which code path or role attempts it. The app role
-- is granted UPDATE/DELETE like every other module table, so this trigger,
-- not the grant, is what enforces it. A superuser can still drop the
-- trigger; this is tamper-resistant against application bugs, not against
-- a database administrator.
CREATE OR REPLACE FUNCTION audit_log_block_mutation()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_log_no_update_delete
    BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW
    EXECUTE FUNCTION audit_log_block_mutation();

CREATE TRIGGER audit_log_no_truncate
    BEFORE TRUNCATE ON audit_log
    FOR EACH STATEMENT
    EXECUTE FUNCTION audit_log_block_mutation();
