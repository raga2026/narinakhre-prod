import sqlite3
import os

def migrate_quotes_table(conn):
    cur = conn.cursor()
    # Drop old quotes table if exists
    cur.execute("DROP TABLE IF EXISTS quotes")
    # Create new quotes table
    cur.execute("""
        CREATE TABLE quotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id TEXT NOT NULL,
            name TEXT NOT NULL,
            whatsapp TEXT NOT NULL,
            email TEXT,
            items_json TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    print("Quotes table migrated to new schema.")

def migrate_products_table(conn):
    cur = conn.cursor()
    # Check columns
    cur.execute("PRAGMA table_info(products)")
    cols = [row[1] for row in cur.fetchall()]
    # Add missing columns (backward compatible)
    if 'description' not in cols:
        cur.execute("ALTER TABLE products ADD COLUMN description TEXT")
        print("Added 'description' column to products.")
    if 'name' not in cols:
        cur.execute("ALTER TABLE products ADD COLUMN name TEXT")
        print("Added 'name' column to products.")
    if 'category' not in cols:
        cur.execute("ALTER TABLE products ADD COLUMN category TEXT")
        print("Added 'category' column to products.")
    if 'material' not in cols:
        cur.execute("ALTER TABLE products ADD COLUMN material TEXT")
        print("Added 'material' column to products.")
    if 'retail_price' not in cols:
        cur.execute("ALTER TABLE products ADD COLUMN retail_price FLOAT")
        print("Added 'retail_price' column to products.")
    if 'mrp_price' not in cols:
        cur.execute("ALTER TABLE products ADD COLUMN mrp_price FLOAT")
        print("Added 'mrp_price' column to products.")
    if 'stock_quantity' not in cols:
        cur.execute("ALTER TABLE products ADD COLUMN stock_quantity INTEGER DEFAULT 0")
        print("Added 'stock_quantity' column to products.")
    if 'stock_total' not in cols:
        cur.execute("ALTER TABLE products ADD COLUMN stock_total INTEGER DEFAULT 0")
        print("Added 'stock_total' column to products.")
    if 'slug' not in cols:
        cur.execute("ALTER TABLE products ADD COLUMN slug TEXT")
        print("Added 'slug' column to products.")
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_products_slug ON products(slug)")
        print("Created unique index on 'slug'.")
    if 'quantity1' not in cols:
        cur.execute("ALTER TABLE products ADD COLUMN quantity1 INTEGER DEFAULT 0")
        print("Added 'quantity1' column to products.")
    if 'price1' not in cols:
        cur.execute("ALTER TABLE products ADD COLUMN price1 FLOAT DEFAULT 0")
        print("Added 'price1' column to products.")
    if 'quantity2' not in cols:
        cur.execute("ALTER TABLE products ADD COLUMN quantity2 INTEGER DEFAULT 0")
        print("Added 'quantity2' column to products.")
    if 'price2' not in cols:
        cur.execute("ALTER TABLE products ADD COLUMN price2 FLOAT DEFAULT 0")
        print("Added 'price2' column to products.")
    if 'quantity3' not in cols:
        cur.execute("ALTER TABLE products ADD COLUMN quantity3 INTEGER DEFAULT 0")
        print("Added 'quantity3' column to products.")
    if 'price3' not in cols:
        cur.execute("ALTER TABLE products ADD COLUMN price3 FLOAT DEFAULT 0")
        print("Added 'price3' column to products.")
    if 'sub_category' not in cols:
        cur.execute("ALTER TABLE products ADD COLUMN sub_category TEXT")
        print("Added 'sub_category' column to products.")
    if 'collection' not in cols:
        cur.execute("ALTER TABLE products ADD COLUMN collection TEXT")
        print("Added 'collection' column to products.")
    if 'size' not in cols:
        cur.execute("ALTER TABLE products ADD COLUMN size TEXT")
        print("Added 'size' column to products.")
    if 'retail_discount_percent' not in cols:
        cur.execute("ALTER TABLE products ADD COLUMN retail_discount_percent FLOAT DEFAULT 0")
        print("Added 'retail_discount_percent' column to products.")
    if 'wholesale_price' not in cols:
        cur.execute("ALTER TABLE products ADD COLUMN wholesale_price FLOAT DEFAULT 0")
        print("Added 'wholesale_price' column to products.")
    if 'min_wholesale_qty' not in cols:
        cur.execute("ALTER TABLE products ADD COLUMN min_wholesale_qty INTEGER DEFAULT 0")
        print("Added 'min_wholesale_qty' column to products.")
    if 'sets_count' not in cols:
        cur.execute("ALTER TABLE products ADD COLUMN sets_count INTEGER DEFAULT 0")
        print("Added 'sets_count' column to products.")
    if 'purchase_cost' not in cols:
        cur.execute("ALTER TABLE products ADD COLUMN purchase_cost FLOAT DEFAULT 0")
        print("Added 'purchase_cost' column to products.")
    if 'making_charges' not in cols:
        cur.execute("ALTER TABLE products ADD COLUMN making_charges FLOAT DEFAULT 0")
        print("Added 'making_charges' column to products.")
    if 'weight_grams' not in cols:
        cur.execute("ALTER TABLE products ADD COLUMN weight_grams FLOAT DEFAULT 0")
        print("Added 'weight_grams' column to products.")
    if 'hsn_code' not in cols:
        cur.execute("ALTER TABLE products ADD COLUMN hsn_code TEXT")
        print("Added 'hsn_code' column to products.")
    if 'gst_percent' not in cols:
        cur.execute("ALTER TABLE products ADD COLUMN gst_percent FLOAT DEFAULT 0")
        print("Added 'gst_percent' column to products.")
    if 'box_packing_type' not in cols:
        cur.execute("ALTER TABLE products ADD COLUMN box_packing_type TEXT")
        print("Added 'box_packing_type' column to products.")
    if 'vendor_id' not in cols:
        cur.execute("ALTER TABLE products ADD COLUMN vendor_id TEXT")
        print("Added 'vendor_id' column to products.")
    if 'status' not in cols:
        cur.execute("ALTER TABLE products ADD COLUMN status TEXT")
        print("Added 'status' column to products.")
    if 'is_active' not in cols:
        cur.execute("ALTER TABLE products ADD COLUMN is_active INTEGER DEFAULT 1")
        print("Added 'is_active' column to products.")
    if 'is_featured' not in cols:
        cur.execute("ALTER TABLE products ADD COLUMN is_featured INTEGER DEFAULT 0")
        print("Added 'is_featured' column to products.")
    if 'image_field' not in cols:
        cur.execute("ALTER TABLE products ADD COLUMN image_field TEXT")
        print("Added 'image_field' column to products.")

def main():
    db_path = 'narinakhre.db'
    if not os.path.exists(db_path):
        print(f"Database not found: {db_path}")
        return
    conn = sqlite3.connect(db_path)
    migrate_quotes_table(conn)
    migrate_products_table(conn)
    conn.commit()
    conn.close()
    print("Migration complete.")

if __name__ == '__main__':
    main()
