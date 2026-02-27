
import os
import shutil

# Paths
SOURCE_DIR = 'uploads_queue'  # The folder where you drop your SKU folders
TARGET_DIR = 'static/assets/products'

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
            
            # Get all images in that SKU folder
            images = [f for f in os.listdir(sku_path) if f.lower().endswith(('.jpg', '.jpeg', '.png'))]
            
            for index, img_name in enumerate(images):
                # We use index + 1 for _1, _2, etc. 
                # The first image (index 0) will be SKU_1.jpg
                new_name = f"{sku}_{index + 1}.jpg"
                
                src = os.path.join(sku_path, img_name)
                dst = os.path.join(TARGET_DIR, new_name)
                
                # Move and Rename
                shutil.move(src, dst)
                print(f"  -> Moved & Renamed to {new_name}")
            
            # Remove the now-empty SKU folder
            os.rmdir(sku_path)

    print("\nAll images organized and moved to static/assets/products!")

if __name__ == "__main__":
    organize_images()
        uploads_queue = os.path.join('uploads_q')
        products_dir = os.path.join('static', 'assets', 'products')
        if not os.path.exists(uploads_queue):
            print(f"uploads_q folder not found: {uploads_queue}")
            return
        if not os.path.exists(products_dir):
            os.makedirs(products_dir, exist_ok=True)
            print(f"Created products_dir: {products_dir}")
        conn = get_db()
        cursor = conn.cursor()
        for sku_folder in os.listdir(uploads_queue):
            sku_path = os.path.join(uploads_queue, sku_folder)
            if os.path.isdir(sku_path):
                images = [f for f in os.listdir(sku_path) if f.lower().endswith(('.jpg', '.jpeg', '.png', '.webp'))]
                images.sort()
                for idx, img in enumerate(images[:5], 1):
                    ext = os.path.splitext(img)[1].lower()
                    new_name = f"{sku_folder}_{idx}.jpg"
                    src = os.path.join(sku_path, img)
                    dst = os.path.join(products_dir, new_name)
                    try:
                        if os.path.exists(dst):
                            os.replace(src, dst)
                        else:
                            shutil.copy(src, dst)
                        print(f"Copied/Renamed {src} -> {dst}")
                        rel_path = os.path.join('assets', 'products', new_name)
                        cursor.execute('INSERT INTO product_images (product_id, image_path, image_index) SELECT id, ?, ? FROM products WHERE sku=?', (rel_path, idx, sku_folder))
                    except Exception as e:
                        print(f"Failed to copy/rename {src} -> {dst}: {e}")
                # Optionally remove the folder after processing
                # os.rmdir(sku_path)
        conn.commit()
        conn.close()
    def import_excel(filepath):
        try:
            df = pd.read_excel(filepath)
            conn = get_db()
            cursor = conn.cursor()
            for _, row in df.iterrows():
                sku = str(row.get('SKU')).strip()
                name = str(row.get('Name')).strip()
                hsn = str(row.get('HSN')).strip()
                gst = float(row.get('GST', 0))
                material = str(row.get('Material')).strip()
                color = str(row.get('Color')).strip()
                sizes = str(row.get('Sizes')).strip()
                category = str(row.get('Category')).strip()
                subcategory = str(row.get('Subcategory')).strip()
                description = str(row.get('Description', ''))
                cursor.execute('INSERT OR IGNORE INTO categories (name) VALUES (?)', (category,))
                cursor.execute('SELECT id FROM categories WHERE name=?', (category,))
                category_id = cursor.fetchone()['id']
                cursor.execute('INSERT OR IGNORE INTO subcategories (name, category_id) VALUES (?, ?)', (subcategory, category_id))
                cursor.execute('SELECT id FROM subcategories WHERE name=? AND category_id=?', (subcategory, category_id))
                subcategory_id = cursor.fetchone()['id']
                cursor.execute('INSERT OR IGNORE INTO products (sku, hsn, gst, material, color, sizes, category_id, subcategory_id, name, description) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                    (sku, hsn, gst, material, color, sizes, category_id, subcategory_id, name, description))
                cursor.execute('SELECT id FROM products WHERE sku=?', (sku,))
                product_id = cursor.fetchone()['id']
                price_tiers = str(row.get('PriceTiers', '')).split(';')
                for tier in price_tiers:
                    if ':' in tier:
                        qty, price = tier.split(':')
                        try:
                            cursor.execute('INSERT INTO price_tiers (product_id, min_qty, price) VALUES (?, ?, ?)', (product_id, int(qty), float(price)))
                        except:
                            pass
                for i in range(1, 6):
                    image_name = f"{sku}_{i}.jpg"
                    image_path = os.path.join('static/assets/products', image_name)
                    if os.path.exists(image_path):
                        cursor.execute('INSERT INTO product_images (product_id, image_path, image_index) VALUES (?, ?, ?)', (product_id, image_path, i))
            conn.commit()
            conn.close()
            return 'Excel imported successfully.'
        except Exception as e:
            return f'Import failed: {e}'
    @app.route('/')
    def gallery():
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM products')
        products = cursor.fetchall()
        product_list = []
        products_dir = os.path.join('static', 'assets', 'products')
        for product in products:
            cursor.execute('SELECT min_qty, price FROM price_tiers WHERE product_id=? ORDER BY min_qty', (product['id'],))
            price_tiers = [{'min_qty': row['min_qty'], 'price': row['price']} for row in cursor.fetchall()]
            cursor.execute('SELECT name FROM categories WHERE id=?', (product['category_id'],))
            category = cursor.fetchone()['name'] if product['category_id'] else ''
            images = []
            for i in range(1, 6):
                img_path = os.path.join(products_dir, f"{product['sku']}_{i}.jpg")
                if os.path.exists(img_path):
                    images.append(f"assets/products/{product['sku']}_{i}.jpg")
            if not images:
                images = ["https://via.placeholder.com/300"]
            product_list.append({
                'id': product['id'],
                'sku': product['sku'],
                'name': product['name'],
                'images': images,
                'material': product['material'],
                'color': product['color'],
                'sizes': product['sizes'],
                'description': product['description'],
                'price_tiers': price_tiers,
                'category': category
            })
        conn.close()
        return render_template('index.html', products=product_list)
    @app.route('/product/<sku>')
    def product_detail(sku):
        conn = get_db()        import os
        import sqlite3
        import shutil
        from flask import Flask, render_template, request, redirect, url_for, flash
        from werkzeug.utils import secure_filename
        import pandas as pd
        app = Flask(__name__)
        app.config['UPLOAD_FOLDER'] = 'static/assets/uploads'
        app.config['DATABASE'] = 'narinakhre.db'
        app.secret_key = 'supersecretkey'
        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
        def get_db():
            conn = sqlite3.connect(app.config['DATABASE'])
            conn.row_factory = sqlite3.Row
            return conn
        def init_db():
            conn = get_db()
            with open('schema.sql', 'r') as f:
                conn.executescript(f.read())
            conn.close()
        with app.app_context():
            init_db()
        @app.route('/admin', methods=['GET', 'POST'])
        def admin():
            if request.method == 'POST':
                file = request.files.get('file')
                if file and file.filename.endswith('.xlsx'):
                    filename = secure_filename(file.filename)
                    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                    file.save(filepath)
                    result = import_excel(filepath)
                    process_uploads_queue()
                    flash(result)
                    return redirect(url_for('admin'))
                else:
                    flash('Please upload a valid Excel (.xlsx) file.')
            return render_template('admin.html')
        def process_uploads_queue():
            uploads_queue = os.path.join('uploads_q')
            products_dir = os.path.join('static', 'assets', 'products')
            if not os.path.exists(uploads_queue):
                print(f"uploads_q folder not found: {uploads_queue}")
                return
            if not os.path.exists(products_dir):
                os.makedirs(products_dir, exist_ok=True)
                print(f"Created products_dir: {products_dir}")
            conn = get_db()
            cursor = conn.cursor()
            for sku_folder in os.listdir(uploads_queue):
                sku_path = os.path.join(uploads_queue, sku_folder)
                if os.path.isdir(sku_path):
                    images = [f for f in os.listdir(sku_path) if f.lower().endswith(('.jpg', '.jpeg', '.png', '.webp'))]
                    images.sort()
                    for idx, img in enumerate(images[:5], 1):
                        ext = os.path.splitext(img)[1].lower()
                        new_name = f"{sku_folder}_{idx}.jpg"
                        src = os.path.join(sku_path, img)
                        dst = os.path.join(products_dir, new_name)
                        try:
                            if os.path.exists(dst):
                                os.replace(src, dst)
                            else:
                                shutil.copy(src, dst)
                            print(f"Copied/Renamed {src} -> {dst}")
                            rel_path = os.path.join('assets', 'products', new_name)
                            cursor.execute('INSERT INTO product_images (product_id, image_path, image_index) SELECT id, ?, ? FROM products WHERE sku=?', (rel_path, idx, sku_folder))
                        except Exception as e:
                            print(f"Failed to copy/rename {src} -> {dst}: {e}")
                    # Optionally remove the folder after processing
                    # os.rmdir(sku_path)
            conn.commit()
            conn.close()
        def import_excel(filepath):
            try:
                df = pd.read_excel(filepath)
                conn = get_db()
                cursor = conn.cursor()
                for _, row in df.iterrows():
                    sku = str(row.get('SKU')).strip()
                    name = str(row.get('Name')).strip()
                    hsn = str(row.get('HSN')).strip()
                    gst = float(row.get('GST', 0))
                    material = str(row.get('Material')).strip()
                    color = str(row.get('Color')).strip()
                    sizes = str(row.get('Sizes')).strip()
                    category = str(row.get('Category')).strip()
                    subcategory = str(row.get('Subcategory')).strip()
                    description = str(row.get('Description', ''))
                    cursor.execute('INSERT OR IGNORE INTO categories (name) VALUES (?)', (category,))
                    cursor.execute('SELECT id FROM categories WHERE name=?', (category,))
                    category_id = cursor.fetchone()['id']
                    cursor.execute('INSERT OR IGNORE INTO subcategories (name, category_id) VALUES (?, ?)', (subcategory, category_id))
                    cursor.execute('SELECT id FROM subcategories WHERE name=? AND category_id=?', (subcategory, category_id))
                    subcategory_id = cursor.fetchone()['id']
                    cursor.execute('INSERT OR IGNORE INTO products (sku, hsn, gst, material, color, sizes, category_id, subcategory_id, name, description) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                        (sku, hsn, gst, material, color, sizes, category_id, subcategory_id, name, description))
                    cursor.execute('SELECT id FROM products WHERE sku=?', (sku,))
                    product_id = cursor.fetchone()['id']
                    price_tiers = str(row.get('PriceTiers', '')).split(';')
                    for tier in price_tiers:
                        if ':' in tier:
                            qty, price = tier.split(':')
                            try:
                                cursor.execute('INSERT INTO price_tiers (product_id, min_qty, price) VALUES (?, ?, ?)', (product_id, int(qty), float(price)))
                            except:
                                pass
                    for i in range(1, 6):
                        image_name = f"{sku}_{i}.jpg"
                        image_path = os.path.join('static/assets/products', image_name)
                        if os.path.exists(image_path):
                            cursor.execute('INSERT INTO product_images (product_id, image_path, image_index) VALUES (?, ?, ?)', (product_id, image_path, i))
                conn.commit()
                conn.close()
                return 'Excel imported successfully.'
            except Exception as e:
                return f'Import failed: {e}'
