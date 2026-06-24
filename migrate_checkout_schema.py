import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / 'narinakhre.db'


def table_exists(cursor, table_name):
    cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    )
    return cursor.fetchone() is not None


def column_exists(cursor, table_name, column_name):
    cursor.execute(f"PRAGMA table_info({table_name})")
    return any(row[1] == column_name for row in cursor.fetchall())


def create_users_table(cursor):
    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            whatsapp TEXT,
            email TEXT,
            username TEXT,
            password TEXT
        )
        '''
    )


def create_order_shipping_table(cursor):
    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS order_shipping (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            status TEXT NOT NULL DEFAULT 'pending',
            consignee_name TEXT NOT NULL,
            consignee_phone TEXT NOT NULL,
            consignee_address TEXT NOT NULL,
            consignee_city TEXT NOT NULL,
            consignee_state TEXT NOT NULL,
            consignee_pincode TEXT NOT NULL,
            internal_order_id TEXT NOT NULL UNIQUE,
            delhivery_waybill TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        '''
    )


def migrate_order_shipping_columns(cursor):
    expected_columns = [
        ("user_id", "INTEGER"),
        ("status", "TEXT NOT NULL DEFAULT 'pending'"),
        ("consignee_name", "TEXT NOT NULL"),
        ("consignee_phone", "TEXT NOT NULL"),
        ("consignee_address", "TEXT NOT NULL"),
        ("consignee_city", "TEXT NOT NULL"),
        ("consignee_state", "TEXT NOT NULL"),
        ("consignee_pincode", "TEXT NOT NULL"),
        ("internal_order_id", "TEXT NOT NULL UNIQUE"),
        ("delhivery_waybill", "TEXT"),
    ]

    for column_name, column_type in expected_columns:
        if not column_exists(cursor, "order_shipping", column_name):
            cursor.execute(
                f"ALTER TABLE order_shipping ADD COLUMN {column_name} {column_type}"
            )
            print(f"Added column order_shipping.{column_name}")


def run_migration():
    print(f"Running checkout migration on: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    try:
        cursor = conn.cursor()

        create_users_table(cursor)
        print("Ensured users table exists")

        create_order_shipping_table(cursor)
        print("Ensured order_shipping table exists")

        if table_exists(cursor, "order_shipping"):
            migrate_order_shipping_columns(cursor)

        conn.commit()
        print("Checkout migration complete")
    finally:
        conn.close()


if __name__ == "__main__":
    run_migration()
