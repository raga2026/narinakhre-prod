"""Catalogues: curated, shareable sets of products with their own public
link (/catalogue/<slug>), separate from the main product listing (e.g. to
send a curated set to one wholesale buyer). Additive -- doesn't touch
products/order_shipping. Mirrors export_orders.py's structure: table SQL +
init function + data-access functions taking a `db` (SupabaseDB) argument.

catalogue_products.category is free TEXT rather than an FK, matching how
products.category/sub_category already work in this codebase (plain TEXT
columns, not a categories_id FK) -- see admin_coupons' own
"SELECT DISTINCT category FROM products" dropdown for the same pattern.
"""
import re

CATALOGUE_TABLES_SQL = [
    '''CREATE TABLE IF NOT EXISTS catalogues (
        id BIGSERIAL PRIMARY KEY,
        slug TEXT NOT NULL UNIQUE,
        title TEXT NOT NULL,
        description TEXT,
        is_active INTEGER NOT NULL DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''',
    '''CREATE TABLE IF NOT EXISTS catalogue_products (
        id BIGSERIAL PRIMARY KEY,
        catalogue_id BIGINT NOT NULL REFERENCES catalogues(id) ON DELETE CASCADE,
        category TEXT,
        model_number_or_name TEXT NOT NULL,
        display_order INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''',
    '''CREATE TABLE IF NOT EXISTS catalogue_product_images (
        id BIGSERIAL PRIMARY KEY,
        catalogue_product_id BIGINT NOT NULL REFERENCES catalogue_products(id) ON DELETE CASCADE,
        image_url TEXT NOT NULL,
        display_order INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''',
]

CATALOGUE_INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_catalogue_products_catalogue_id ON catalogue_products (catalogue_id)",
    "CREATE INDEX IF NOT EXISTS idx_catalogue_product_images_catalogue_product_id "
    "ON catalogue_product_images (catalogue_product_id)",
]


def initialize_catalogue_tables_if_needed(client):
    for sql in CATALOGUE_TABLES_SQL:
        try:
            client.rpc('execute_sql', {'query': sql}).execute()
        except Exception as e:
            print(f'Catalogue tables init warning (may already exist): {e}')
    for sql in CATALOGUE_INDEXES_SQL:
        try:
            client.rpc('execute_sql', {'query': sql}).execute()
        except Exception as e:
            print(f'Catalogue tables index warning (may already exist): {e}')


def generate_slug(title):
    slug = re.sub(r'[^a-z0-9]+', '-', (title or '').lower()).strip('-')
    return slug or 'catalogue'


def get_unique_catalogue_slug(db, title, exclude_id=None):
    base = generate_slug(title)
    slug = base
    i = 1
    while True:
        if exclude_id:
            row = db.execute("SELECT id FROM catalogues WHERE slug=? AND id != ?", (slug, exclude_id)).fetchone()
        else:
            row = db.execute("SELECT id FROM catalogues WHERE slug=?", (slug,)).fetchone()
        if not row:
            return slug
        i += 1
        slug = f'{base}-{i}'


# ---------------------------------------------------------------------------
# catalogues
# ---------------------------------------------------------------------------

def create_catalogue(db, title, description=None, slug=None):
    slug = generate_slug(slug) if (slug or '').strip() else get_unique_catalogue_slug(db, title)
    db.execute(
        "INSERT INTO catalogues (slug, title, description) VALUES (?,?,?)",
        (slug, title.strip(), (description or '').strip() or None)
    )
    db.commit()
    return db.execute("SELECT * FROM catalogues WHERE slug=?", (slug,)).fetchone()


def get_catalogue(db, catalogue_id):
    return db.execute("SELECT * FROM catalogues WHERE id=?", (catalogue_id,)).fetchone()


def get_catalogue_by_slug(db, slug):
    return db.execute("SELECT * FROM catalogues WHERE slug=?", (slug,)).fetchone()


def list_catalogues(db):
    return db.execute("SELECT * FROM catalogues ORDER BY created_at DESC").fetchall()


def update_catalogue(db, catalogue_id, title=None, description=None):
    db.execute(
        "UPDATE catalogues SET title = COALESCE(?, title), description = COALESCE(?, description) WHERE id=?",
        (title, description, catalogue_id)
    )
    db.commit()


def toggle_catalogue_active(db, catalogue_id):
    db.execute(
        "UPDATE catalogues SET is_active = CASE WHEN is_active=1 THEN 0 ELSE 1 END WHERE id=?",
        (catalogue_id,)
    )
    db.commit()


def delete_catalogue(db, catalogue_id):
    db.execute("DELETE FROM catalogues WHERE id=?", (catalogue_id,))
    db.commit()


# ---------------------------------------------------------------------------
# catalogue_products
# ---------------------------------------------------------------------------

