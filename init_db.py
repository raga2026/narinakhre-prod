import sqlite3
import os

def initialize():
    if not os.path.exists('schema.sql'):
        print("Error: schema.sql not found!")
        return

    conn = sqlite3.connect('narinakhre.db')
    with open('schema.sql', 'r', encoding='utf-8') as f:
        conn.executescript(f.read())
    conn.close()
    print("Database 'narinakhre.db' initialized successfully.")

if __name__ == "__main__":
    initialize()