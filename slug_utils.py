import re
import sqlite3

def generate_slug(name):
    slug = re.sub(r'[^a-zA-Z0-9]+', '-', name.lower()).strip('-')
    return slug

def get_unique_slug(conn, name):
    base_slug = generate_slug(name)
    slug = base_slug
    i = 1
    cur = conn.cursor()
    while True:
        cur.execute("SELECT 1 FROM products WHERE slug = ?", (slug,))
        if not cur.fetchone():
            break
        slug = f"{base_slug}-{i}"
        i += 1
    return slug
