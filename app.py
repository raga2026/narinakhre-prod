
# --- Admin Dashboard Route ---
# Place this after app = Flask(__name__)

"""
Route for category filter page. Move this below app = Flask(__name__)
"""
# --- Checkout Route ---
# Place this after app = Flask(__name__)
# --- Clear Quote Route ---
# --- Clear Quote Route ---
"""
Route for category filter page. Move this below app = Flask(__name__)
"""
# --- Checkout Route ---
# Place this after app = Flask(__name__)
# --- Clear Quote Route ---
# --- Clear Quote Route ---
# --- Flask & Core Imports ---

from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, send_from_directory, g
import sqlite3
import os
import json
import uuid
from werkzeug.utils import secure_filename
from PIL import Image

import pandas as pd

import smtplib
from email.mime.multipart import MIMEMultipart

from email.mime.text import MIMEText


app = Flask(__name__)

# --- Admin Dashboard Route ---
@app.route('/admin')
def admin():
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))
    db = get_db()
    products = db.execute('SELECT * FROM products').fetchall()
    categories = get_categories()
    return render_template('admin.html', products=products, categories=categories, monaco_ui=True)

@app.route('/category/<category>')
def category_products(category):
    db = get_db()
    products = db.execute('SELECT * FROM products WHERE category = ?', (category,)).fetchall()
    product_list = []
    for product in products:
        product = dict(product)
        product['images'] = []
        for i in range(1,6):
            img_path = os.path.join('static', 'assets', 'products', f"{product['sku']}_{i}.jpg")
            if os.path.exists(img_path):
                product['images'].append(url_for('static', filename=f"assets/products/{product['sku']}_{i}.jpg"))
        if not product['images']:
            product['images'] = [url_for('static', filename='assets/products/default.jpg')]
        product['tiers'] = [
            {'tier': 1, 'qty': product.get('quantity1'), 'price': product.get('price1')},
            {'tier': 2, 'qty': product.get('quantity2'), 'price': product.get('price2')},
            {'tier': 3, 'qty': product.get('quantity3'), 'price': product.get('price3')},
        ]
        product['sizes'] = product.get('sizes', '')
        product_list.append(product)
    return render_template('category_products.html', category=category, products=product_list)
app.config['UPLOAD_FOLDER'] = 'static/assets/uploads'
app.config['DATABASE'] = 'narinakhre.db'
app.secret_key = 'supersecretkey'  # Static string, not os.urandom(24)

# --- Clear Quote Route ---
@app.route('/clear_quote', methods=['POST'])
def clear_quote():
    session.pop('cart', None)
    session.modified = True
    # Stay on checkout page after clearing
    return redirect(url_for('checkout'))

# --- Database Connection Utility ---

# --- Improved Database Connection Utility ---
def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(app.config['DATABASE'])
        g.db.row_factory = sqlite3.Row
    return g.db

# --- Close DB Connection After Each Request ---
@app.teardown_appcontext
def close_db(error):
    db = g.pop('db', None)
    if db is not None:
        db.close()

