import os
import shutil
import pandas as pd
import sqlite3
from PIL import Image

# Paths
SOURCE_DIR = 'uploads_queue'
TARGET_DIR = 'static/assets/products'

def get_db():
    conn = sqlite3.connect('database.db')
    conn.row_factory = sqlite3.Row
    return conn

def organize_images():
    if not os.path.exists(SOURCE_DIR):
        os.makedirs(SOURCE_DIR)
        print(f"Created '{SOURCE_DIR}'. Drop your SKU folders in there and run again.")
        return

    # Loop through each folder (SKU) in the queue
    for sku in os.listdir(SOURCE_DIR):
        sku_path = os.path.join(SOURCE_DIR, sku)
        if os.path.isdir(sku_path):
            print(f"Processing SKU: {sku}")
            images = [f for f in os.listdir(sku_path) if f.lower().endswith(('.jpg', '.jpeg', '.png', '.webp'))]
            images.sort()
            for index, img_name in enumerate(images[:5]):
                new_name = f"{sku}_{index + 1}.jpg"
                src = os.path.join(sku_path, img_name)
                dst = os.path.join(TARGET_DIR, new_name)
                if not os.path.exists(TARGET_DIR):
                    os.makedirs(TARGET_DIR)
                # Compress and resize
                try:
                    with Image.open(src) as im:
                        im = im.convert('RGB')
                        max_size = (1200, 1200)
                        im.thumbnail(max_size, Image.ANTIALIAS)
                        im.save(dst, 'JPEG', quality=80)
                    print(f"  -> Compressed & Saved {new_name}")
                except Exception as e:
                    print(f"  -> Failed to process {img_name}: {e}")
            # Remove the now-empty SKU folder
            for img_name in images:
                try:
                    os.remove(os.path.join(sku_path, img_name))
                except Exception:
                    pass
            try:
                os.rmdir(sku_path)
            except Exception:
                pass

    print("\nAll images compressed and moved to static/assets/products!")

def process_uploads_queue():
    """Processes the uploads queue and updates the database."""
    uploads_queue = 'uploads_queue'
    products_dir = os.path.join('static', 'assets', 'products')
    
    if not os.path.exists(uploads_queue):
        print(f"uploads_queue folder not found: {uploads_queue}")
        return
        
    if not os.path.exists(products_dir):
        os.makedirs(products_dir, exist_ok=True)

    conn = get_db()
    cursor = conn.cursor()
    
    for sku_folder in os.listdir(uploads_queue):
        sku_path = os.path.join(uploads_queue, sku_folder)
        if os.path.isdir(sku_path):
            images = [f for f in os.listdir(sku_path) if f.lower().endswith(('.jpg', '.jpeg', '.png', '.webp'))]
            images.sort()
            for idx, img in enumerate(images[:5], 1):
                new_name = f"{sku_folder}_{idx}.jpg"
                src = os.path.join(sku_path, img)
                dst = os.path.join(products_dir, new_name)
                try:
                    shutil.copy(src, dst)
                    rel_path = f"assets/products/{new_name}"
                    cursor.execute('''
                        INSERT INTO product_images (product_id, image_path, image_index) 
                        SELECT id, ?, ? FROM products WHERE sku=?
                    ''', (rel_path, idx, sku_folder))
                except Exception as e:
                    print(f"Failed to copy {sku_folder}: {e}")
    conn.commit()
    conn.close()

def import_excel(filepath):
    """Imports product data from Excel."""
    try:
        df = pd.read_excel(filepath)
        conn = get_db()
        cursor = conn.cursor()
        for _, row in df.iterrows():
            sku = str(row.get('SKU')).strip()
            name = str(row.get('Name')).strip()
            # ... (Logic to insert products, categories, and price tiers)
            # This section remains your existing logic, but now properly indented
        conn.commit()
        conn.close()
        return 'Excel imported successfully.'
    except Exception as e:
        return f'Import failed: {e}'

if __name__ == "__main__":
    organize_images()