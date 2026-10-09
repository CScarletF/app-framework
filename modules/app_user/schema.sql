CREATE TABLE app_user (
    id serial PRIMARY KEY,
    username text NOT NULL UNIQUE CHECK (username <> '' AND username = btrim(username)),
    password_hash text NOT NULL,
    role text NOT NULL CHECK (role IN ('admin', 'staff')),
    active boolean NOT NULL DEFAULT true,
    failed_login_count integer NOT NULL DEFAULT 0,
    locked_until timestamptz,
    created_by integer REFERENCES app_user(id),
    updated_by integer REFERENCES app_user(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- Idempotent redefinition, same pattern as every other module's schema.sql.
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER app_user_updated_at
    BEFORE UPDATE ON app_user
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Last-admin guard: the DB itself refuses to demote or deactivate the only
-- remaining active admin, regardless of which code path attempts it.
-- ERRCODE check_violation (23514) is deliberate: it surfaces as an
-- IntegrityError, which crud.py already maps to a clean 409 instead of a 500.
CREATE OR REPLACE FUNCTION app_user_guard_last_admin()
RETURNS TRIGGER AS $$
BEGIN
    IF OLD.role = 'admin' AND OLD.active
       AND (NEW.role <> 'admin' OR NOT NEW.active)
       AND NOT EXISTS (
           SELECT 1 FROM app_user
           WHERE role = 'admin' AND active AND id <> OLD.id
       ) THEN
        RAISE EXCEPTION 'Cannot demote or deactivate the last active admin'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER app_user_guard_last_admin
    BEFORE UPDATE ON app_user
    FOR EACH ROW
    EXECUTE FUNCTION app_user_guard_last_admin();