# --- Create Tables Utility ---
def create_tables():
    with sqlite3.connect(app.config['DATABASE']) as conn:
        cursor = conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE
        )''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sku TEXT UNIQUE,
            name TEXT,
            category TEXT,
            subcategory TEXT,
            description TEXT,
            material TEXT,
            color TEXT,
            sizes TEXT,
            hsn TEXT,
            gst REAL,
            quantity1 INTEGER,
            price1 REAL,
            quantity2 INTEGER,
            quantity3 INTEGER,
            price3 REAL,
            image_url TEXT
        )''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS product_images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            image_path TEXT NOT NULL,
            image_index INTEGER NOT NULL,
            FOREIGN KEY (product_id) REFERENCES products(id)
        )''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS quotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            whatsapp TEXT,
            email TEXT,
            items_json TEXT,
            total_amount REAL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )''')
        conn.commit()

# --- Helpers ---
def get_categories():
    db = get_db()
    categories = db.execute('SELECT * FROM categories').fetchall()
    return [c['name'] for c in categories]

# --- EMAIL CONFIG FOR GODADDY/OFFICE365 ---
MAIL_SERVER = 'smtp.zoho.in'
MAIL_PORT = 465
MAIL_USE_SSL = True
MAIL_USE_TLS = False
MAIL_USERNAME = 'info@narinakhre.com'
MAIL_PASSWORD = '3hbztEFHs0Ei'

# --- ROUTES ---
@app.route('/contact', methods=['GET', 'POST'])
def contact():
    if request.method == 'POST':
        name = request.form.get('name')
        whatsapp = request.form.get('whatsapp')
        email = request.form.get('email')
        # Compose email body
        body = f"""
        <h2>Contact Inquiry from NariNakhre</h2>
        <p><strong>Name:</strong> {name}</p>
        <p><strong>WhatsApp:</strong> {whatsapp}</p>
        <p><strong>Email:</strong> {email}</p>
        """
        sender_email = MAIL_USERNAME
        sender_password = MAIL_PASSWORD
        admin_email = sender_email
        subject = "Nari Nakhre Contact Inquiry"
        msg = MIMEMultipart()
        msg['From'] = sender_email
        msg['To'] = email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'html'))
        admin_msg = MIMEMultipart()
        admin_msg['From'] = sender_email
        admin_msg['To'] = admin_email
        admin_msg['Subject'] = f"New Contact Inquiry from {name}"
        admin_msg.attach(MIMEText(body, 'html'))
        try:
            server = smtplib.SMTP_SSL(MAIL_SERVER, MAIL_PORT)
            server.login(sender_email, sender_password)
            server.send_message(msg)
            server.send_message(admin_msg)
            server.quit()
            flash("Contact inquiry submitted and email sent!", "success")
        except Exception as e:
            print(f"Contact email error: {e}")
            flash("Contact submitted, but email could not be sent.", "warning")
        return redirect(url_for('thank_you'))
    return render_template('contact.html')
# Redirect /admin-login to /admin/login for user convenience
@app.route('/admin-login', methods=['GET', 'POST'])
def admin_login_redirect():
    return redirect(url_for('admin_login'))

@app.route('/')
def index():
    # ...existing code...
    db = get_db()
    products = db.execute('SELECT * FROM products').fetchall()
    session['cart'] = session.get('cart', {})
    grouped_products = {}
    for product in products:
        cat = product['category'].strip() if product['category'] else None
        if not cat:
            continue  # skip products with no category
        if cat not in grouped_products:
            grouped_products[cat] = []
        # Add gallery images and tiered pricing to each product
        product = dict(product)
        product['images'] = []
        for i in range(1,6):
            img_path = os.path.join('static', 'assets', 'products', f"{product['sku']}_{i}.jpg")
            if os.path.exists(img_path):
                product['images'].append(url_for('static', filename=f"assets/products/{product['sku']}_{i}.jpg"))
        if not product['images']:
            product['images'] = [url_for('static', filename='assets/products/default.jpg')]
        product['tiers'] = [
            {'tier': 1, 'qty': product.get('quantity1'), 'price': product.get('price1')},
            {'tier': 2, 'qty': product.get('quantity2'), 'price': product.get('price2')},
            {'tier': 3, 'qty': product.get('quantity3'), 'price': product.get('price3')},
        ]
        grouped_products[cat].append(product)
    return render_template('index.html', grouped_products=grouped_products, cart=session['cart'])

@app.route('/update-cart', methods=['POST'])
def update_cart():
    data = request.get_json()
    print('[Add to Quote] Received:', data)
    product_id = int(data.get('product_id'))
    qty = int(data.get('qty'))
    try:
        tier_val = data.get('tier')
        tier = int(float(tier_val))
    except Exception as e:
        print(f'[Add to Quote] Tier conversion error: {e}, value: {data.get("tier")}')
        tier = 1
    price = float(data.get('price'))
    size = data.get('size', '')
    if not size:
        print('[Add to Quote] Error: Size is required.')
        return jsonify({'status': 'error', 'message': 'Size is required.'}), 400
    db = get_db()
    product = db.execute('SELECT sku, name FROM products WHERE id = ?', (product_id,)).fetchone()
    if not product:
        return jsonify({'status': 'error', 'message': 'Product not found.'}), 404
    sku = product['sku']
    name = product['name']
    product_key = f"{sku}_{tier}_{size}"
    cart = session.get('cart', {})
    item_key = f"{sku}_{tier}_{size}"
    new_qty = qty
    new_item_data = {
        'qty': new_qty,
        'tier': tier,
        'price': price,
        'size': size,
        'sku': sku,
        'name': name
    }
    if new_qty > 0:
        cart[item_key] = new_item_data
        print(f'[Add to Quote] Added/Updated item: {item_key} -> {new_item_data}')
    else:
        cart.pop(item_key, None)
        print(f'[Add to Quote] Removed item: {item_key}')
    session['cart'] = cart
    session.modified = True
    print("[Add to Quote] Cart after update:", session['cart'])
    # Return the updated quantity for this item (0 if removed)
    updated_qty = cart[item_key]['qty'] if item_key in cart else 0
    return jsonify({'status': 'success', 'cart_count': len(session['cart']), 'new_total': len(session['cart']), 'new_qty': updated_qty})

@app.route('/product/<int:product_id>')
def product_detail(product_id):
    db = get_db()
    product = db.execute('SELECT * FROM products WHERE id = ?', (product_id,)).fetchone()
    if product is None:
        return "Product not found", 404
    product = dict(product)
    # Set tiers like homepage
    product['tiers'] = [
        {'tier': 1, 'qty': product.get('quantity1'), 'price': product.get('price1')},
        {'tier': 2, 'qty': product.get('quantity2'), 'price': product.get('price2')},
        {'tier': 3, 'qty': product.get('quantity3'), 'price': product.get('price3')},
    ]
    # Set sizes as string (for dropdown)
    product['sizes'] = product.get('sizes', '')
    images = db.execute('SELECT image_path FROM product_images WHERE product_id = ? ORDER BY image_index', (product_id,)).fetchall()
    # Normalize image paths to be relative to static folder
    image_urls = []
    for img in images:
        path = img['image_path']
        # Remove leading slashes and static/ if present
        if path.startswith('static/'):
            path = path[len('static/'):]
        path = path.lstrip('/')
        image_urls.append(path)
    if not image_urls:
        # Fallback: check for up to 5 images in static/assets/products/
        for i in range(1, 6):
            img_path = f"static/assets/products/{product['sku']}_{i}.jpg"
            if os.path.exists(img_path):
                image_urls.append(f"assets/products/{product['sku']}_{i}.jpg")
        if not image_urls:
            image_urls = [f"assets/products/{product['sku']}_1.jpg"]
    related_products_raw = db.execute('SELECT * FROM products WHERE id != ? ORDER BY RANDOM() LIMIT 3', (product_id,)).fetchall()
    related_products = []
    for rel in related_products_raw:
        rel = dict(rel)
        rel['tiers'] = [
            {'tier': 1, 'qty': rel.get('quantity1'), 'price': rel.get('price1')},
            {'tier': 2, 'qty': rel.get('quantity2'), 'price': rel.get('price2')},
            {'tier': 3, 'qty': rel.get('quantity3'), 'price': rel.get('price3')},
        ]
        rel['sizes'] = rel.get('sizes', '')
        related_products.append(rel)

    return render_template('product_detail.html', product=product, image_urls=image_urls, related_products=related_products)

@app.route('/admin/bulk_update_products', methods=['POST'])
def bulk_update_products():
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))
    if 'excel_file' not in request.files:
        flash("No file part", "danger")
        return redirect(url_for('admin'))
    file = request.files['excel_file']
    if file.filename == '':
        flash("No selected file", "danger")
        return redirect(url_for('admin'))
    if file:
        try:
            df = pd.read_excel(file)
            df.columns = df.columns.str.strip().str.lower()
            db = get_db()
            update_count = 0
            for index, row in df.iterrows():
                sku_val = str(row.get('sku', '')).strip()
                if not sku_val or sku_val == 'nan':
                    continue
                # Only update existing products
                exists = db.execute('SELECT id FROM products WHERE sku = ?', (sku_val,)).fetchone()
                if not exists:
                    continue
                # Update all columns present in the Excel
                update_fields = []
                update_values = []
                for col in df.columns:
                    if col != 'sku':
                        update_fields.append(f"{col} = ?")
                        update_values.append(row.get(col))
                if update_fields:
                    update_values.append(sku_val)
                    db.execute(f"UPDATE products SET {', '.join(update_fields)} WHERE sku = ?", update_values)
                    update_count += 1
            db.commit()
            flash(f'Bulk update successful! {update_count} products updated.', 'success')
        except Exception as e:
            db.rollback()
            flash(f"Error in bulk update: {str(e)}", "danger")
    return redirect(url_for('admin'))

    # ...existing code...
    # ...existing code...
@app.route('/admin/manage_images', methods=['GET'])
def admin_manage_images():
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))
    sku_search = request.args.get('sku_search', '').strip()
    db = get_db()
    if sku_search:
        products = db.execute('SELECT sku, name FROM products WHERE sku LIKE ?', (f'%{sku_search}%',)).fetchall()
    else:
        products = db.execute('SELECT sku, name FROM products').fetchall()
    product_list = []
    for product in products:
        sku = product['sku']
        images = []
        for i in range(1, 6):
            img_path = f"assets/products/{sku}_{i}.jpg"
            full_path = os.path.join('static', img_path)
            if os.path.exists(full_path):
                images.append(img_path)
        product_list.append({'sku': sku, 'name': product['name'], 'images': images})
    return render_template('admin_manage_images.html', products=product_list, sku_search=sku_search)

@app.route('/admin/delete_image', methods=['POST'])
def admin_delete_image():
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))
    sku = request.form.get('sku')
    img_url = request.form.get('img_url')
    img_path = os.path.join('static', img_url)
    if os.path.exists(img_path):
        os.remove(img_path)
        flash('Image deleted.', 'success')
    else:
        flash('Image not found.', 'danger')
    return redirect(url_for('admin_manage_images'))

@app.route('/admin/upload_images', methods=['POST'])
def admin_upload_images():
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))
    sku = request.form.get('sku')
    files = request.files.getlist('images')
    count = 0
    for i in range(1, 6):
        img_path = os.path.join('static', 'assets', 'products', f"{sku}_{i}.jpg")
        if os.path.exists(img_path):
            count += 1
    available_slots = 5 - count
    uploaded = 0
    for file in files:
        if uploaded >= available_slots:
            break
        if file and file.filename:
            idx = count + uploaded + 1
            filename = f"{sku}_{idx}.jpg"
            save_path = os.path.join('static', 'assets', 'products', filename)
            file.save(save_path)
            uploaded += 1
    if uploaded:
        flash(f'{uploaded} image(s) uploaded.', 'success')
    else:
        flash('No images uploaded (max 5 per SKU).', 'warning')
    return redirect(url_for('admin_manage_images'))
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))
    db = get_db()
    products = db.execute('SELECT * FROM products').fetchall()
    categories = get_categories()
    # Pass Monaco UI flag for admin page
    return render_template('admin.html', products=products, categories=categories, monaco_ui=True)

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if username == 'admin' and password == 'Raghav@2026':
            session['admin_logged_in'] = True
            return redirect(url_for('admin'))
        else:
            flash("Invalid credentials", "danger")
    return render_template('admin_login.html')

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    return redirect(url_for('index'))

@app.route('/admin/add_product', methods=['POST'])
def add_product():
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    
    sku = request.form.get('sku')
    name = request.form.get('name')
    category = request.form.get('category')
    subcategory = request.form.get('subcategory')
    description = request.form.get('description')
    material = request.form.get('material')
    color = request.form.get('color')
    sizes = request.form.get('sizes')
    hsn = request.form.get('hsn')
    gst = request.form.get('gst')
    q1 = request.form.get('quantity1')
    p1 = request.form.get('price1')
    
    image = request.files.get('image')
    image_url = ""
    if image and image.filename != '':
        filename = secure_filename(image.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        image.save(filepath)
        image_url = filename

    db = get_db()
    try:
        db.execute('''INSERT INTO products 
            (sku, name, category, subcategory, description, material, color, sizes, hsn, gst, quantity1, price1, image_url)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (sku, name, category, subcategory, description, material, color, sizes, hsn, gst, q1, p1, image_url))
        db.commit()
        flash("Product added successfully!", "success")
    except sqlite3.IntegrityError:
        flash("Error: SKU must be unique", "danger")
    
    return redirect(url_for('admin'))

