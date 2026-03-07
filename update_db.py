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
    # Add missing columns
    if 'description' not in cols:
        cur.execute("ALTER TABLE products ADD COLUMN description TEXT")
        print("Added 'description' column to products.")
    if 'material' not in cols:
        cur.execute("ALTER TABLE products ADD COLUMN material TEXT")
        print("Added 'material' column to products.")

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
