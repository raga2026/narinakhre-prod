import sqlite3

DB_PATH = 'narinakhre.db'  # Update this if your DB file is elsewhere

ALTERS = [
    # Products table
    "ALTER TABLE products ADD COLUMN weight FLOAT DEFAULT 0;",
    "ALTER TABLE products ADD COLUMN length FLOAT DEFAULT 0;",
    "ALTER TABLE products ADD COLUMN breadth FLOAT DEFAULT 0;",
    "ALTER TABLE products ADD COLUMN height FLOAT DEFAULT 0;",
    # Orders table
    "ALTER TABLE orders ADD COLUMN address_line1 TEXT;",
    "ALTER TABLE orders ADD COLUMN address_line2 TEXT;",
    "ALTER TABLE orders ADD COLUMN city TEXT;",
    "ALTER TABLE orders ADD COLUMN state TEXT;",
    "ALTER TABLE orders ADD COLUMN pincode TEXT;",
    "ALTER TABLE orders ADD COLUMN country TEXT;",
    "ALTER TABLE orders ADD COLUMN email TEXT;",
    "ALTER TABLE orders ADD COLUMN mobile TEXT;",
    # Users table
    "ALTER TABLE users ADD COLUMN address_line1 TEXT;",
    "ALTER TABLE users ADD COLUMN address_line2 TEXT;",
    "ALTER TABLE users ADD COLUMN city TEXT;",
    "ALTER TABLE users ADD COLUMN state TEXT;",
    "ALTER TABLE users ADD COLUMN pincode TEXT;",
    "ALTER TABLE users ADD COLUMN country TEXT;",
    "ALTER TABLE users ADD COLUMN email TEXT;",
    "ALTER TABLE users ADD COLUMN mobile TEXT;",
]

def run_migration():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    for stmt in ALTERS:
        try:
            cur.execute(stmt)
            print(f"Executed: {stmt}")
        except sqlite3.OperationalError as e:
            if 'duplicate column name' in str(e):
                print(f"Skipped (already exists): {stmt}")
            else:
                print(f"Error executing '{stmt}': {e}")
    conn.commit()
    conn.close()
    print("Migration complete.")

if __name__ == "__main__":
    run_migration()