def add_catalogue_product(db, catalogue_id, category, model_number_or_name):
    row = db.execute(
        "SELECT COALESCE(MAX(display_order), -1) as max_order FROM catalogue_products WHERE catalogue_id=?",
        (catalogue_id,)
    ).fetchone()
    next_order = int((row or {}).get('max_order', -1) or -1) + 1
    db.execute(
        "INSERT INTO catalogue_products (catalogue_id, category, model_number_or_name, display_order) VALUES (?,?,?,?)",
        (catalogue_id, (category or '').strip() or None, model_number_or_name.strip(), next_order)
    )
    db.commit()
    return db.execute(
        "SELECT * FROM catalogue_products WHERE catalogue_id=? ORDER BY id DESC LIMIT 1",
        (catalogue_id,)
    ).fetchone()


def get_catalogue_product(db, catalogue_product_id):
    return db.execute("SELECT * FROM catalogue_products WHERE id=?", (catalogue_product_id,)).fetchone()


def list_catalogue_products(db, catalogue_id):
    return db.execute(
        "SELECT * FROM catalogue_products WHERE catalogue_id=? ORDER BY display_order, id",
        (catalogue_id,)
    ).fetchall()


def update_catalogue_product(db, catalogue_product_id, category=None, model_number_or_name=None):
    db.execute(
        "UPDATE catalogue_products SET category = COALESCE(?, category), "
        "model_number_or_name = COALESCE(?, model_number_or_name) WHERE id=?",
        (category, model_number_or_name, catalogue_product_id)
    )
    db.commit()


def delete_catalogue_product(db, catalogue_product_id):
    db.execute("DELETE FROM catalogue_products WHERE id=?", (catalogue_product_id,))
    db.commit()


def move_catalogue_product(db, catalogue_id, catalogue_product_id, direction):
    """direction: 'up' or 'down' -- swaps display_order with its neighbour
    in the catalogue's current ordering. No-op at either end of the list."""
    products = list_catalogue_products(db, catalogue_id)
    ids = [p['id'] for p in products]
    if catalogue_product_id not in ids:
        return
    idx = ids.index(catalogue_product_id)
    swap_idx = idx - 1 if direction == 'up' else idx + 1
    if swap_idx < 0 or swap_idx >= len(products):
        return
    a, b = products[idx], products[swap_idx]
    db.execute("UPDATE catalogue_products SET display_order=? WHERE id=?", (b['display_order'], a['id']))
    db.execute("UPDATE catalogue_products SET display_order=? WHERE id=?", (a['display_order'], b['id']))
    db.commit()


# ---------------------------------------------------------------------------
# catalogue_product_images
# ---------------------------------------------------------------------------

def add_catalogue_product_image(db, catalogue_product_id, image_url):
    row = db.execute(
        "SELECT COALESCE(MAX(display_order), -1) as max_order FROM catalogue_product_images "
        "WHERE catalogue_product_id=?",
        (catalogue_product_id,)
    ).fetchone()
    next_order = int((row or {}).get('max_order', -1) or -1) + 1
    db.execute(
        "INSERT INTO catalogue_product_images (catalogue_product_id, image_url, display_order) VALUES (?,?,?)",
        (catalogue_product_id, image_url, next_order)
    )
    db.commit()


def list_catalogue_product_images(db, catalogue_product_id):
    return db.execute(
        "SELECT * FROM catalogue_product_images WHERE catalogue_product_id=? ORDER BY display_order, id",
        (catalogue_product_id,)
    ).fetchall()


def delete_catalogue_product_image(db, image_id):
    db.execute("DELETE FROM catalogue_product_images WHERE id=?", (image_id,))
    db.commit()


def move_catalogue_product_image(db, catalogue_product_id, image_id, direction):
    images = list_catalogue_product_images(db, catalogue_product_id)
    ids = [im['id'] for im in images]
    if image_id not in ids:
        return
    idx = ids.index(image_id)
    swap_idx = idx - 1 if direction == 'up' else idx + 1
    if swap_idx < 0 or swap_idx >= len(images):
        return
    a, b = images[idx], images[swap_idx]
    db.execute("UPDATE catalogue_product_images SET display_order=? WHERE id=?", (b['display_order'], a['id']))
    db.execute("UPDATE catalogue_product_images SET display_order=? WHERE id=?", (a['display_order'], b['id']))
    db.commit()


def get_catalogue_products_with_images(db, catalogue_id):
    """list_catalogue_products() rows, each with an 'images' list of full
    catalogue_product_images rows (id, image_url, display_order, ...)
    attached -- the admin edit page needs each image's id for its
    delete/move buttons; the public page just reads image.image_url."""
    products = list_catalogue_products(db, catalogue_id)
    for p in products:
        p['images'] = list_catalogue_product_images(db, p['id'])
    return products