@app.route('/admin/delete_product/<int:product_id>')
def delete_product(product_id):
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    db = get_db()
    db.execute('DELETE FROM products WHERE id = ?', (product_id,))
    db.commit()
    flash("Product deleted!", "success")
    return redirect(url_for('admin'))

@app.route('/admin/import', methods=['POST'])
def import_excel():
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    if 'excel_file' not in request.files:
        flash("No file part", "danger")
        return redirect(url_for('admin'))
    file = request.files['excel_file']
    if file.filename == '':
        flash("No selected file", "danger")
        return redirect(url_for('admin'))
    if file:
        try:
            # Load the Excel file
            df = pd.read_excel(file)
            # FOOLPROOF STEP 1: Clean and normalize headers (lowercase)
            df.columns = df.columns.str.strip().str.lower()
            # FOOLPROOF STEP 2: Clear existing products if you want a fresh start
            # Product.query.delete() 
            success_count = 0
            db = get_db()
            for index, row in df.iterrows():
                # FOOLPROOF STEP 3: Skip empty rows or rows without a SKU
                sku_val = str(row.get('sku', '')).strip()
                if not sku_val or sku_val == 'nan':
                    continue
                # FOOLPROOF STEP 4: Robust Mapping
                # Ensure SKU is unique
                exists = db.execute('SELECT id FROM products WHERE sku = ?', (sku_val,)).fetchone()
                if exists:
                    continue
                name = str(row.get('name', 'Unnamed Product'))
                category = str(row.get('category', 'General'))
                subcategory = str(row.get('subcategory', ''))
                description = str(row.get('description', ''))
                material = str(row.get('material', ''))
                color = str(row.get('color', ''))
                sizes = str(row.get('sizes', 'Standard'))
                hsn = str(row.get('hsn', ''))
                gst = float(row.get('gst', 0) if pd.notnull(row.get('gst')) else 0)
                q1 = int(row.get('quantity1', 0) if pd.notnull(row.get('quantity1')) else 0)
                p1 = float(row.get('price1', 0) if pd.notnull(row.get('price1')) else 0)
                q2 = int(row.get('quantity2', 0) if pd.notnull(row.get('quantity2')) else 0)
                p2 = float(row.get('price2', 0) if pd.notnull(row.get('price2')) else 0)
                q3 = int(row.get('quantity3', 0) if pd.notnull(row.get('quantity3')) else 0)
                p3 = float(row.get('price3', 0) if pd.notnull(row.get('price3')) else 0)
                image_url = str(row.get('image_url', ''))
                db.execute('''INSERT INTO products (sku, name, category, subcategory, description, material, color, sizes, hsn, gst, quantity1, price1, quantity2, price2, quantity3, price3, image_url) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                    (sku_val, name, category, subcategory, description, material, color, sizes, hsn, gst, q1, p1, q2, p2, q3, p3, image_url))
                success_count += 1
            db.commit()
            flash(f'Import Successful! {success_count} products added to catalog.')
        except Exception as e:
            db.rollback()
            flash(f"Error importing Excel: {str(e)}", "danger")
            
    return redirect(url_for('admin'))

@app.route('/admin/delete_products', methods=['GET', 'POST'])
def admin_delete_products():
    db = get_db()
    if request.method == 'POST':
        product_ids = request.form.getlist('product_ids')
        if product_ids:
            # This handles the old 'Delete Selected' button logic if you still use it
            db.executemany('DELETE FROM products WHERE id = ?', [(pid,) for pid in product_ids])
            db.commit()
            flash(f"Successfully deleted {len(product_ids)} products.")
        return redirect(url_for('admin_delete_products'))

    # THE CRITICAL CHANGE: We now SELECT every column needed for the bulk table
    products = db.execute('''
        SELECT id, sku, name, category, 
               quantity1, price1, 
               quantity2, price2, 
               quantity3, price3 
        FROM products
    ''').fetchall()
    
    return render_template('admin_delete_products.html', products=products)
    


@app.route('/checkout', methods=['GET', 'POST'])
def checkout():
    print("[BUG DEBUG] session['cart'] at checkout:", session.get('cart', {}))
    cart_data = session.get('cart', {})
    db = get_db()
    display_cart = []
    subtotal = 0.0
    total_tax = 0.0
    if request.method == 'POST':
        name = request.form.get('name')
        session['user_name'] = name
    for key, item in cart_data.items():
        if isinstance(item, dict):
            qty = int(item.get('qty', 1))
            tier = int(item.get('tier', 1))
            price = float(item.get('price', 0))
            size = item.get('size', '')
            sku = item.get('sku')
        else:
            qty = int(item)
            tier = 1
            price = 0.0
            size = ''
            sku = key
        product = db.execute('SELECT * FROM products WHERE sku = ?', (sku,)).fetchone()
        if product:
            gst_rate = product['gst'] or 0.0
            item_subtotal = price * qty
            item_tax = item_subtotal * (gst_rate / 100)
            subtotal += item_subtotal
            total_tax += item_tax
            display_cart.append({
                'id': product['id'],
                'sku': product['sku'],
                'name': product['name'] if product['name'] else product['sku'],
                'units': qty,
                'tier': tier,
                'size': size,
                'price': price,
                'tax': item_tax,
                'total': item_subtotal + item_tax
            })
        else:
            display_cart.append({
                'id': sku,
                'sku': sku,
                'name': sku,
                'units': qty,
                'tier': tier,
                'size': size,
                'price': price,
                'tax': 0,
                'total': price * qty
            })
    grand_total = subtotal + total_tax
    if request.method == 'POST':
        try:
            # PDF/email logic here (if any)
            pass
        except Exception as e:
            print(f"PDF/Email error: {e}")
        return redirect(url_for('thank_you'))
    # Always show cart on GET
    return render_template('checkout.html', display_cart=display_cart, subtotal=subtotal, total_tax=total_tax, grand_total=grand_total)

def send_quote_emails(name, email, whatsapp, address, display_cart, subtotal, total_tax, grand_total):
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    sender_email = 'info@narinakhre.com'
    sender_password = MAIL_PASSWORD
    admin_email = sender_email
    receiver_email = email
    subject = "Nari Nakhre Wholesale Quote"
    from flask import render_template
    print("[DEBUG] Rendering email_quote.html with:")
    print("name:", name)
    print("address:", address)
    print("display_cart:", display_cart)
    print("grand_total:", grand_total)
    print("subtotal:", subtotal)
    print("total_tax:", total_tax)
    formatted_subtotal = f"₹{subtotal:,.2f}"
    formatted_gst = f"₹{total_tax:,.2f}"
    formatted_grand_total = f"₹{grand_total:,.2f}"
    try:
        body = render_template('email_quote.html',
            name=name,
            address=address,
            display_cart=display_cart,
            subtotal=formatted_subtotal,
            gst=formatted_gst,
            grand_total=formatted_grand_total)
        print("[DEBUG] Email template rendered successfully.")
    except Exception as e:
        print("[DEBUG] Error rendering email_quote.html:", e)
        raise
    msg = MIMEMultipart()
    msg['From'] = sender_email
    msg['To'] = receiver_email
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'html'))
    admin_msg = MIMEMultipart()
    admin_msg['From'] = sender_email
    admin_msg['To'] = admin_email
    admin_msg['Subject'] = f"New Quote from {name}"
    admin_msg.attach(MIMEText(body, 'html'))
    # Add forwarding to mohinicosmetics.india@gmail.com
    forward_msg = MIMEMultipart()
    forward_msg['From'] = sender_email
    forward_msg['To'] = 'mohinicosmetcs.in@gmail.com'
    forward_msg['Subject'] = f"Forwarded Quote from {name}"
    forward_msg.attach(MIMEText(body, 'html'))
    try:
        server = smtplib.SMTP_SSL(MAIL_SERVER, MAIL_PORT)
        server.login(sender_email, sender_password)
        server.send_message(msg)
        server.send_message(admin_msg)
        server.send_message(forward_msg)
        server.quit()
        print("Quote email sent successfully.")
    except Exception as e:
        print(f"Failed to send email: {e}")

@app.route('/submit_quote', methods=['POST'])
def submit_quote():
    # 1. Extract user details
    if request.is_json:
        data = request.get_json()
        name = data.get('name')
        whatsapp = data.get('whatsapp')
        email = data.get('email')
        address = data.get('address', '')
        total_amount = data.get('tentative_total', 0)
        cart = data.get('cart', {})
    else:
        name = request.form.get('name')
        whatsapp = request.form.get('whatsapp')
        email = request.form.get('email')
        address = request.form.get('address', '')
        total_amount = request.form.get('total_amount', 0)
        cart = json.loads(request.form.get('cart_data', '{}'))
    db = get_db()
    # 3. Build display_cart and calculate totals
    display_cart = []
    subtotal = 0.0
    total_tax = 0.0
    grand_total = 0.0
    subtotal = 0.0
    total_gst = 0.0
    display_cart = []
    # Insert quote to DB to get unique ID
    items_json = json.dumps(cart)
    request_id = str(uuid.uuid4())
    cursor = db.execute('INSERT INTO quotes (request_id, name, whatsapp, email, items_json, total_amount) VALUES (?, ?, ?, ?, ?, ?)', (request_id, name, whatsapp, email, items_json, grand_total))
    db.commit()
    quote_id = cursor.lastrowid
    for item_key, item in cart.items():
        # Get number of packs (units) and pieces per pack (tier)
        tier = int(item.get('tier', 1))  # pieces per unit (pack)
        units = int(item.get('qty', 1))  # number of packs
        price = float(item.get('price', 0))
        size = item.get('size', '')
        sku = item.get('sku', '')
        prod_name = item.get('name', '')
        product = db.execute('SELECT * FROM products WHERE sku = ?', (sku,)).fetchone()
        if product:
            gst_rate = product['gst'] or 0.0
            row_total = price * units
            subtotal += row_total
            gst_amount = row_total * (gst_rate / 100) if gst_rate else 0.0
            total_gst += gst_amount
            display_cart.append({
                'sku': sku,
                'name': prod_name or product['name'],
                'size': size,
                'tier': tier,  # pieces per unit (pack)
                'units': units,  # number of packs
                'price': f"₹{price:,.2f}",
                'row_total': f"₹{row_total:,.2f}"
            })
    grand_total = subtotal + total_gst
    formatted_subtotal = f"₹{subtotal:,.2f}"
    formatted_gst = f"₹{total_gst:,.2f}"
    formatted_grand_total = f"₹{grand_total:,.2f}"
    # 4. Render email template and pass data
    try:
        html_body = render_template('email_quote.html', name=name, address=address, display_cart=display_cart, subtotal=formatted_subtotal, gst=formatted_gst, grand_total=formatted_grand_total)
        # 5. Send email using Zoho SMTP
        sender_email = 'info@narinakhre.com'
        sender_password = MAIL_PASSWORD
        admin_email = 'mohinicosmetics.india@gmail.com'
        subject = f"Nari Nakhre Quote #{quote_id} for {name}"
        # User email
        msg = MIMEMultipart()
        msg['From'] = sender_email
        msg['To'] = email
        msg['Subject'] = subject
        msg.attach(MIMEText(html_body, 'html'))
        # Admin email
        admin_msg = MIMEMultipart()
        admin_msg['From'] = sender_email
        admin_msg['To'] = admin_email
        admin_msg['Subject'] = f"New Quote from {name}"
        admin_msg.attach(MIMEText(html_body, 'html'))
        server = smtplib.SMTP_SSL(MAIL_SERVER, MAIL_PORT)
        server.login(sender_email, sender_password)
        server.send_message(msg)
        server.send_message(admin_msg)
        server.quit()
        flash("Quote submitted and email sent!", "success")
        # 6. Clear cart only after email is sent
        session.pop('cart', None)
        session.modified = True
    except Exception as e:
        print(f"Email error: {e}")
        flash("Quote submitted, but email could not be sent.", "warning")
    # Store display_cart and grand_total for thank you page
    session['quote_display_cart'] = display_cart
    session['quote_grand_total'] = grand_total
    session['user_name'] = name
    session['user_whatsapp'] = whatsapp
    session['user_email'] = email
    return redirect(url_for('thank_you'))

@app.route('/thank_you')
def thank_you():
    user_name = session.get('user_name', 'Valued Customer')
    user_email = session.get('user_email', '')
    display_cart = session.get('quote_display_cart', [])
    grand_total = session.get('quote_grand_total', 0)
    return render_template('thank_you.html', user={'name': user_name, 'email': user_email}, display_cart=display_cart, grand_total=grand_total)

@app.route('/thank-you')
def thank_you_dash():
    user_name = session.get('user_name', 'Valued Customer')
    return render_template('thank_you.html', user={'name': user_name})

# --- MIGRATION: Add address column to quotes if missing ---
def ensure_quotes_columns():
    db = get_db()
    cursor = db.execute("PRAGMA table_info(quotes)")
    columns = [row[1] for row in cursor.fetchall()]
    if 'address' not in columns:
        db.execute("ALTER TABLE quotes ADD COLUMN address TEXT")
        db.commit()
    if 'subtotal' not in columns:
        db.execute("ALTER TABLE quotes ADD COLUMN subtotal REAL")
        db.commit()
    if 'total_tax' not in columns:
        db.execute("ALTER TABLE quotes ADD COLUMN total_tax REAL")
        db.commit()
    if 'grand_total' not in columns:
        db.execute("ALTER TABLE quotes ADD COLUMN grand_total REAL")
        db.commit()

def auto_migrate_products_table():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(products)")
    columns = [column[1] for column in cursor.fetchall()]
    if 'image_url' not in columns:
        cursor.execute("ALTER TABLE products ADD COLUMN image_url TEXT")
        conn.commit()

# --- AJAX Routes for Bulk Management ---


# --- AJAX Routes for Bulk Management ---

@app.route('/update_bulk_product', methods=['POST'])
def update_bulk_product():
    """Updates a single product's pricing tiers via AJAX."""
    data = request.get_json()
    db = get_db()
    try:
        db.execute('''
            UPDATE products 
            SET quantity1 = ?, price1 = ?, 
                quantity2 = ?, price2 = ?, 
                quantity3 = ?, price3 = ?
            WHERE sku = ?
        ''', (data['qty1'], data['p1'], data['qty2'], data['p2'], data['qty3'], data['p3'], data['sku']))
        db.commit()
        return jsonify({"status": "success", "message": "Product updated"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/delete_product_ajax', methods=['POST'])
def delete_product_ajax():
    """Deletes a single product via AJAX."""
    data = request.get_json()
    db = get_db()
    try:
        db.execute('DELETE FROM products WHERE sku = ?', (data['sku'],))
        db.commit()
        return jsonify({"status": "deleted", "sku": data['sku']})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
@app.route('/admin/organize_images', methods=['POST'])
def admin_organize_images():
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))
    from organize_images import organize_images
    organize_images()
    flash('Images have been organized from uploads_q to static/assets/products.', 'success')
    return redirect(url_for('admin_delete_products') )

# --- Excel Download Routes ---
@app.route('/admin/edit_product_details', methods=['GET', 'POST'])
def admin_edit_product_details():
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))
    db = get_db()
    categories = [c['name'] for c in db.execute('SELECT name FROM categories').fetchall()]
    subcategories = [row['subcategory'] for row in db.execute('SELECT DISTINCT subcategory FROM products WHERE subcategory IS NOT NULL AND subcategory != ""').fetchall()]
    products = db.execute('SELECT * FROM products').fetchall()
    if request.method == 'POST':
        sku = request.form.get('sku')
        name = request.form.get('name')
        description = request.form.get('description')
        hsn = request.form.get('hsn')
        gst = request.form.get('gst')
        category = request.form.get('category')
        subcategory = request.form.get('subcategory')
        sizes = request.form.get('sizes')
        material = request.form.get('material')
        color = request.form.get('color')
        db.execute('''UPDATE products SET name=?, description=?, hsn=?, gst=?, category=?, subcategory=?, sizes=?, material=?, color=? WHERE sku=?''',
            (name, description, hsn, gst, category, subcategory, sizes, material, color, sku))
        db.commit()
        flash(f'Product {sku} details updated!', 'success')
        return redirect(url_for('admin_edit_product_details'))
    return render_template('admin_edit_product_details.html', products=products, categories=categories, subcategories=subcategories)

from io import BytesIO
from flask import send_file

@app.route('/download_users_excel')
def download_users_excel():
    db = get_db()
    users = db.execute('SELECT name, whatsapp, email FROM quotes').fetchall()
    df = pd.DataFrame(users, columns=['name', 'whatsapp', 'email'])
    output = BytesIO()
    df.to_excel(output, index=False)
    output.seek(0)
    return send_file(output, download_name='users.xlsx', as_attachment=True, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@app.route('/download_quotes_excel')
def download_quotes_excel():
    db = get_db()
    quotes = db.execute('SELECT id, name, whatsapp, email, address, subtotal, total_tax, grand_total, created_at FROM quotes').fetchall()
    df = pd.DataFrame(quotes, columns=['id', 'name', 'whatsapp', 'email', 'address', 'subtotal', 'total_tax', 'grand_total', 'created_at'])
    output = BytesIO()
    df.to_excel(output, index=False)
    output.seek(0)
    return send_file(output, download_name='quotes.xlsx', as_attachment=True, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@app.route('/download_products_excel')
def download_products_excel():
    db = get_db()
    products = db.execute('SELECT sku, name, category, subcategory, description, material, color, sizes, hsn, gst, quantity1, price1, quantity2, price2, quantity3, price3, image_url FROM products').fetchall()
    df = pd.DataFrame(products, columns=['sku', 'name', 'category', 'subcategory', 'description', 'material', 'color', 'sizes', 'hsn', 'gst', 'quantity1', 'price1', 'quantity2', 'price2', 'quantity3', 'price3', 'image_url'])
    output = BytesIO()
    df.to_excel(output, index=False)
    output.seek(0)
    return send_file(output, download_name='products.xlsx', as_attachment=True, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
if __name__ == '__main__':
    create_tables()
    with app.app_context():
        auto_migrate_products_table()
        ensure_quotes_columns()
    app.run(debug=True)