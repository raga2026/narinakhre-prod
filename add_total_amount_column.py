import sqlite3

# Path to your SQLite database file
DB_PATH = "narinakhre.db"

# Connect to the database
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

try:
    cursor.execute("ALTER TABLE quotes ADD COLUMN total_amount REAL;")
    print("Column 'total_amount' added successfully.")
except sqlite3.OperationalError as e:
    print(f"Error: {e}")
finally:
    conn.commit()
    conn.close()
