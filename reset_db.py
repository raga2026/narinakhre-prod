import sqlite3

DB_PATH = 'narinakhre.db'

def reset_quotes_table():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # Drop existing quotes table
    cursor.execute('DROP TABLE IF EXISTS quotes')
    # Recreate quotes table with required columns
    cursor.execute('''
        CREATE TABLE quotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_name TEXT,
            whatsapp TEXT,
            email TEXT,
            address TEXT,
            cart_json TEXT,
            total_amount REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()
    print('quotes table reset successfully.')

if __name__ == '__main__':
    reset_quotes_table()
