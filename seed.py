
import sqlite3

def seed():
    conn = sqlite3.connect('narinakhre.db')
    cur = conn.cursor()

    # Create Categories
    cur.execute("INSERT OR IGNORE INTO categories (id, name) VALUES (1, 'Jewelry'), (2, 'Beauty')")
    cur.execute("INSERT OR IGNORE INTO subcategories (id, name, category_id) VALUES (1, 'Bangles', 1), (2, 'Lipstick', 2)")

    # Create Products: Bangle (with sizes) and Lipstick (no sizes)
    cur.execute("""
        INSERT OR REPLACE INTO products (sku, name, subcategory_id, material, color, sizes, description)
        VALUES 
        ('BNG-01', 'Bridal Silk Bangle', 1, 'Silk', 'Red', '2/4, 2/6, 2/8', 'HSN: 7117, GST: 3%'),
        ('LIP-01', 'Matte Finish Lipstick', 2, 'Vegan', 'Ruby', NULL, 'HSN: 3304, GST: 18%')
    """)

    # Create Pricing Tiers (1 unit vs 50 units)
    cur.execute("INSERT INTO price_tiers (product_id, min_qty, price) VALUES (1, 1, 500), (1, 50, 450), (2, 1, 800), (2, 20, 700)")

    conn.commit()
    conn.close()
    print("Test products added to database.")

if __name__ == "__main__":
    seed()