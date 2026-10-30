CREATE TABLE sale (
    id serial PRIMARY KEY,
    total numeric(10,2) NOT NULL CHECK (total >= 0),
    payment_method text NOT NULL,
    sold_at timestamptz NOT NULL DEFAULT now(),
    -- Void state: a voided sale is kept and tagged, never removed by the
    -- void itself. NULL voided_at means active. voided_by is nullable for
    -- the same reason created_by is (auth disabled / no author).
    voided_at timestamptz,
    voided_by integer REFERENCES app_user(id),
    -- Audit attribution. Nullable: rows created before authentication
    -- existed (or with it disabled) have no author.
    created_by integer REFERENCES app_user(id),
    updated_by integer REFERENCES app_user(id),
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

CREATE TRIGGER sale_updated_at
    BEFORE UPDATE ON sale
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
