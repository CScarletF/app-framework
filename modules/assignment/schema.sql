CREATE TABLE assignment (
    id serial PRIMARY KEY,
    equipment_id integer NOT NULL REFERENCES equipment(id),
    assigned_to text NOT NULL,
    assigned_at timestamptz NOT NULL DEFAULT now(),
    released_at timestamptz,
    notes text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- Redefined here (CREATE OR REPLACE, safe/idempotent) rather than assumed
-- to already exist -- matches modules/_template's own pattern, since
-- sync_tables.py may run this module's schema.sql before equipment's if
-- --all iteration order isn't guaranteed.
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER assignment_updated_at
    BEFORE UPDATE ON assignment
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
