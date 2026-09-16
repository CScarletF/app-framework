CREATE TABLE sale_item (
    id serial PRIMARY KEY,
    sale_id integer NOT NULL REFERENCES sale(id),
    product_id integer NOT NULL REFERENCES product(id),
    quantity integer NOT NULL CHECK (quantity > 0),
    unit_price_at_sale numeric(10,2) NOT NULL CHECK (unit_price_at_sale >= 0),
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

CREATE TRIGGER sale_item_updated_at
    BEFORE UPDATE ON sale_item
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();