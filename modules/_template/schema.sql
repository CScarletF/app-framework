-- Copy this folder to modules/<your_module_name>/, then:
-- 1. Rename this table and add your real columns below.
-- 2. Update table.json to match (table name, required fields, allowed_values).
-- 3. Update frontend/module.json to match (columns, form fields).
-- 4. Run: python core/backend/sync_tables.py --module=<your_module_name> --apply-schema
--    This applies this file to the DB and generates table_core.py for you --
--    you never hand-write table_core.py.

CREATE TABLE REPLACE_WITH_TABLE_NAME (
    id serial PRIMARY KEY,
    name text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER REPLACE_WITH_TABLE_NAME_updated_at
    BEFORE UPDATE ON REPLACE_WITH_TABLE_NAME
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
