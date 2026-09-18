CREATE TABLE recipe (
    id serial PRIMARY KEY,
    menu_item_product_id integer NOT NULL REFERENCES product(id),
    ingredient_product_id integer NOT NULL REFERENCES product(id),
    quantity_required integer NOT NULL CHECK (quantity_required > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT recipe_no_self_reference CHECK (menu_item_product_id <> ingredient_product_id),
    CONSTRAINT recipe_unique_pair UNIQUE (menu_item_product_id, ingredient_product_id)
);

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER recipe_updated_at
    BEFORE UPDATE ON recipe
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();