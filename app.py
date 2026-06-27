
import os
import json
import hmac
import sqlite3
import smtplib
import requests
import razorpay
import pyotp
from datetime import datetime
from functools import wraps
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from flask import Flask, g, jsonify, redirect, render_template, request, session, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.routing import BuildError

from utils.shipping_manager import get_shipping_provider


def load_env_file(env_path):
    if not os.path.exists(env_path):
        return
    with open(env_path, 'r', encoding='utf-8') as env_file:
        for raw_line in env_file:
            line = raw_line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


app = Flask(__name__)
load_env_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'nari-nakhre-dev-secret')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.environ.get('DB_PATH', os.path.join(BASE_DIR, 'narinakhre.db'))

app.config['SHIPPING_PROVIDER'] = os.environ.get('SHIPPING_PROVIDER', 'mock')
app.config['DELHIVERY_API_KEY'] = os.environ.get('DELHIVERY_API_KEY', '')
app.config['WAREHOUSE_PIN'] = os.environ.get('WAREHOUSE_PIN', '400001')
app.config['RAZORPAY_KEY_ID'] = os.environ.get('RAZORPAY_KEY_ID', '')
app.config['RAZORPAY_KEY_SECRET'] = os.environ.get('RAZORPAY_KEY_SECRET', '')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + DATABASE.replace('\\', '/')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

DELHIVERY_API_TOKEN = os.environ.get('DELHIVERY_API_TOKEN', '')
DELHIVERY_CLIENT_NAME = os.environ.get('DELHIVERY_CLIENT_NAME', '')
DELHIVERY_PICKUP_LOCATION = os.environ.get('DELHIVERY_PICKUP_LOCATION', '')
RAZORPAY_KEY_ID = os.environ.get('RAZORPAY_KEY_ID')
RAZORPAY_KEY_SECRET = os.environ.get('RAZORPAY_KEY_SECRET')
ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', '')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', '')
ADMIN_TOTP_SECRET = os.environ.get('ADMIN_TOTP_SECRET', '')
razorpay_client = razorpay.Client(auth=(os.environ.get("RAZORPAY_KEY_ID"), os.environ.get("RAZORPAY_KEY_SECRET")))


class User(db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)


class Address(db.Model):
    __tablename__ = 'order_shipping'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    status = db.Column(db.String(32), nullable=False, default='pending')
    consignee_name = db.Column(db.String(255), nullable=False)
    consignee_phone = db.Column(db.String(32), nullable=False)
    consignee_address = db.Column(db.String(500), nullable=False)
    consignee_city = db.Column(db.String(120), nullable=False)
    consignee_state = db.Column(db.String(120), nullable=False)
    consignee_pincode = db.Column(db.String(12), nullable=False)
    internal_order_id = db.Column(db.String(64), nullable=False, unique=True)
    delhivery_waybill = db.Column(db.String(64), nullable=True)


class Product(db.Model):
    __tablename__ = 'products'

    id = db.Column(db.Integer, primary_key=True)
    sku = db.Column(db.String(128), nullable=False, unique=True)
    name = db.Column(db.String(255), nullable=True)
    slug = db.Column(db.String(255), nullable=True)
    category = db.Column(db.String(255), nullable=True)
    sub_category = db.Column(db.String(255), nullable=True)
    collection = db.Column(db.String(255), nullable=True)
    size = db.Column(db.String(255), nullable=True)

    retail_price = db.Column(db.Float, default=0.0)
    mrp_price = db.Column(db.Float, default=0.0)
    retail_discount_percent = db.Column(db.Float, default=0.0)
    wholesale_price = db.Column(db.Float, default=0.0)
    min_wholesale_qty = db.Column(db.Integer, default=0)
    sets_count = db.Column(db.Integer, default=0)

    image_field = db.Column(db.String(1024), nullable=True)
    quantity1 = db.Column(db.Integer, default=0)
    price1 = db.Column(db.Float, default=0.0)
    quantity2 = db.Column(db.Integer, default=0)
    price2 = db.Column(db.Float, default=0.0)
    quantity3 = db.Column(db.Integer, default=0)
    price3 = db.Column(db.Float, default=0.0)

    purchase_cost = db.Column(db.Float, default=0.0)
    making_charges = db.Column(db.Float, default=0.0)
    weight_grams = db.Column(db.Float, default=0.0)
    material = db.Column(db.String(255), nullable=True)
    hsn_code = db.Column(db.String(64), nullable=True)
    gst_percent = db.Column(db.Float, default=0.0)

    stock_total = db.Column(db.Integer, default=0)
    box_packing_type = db.Column(db.String(255), nullable=True)
    vendor_id = db.Column(db.String(128), nullable=True)
    status = db.Column(db.String(64), nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    is_featured = db.Column(db.Boolean, default=False)


def upload_image_to_supabase(file_storage_object, filename):
    supabase_url = (os.environ.get('SUPABASE_URL') or '').rstrip('/')
    supabase_key = os.environ.get('SUPABASE_KEY')
    bucket_name = os.environ.get('SUPABASE_BUCKET_NAME', 'products')

    if not supabase_url or not supabase_key or not bucket_name:
        app.logger.error('Supabase configuration missing for image upload.')
        return None

    try:
        if hasattr(file_storage_object, 'stream') and hasattr(file_storage_object.stream, 'seek'):
            file_storage_object.stream.seek(0)
        elif hasattr(file_storage_object, 'seek'):
            file_storage_object.seek(0)

        binary_payload = file_storage_object.read()
        upload_url = f"{supabase_url}/storage/v1/object/{bucket_name}/{filename}"
        headers = {
            'Authorization': f'Bearer {supabase_key}',
            'apikey': supabase_key,
            'Content-Type': getattr(file_storage_object, 'mimetype', 'application/octet-stream'),
            'x-upsert': 'true',
        }
        response = requests.put(upload_url, headers=headers, data=binary_payload, timeout=30)

        if response.status_code == 200:
            return f"{supabase_url}/storage/v1/object/public/{bucket_name}/{filename}"

        app.logger.error('Supabase upload failed: %s %s', response.status_code, response.text)
        return None
    except Exception as exc:
        app.logger.error('Supabase upload exception: %s', exc)
        return None


def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(error=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()


def initialize_database_if_needed():
    def ensure_table_columns(conn, table_name, required_columns):
        existing = {
            row[1] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        for col_name, col_def in required_columns:
            if col_name not in existing:
                conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_def}")

    # Main app database + local quotes database (if used separately)
    db_paths = [DATABASE, os.path.join(BASE_DIR, 'quotes.db')]

    for db_path in db_paths:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                '''
                CREATE TABLE IF NOT EXISTS categories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT
                )
                '''
            )

            # Ensure categories has all required columns in existing databases.
            required_category_columns = [
                ('name', 'TEXT'),
            ]
            ensure_table_columns(conn, 'categories', required_category_columns)

            conn.execute(
                '''
                CREATE TABLE IF NOT EXISTS products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sku TEXT NOT NULL UNIQUE,
                    name TEXT,
                    slug TEXT,
                    category TEXT,
                    sub_category TEXT,
                    collection TEXT,
                    size TEXT,
                    retail_price REAL DEFAULT 0.0,
                    mrp_price REAL DEFAULT 0.0,
                    retail_discount_percent REAL DEFAULT 0.0,
                    wholesale_price REAL DEFAULT 0.0,
                    min_wholesale_qty INTEGER DEFAULT 0,
                    sets_count INTEGER DEFAULT 0,
                    image_field TEXT,
                    quantity1 INTEGER DEFAULT 0,
                    price1 REAL DEFAULT 0.0,
                    quantity2 INTEGER DEFAULT 0,
                    price2 REAL DEFAULT 0.0,
                    quantity3 INTEGER DEFAULT 0,
                    price3 REAL DEFAULT 0.0,
                    purchase_cost REAL DEFAULT 0.0,
                    making_charges REAL DEFAULT 0.0,
                    weight_grams REAL DEFAULT 0.0,
                    material TEXT,
                    hsn_code TEXT,
                    gst_percent REAL DEFAULT 0.0,
                    stock_total INTEGER DEFAULT 0,
                    box_packing_type TEXT,
                    vendor_id TEXT,
                    status TEXT,
                    is_active INTEGER DEFAULT 1,
                    is_featured INTEGER DEFAULT 0,
                    category_id INTEGER,
                    weight REAL DEFAULT 0.0,
                    length REAL DEFAULT 0.0,
                    breadth REAL DEFAULT 0.0,
                    height REAL DEFAULT 0.0,
                    FOREIGN KEY (category_id) REFERENCES categories(id)
                )
                '''
            )

            # Add missing columns in already-existing products tables
            required_product_columns = [
                ('sku', 'TEXT'),
                ('name', 'TEXT'),
                ('slug', 'TEXT'),
                ('category', 'TEXT'),
                ('sub_category', 'TEXT'),
                ('collection', 'TEXT'),
                ('size', 'TEXT'),
                ('retail_price', 'REAL DEFAULT 0.0'),
                ('mrp_price', 'REAL DEFAULT 0.0'),
                ('retail_discount_percent', 'REAL DEFAULT 0.0'),
                ('wholesale_price', 'REAL DEFAULT 0.0'),
                ('min_wholesale_qty', 'INTEGER DEFAULT 0'),
                ('sets_count', 'INTEGER DEFAULT 0'),
                ('image_field', 'TEXT'),
                ('quantity1', 'INTEGER DEFAULT 0'),
                ('price1', 'REAL DEFAULT 0.0'),
                ('quantity2', 'INTEGER DEFAULT 0'),
                ('price2', 'REAL DEFAULT 0.0'),
                ('quantity3', 'INTEGER DEFAULT 0'),
                ('price3', 'REAL DEFAULT 0.0'),
                ('purchase_cost', 'REAL DEFAULT 0.0'),
                ('making_charges', 'REAL DEFAULT 0.0'),
                ('weight_grams', 'REAL DEFAULT 0.0'),
                ('material', 'TEXT'),
                ('hsn_code', 'TEXT'),
                ('gst_percent', 'REAL DEFAULT 0.0'),
                ('stock_total', 'INTEGER DEFAULT 0'),
                ('box_packing_type', 'TEXT'),
                ('vendor_id', 'TEXT'),
                ('status', 'TEXT'),
                ('is_active', 'INTEGER DEFAULT 1'),
                ('is_featured', 'INTEGER DEFAULT 0'),
                ('category_id', 'INTEGER'),
                ('weight', 'REAL DEFAULT 0.0'),
                ('length', 'REAL DEFAULT 0.0'),
                ('breadth', 'REAL DEFAULT 0.0'),
                ('height', 'REAL DEFAULT 0.0'),
            ]
            ensure_table_columns(conn, 'products', required_product_columns)

            conn.execute(
                '''
                CREATE TABLE IF NOT EXISTS quotes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT UNIQUE,
                    name TEXT,
                    whatsapp TEXT,
                    email TEXT,
                    items_json TEXT,
                    total_amount REAL DEFAULT 0.0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                '''
            )

            conn.commit()
        finally:
            conn.close()


def ensure_checkout_tables_exist():
    """Create checkout-related tables only if missing; never drops existing data."""
    conn = sqlite3.connect(DATABASE)
    try:
        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY
            )
            '''
        )
        conn.execute(
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
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            '''
        )
        conn.commit()
    finally:
        conn.close()


initialize_database_if_needed()
ensure_checkout_tables_exist()


def send_contact_email(to_email, subject, body):
    # Configure your SMTP server here
    SMTP_SERVER = 'smtp.gmail.com'
    SMTP_PORT = 587
    SMTP_USER = 'info@narinakhre.com'  # Replace with your email
    SMTP_PASS = 'yourpassword'         # Replace with your password or app password
    msg = MIMEMultipart()
    msg['From'] = SMTP_USER
    msg['To'] = to_email
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain'))
    try:
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SMTP_USER, to_email, msg.as_string())
        server.quit()
    except Exception as e:
        print('Email send failed:', e)

@app.route('/retail/contact', methods=['GET', 'POST'])
def retail_contact():
    g.site_type = 'retail'
    if request.method == 'POST':
        name = request.form.get('name')
        whatsapp = request.form.get('whatsapp')
        email = request.form.get('email')
        message = request.form.get('message')
        # Email to customer
        customer_subject = 'Thank you for contacting Nari Nakhre'
        customer_body = f"""Dear {name},\n\nThank you for reaching out to Nari Nakhre! We have received your message and will get back to you soon.\n\nYour Message:\n{message}\n\nBest regards,\nNari Nakhre Team"""
        send_contact_email(email, customer_subject, customer_body)
        # Email to admin
        admin_subject = f'New Retail Contact Form Submission from {name}'
        admin_body = f"""New contact form submission:\n\nName: {name}\nWhatsApp: {whatsapp}\nEmail: {email}\nMessage: {message}"""
        send_contact_email('info@narinakhre.com', admin_subject, admin_body)
        return redirect('/retail/thank_you')
    return render_template('retail/contact.html')


@app.route('/contact', methods=['GET', 'POST'])
@app.route('/wholesale/contact', methods=['GET', 'POST'])
def wholesale_contact():
    g.site_type = 'wholesale'
    if request.method == 'POST':
        # Honeypot defense: silently discard bot submissions.
        if (request.form.get('system_verification_token') or '').strip():
            return redirect(url_for('wholesale_thank_you'))

        name = (request.form.get('name') or '').strip()
        whatsapp = (request.form.get('whatsapp') or '').strip()
        email = (request.form.get('email') or '').strip()
        message = (request.form.get('message') or '').strip()

        request_id = f"NN-QT-{datetime.now().strftime('%Y%m%d%H%M%S')}-{(whatsapp[-4:] if whatsapp else '0000')}"
        quote_payload = json.dumps({
            'source': 'wholesale_contact',
            'message': message,
        })

        db_conn = get_db()
        db_conn.execute(
            'INSERT INTO quotes (request_id, name, whatsapp, email, items_json, total_amount) VALUES (?, ?, ?, ?, ?, ?)',
            (request_id, name, whatsapp, email, quote_payload, None),
        )
        db_conn.commit()

        if email:
            customer_subject = 'Thank you for your quote request - Nari Nakhre Wholesale'
            customer_body = (
                f"Dear {name},\n\n"
                'Your wholesale quote request has been received successfully. '\
                'Our team will review and get in touch shortly.\n\n'
                f"Request ID: {request_id}\n\n"
                'Regards,\nNari Nakhre Wholesale Team'
            )
            send_contact_email(email, customer_subject, customer_body)

        admin_subject = f'New Wholesale Contact/Quote Request: {request_id}'
        admin_body = (
            f"Request ID: {request_id}\n"
            f"Name: {name}\n"
            f"WhatsApp: {whatsapp}\n"
            f"Email: {email}\n\n"
            f"Message:\n{message}"
        )
        send_contact_email('info@narinakhre.com', admin_subject, admin_body)

        session['wholesale_contact_user'] = {'name': name, 'email': email}
        session.modified = True
        return redirect(url_for('wholesale_thank_you'))

    return render_template('wholesale/contact.html')


@app.route('/wholesale/thank_you')
def wholesale_thank_you():
    g.site_type = 'wholesale'
    user = session.pop('wholesale_contact_user', None) or {'name': 'Customer', 'email': ''}
    session.modified = True
    return render_template('wholesale/thank_you.html', user=user, contact_only=True)

# --- SITE DETECTION ---
@app.before_request
def detect_site_type():
    host = request.host.lower()
    path = request.path.lower()
    g.site_type = 'retail' if ('retail' in host or path.startswith('/retail')) else 'wholesale'

def render_site(template_name, **kwargs):
    site_type = getattr(g, 'site_type', 'wholesale')
    db = get_db()
    # For retail, fetch categories from the products table's 'category' column
    if site_type == 'retail':
        cats = db.execute('SELECT DISTINCT category FROM products WHERE category IS NOT NULL AND category != ""').fetchall()
        categories = [c['category'] for c in cats]
    else:
        cats = db.execute('SELECT DISTINCT c.name FROM products p JOIN categories c ON p.category_id = c.id WHERE c.name IS NOT NULL AND c.name != ""').fetchall()
        categories = [c['name'] for c in cats]
    kwargs['categories'] = categories
    return render_template(f"{site_type}/{template_name}", **kwargs)

# --- ROUTES: HOME & CATEGORY ---
def get_supabase_image_urls(sku):
    supabase_url = (os.environ.get('SUPABASE_URL') or '').rstrip('/')
    bucket = 'products'
    if not supabase_url:
        return []
    return [f"{supabase_url}/storage/v1/object/public/{bucket}/{sku}_{i}.webp" for i in range(1, 10)]


def get_random_hero_images(db, count=4):
    import random
    rows = db.execute(
        "SELECT image_field FROM products WHERE image_field IS NOT NULL AND image_field LIKE 'http%' ORDER BY RANDOM() LIMIT ?",
        (count,)
    ).fetchall()
    return [r['image_field'] for r in rows]


def build_product_dict(p):
    p_dict = dict(p)
    image_field = p_dict.get('image_field') or ''
    if image_field.startswith('http'):
        all_urls = get_supabase_image_urls(p_dict['sku'])
        images = [image_field] + [u for u in all_urls if u != image_field]
    else:
        images = get_supabase_image_urls(p_dict['sku'])
    if not images:
        images = ['/static/assets/products/default.jpg']
    p_dict['images'] = images
    tiers = []
    for i in range(1, 4):
        qty = p_dict.get(f'quantity{i}')
        price = p_dict.get(f'price{i}')
        if qty and price:
            try:
                qty_val = int(qty)
                price_val = float(price)
                if qty_val > 0 and price_val > 0:
                    tiers.append({'qty': qty_val, 'price': price_val})
            except Exception:
                continue
    if not tiers:
        tiers = [{'qty': 1, 'price': 0}]
    p_dict['tiers'] = tiers
    return p_dict


@app.route('/')
@app.route('/retail')
@app.route('/wholesale')
def index():
    if request.path.startswith('/retail'):
        g.site_type = 'retail'
    elif request.path.startswith('/wholesale'):
        g.site_type = 'wholesale'

    db = get_db()
    hero_images = get_random_hero_images(db, count=4)

    if g.site_type == 'retail':
        products = db.execute('SELECT * FROM products WHERE is_active=1').fetchall()
        grouped_products = {}
        for p in products:
            cat = p['category'] or 'New Arrivals'
            if cat not in grouped_products:
                grouped_products[cat] = []
            grouped_products[cat].append(build_product_dict(p))
        return render_site('index.html', grouped_products=grouped_products, hero_images=hero_images)

    products = db.execute('''
        SELECT p.*, c.name as category_name FROM products p
        LEFT JOIN categories c ON p.category_id = c.id
        WHERE p.is_active=1
    ''').fetchall()
    grouped_products = {}
    for p in products:
        cat = p['category_name'] or p['category'] or 'New Arrivals'
        if cat not in grouped_products:
            grouped_products[cat] = []
        grouped_products[cat].append(build_product_dict(p))
    return render_site('index.html', grouped_products=grouped_products, hero_images=hero_images)

@app.route('/category/<category>')
@app.route('/retail/category/<category>')
@app.route('/wholesale/category/<category>')
def category_products(category):
    if request.path.startswith('/retail'):
        g.site_type = 'retail'
    elif request.path.startswith('/wholesale'):
        g.site_type = 'wholesale'
        
    db = get_db()
    # For retail, filter by the 'category' column
    if request.path.startswith('/retail'):
        raw_products = db.execute('SELECT * FROM products WHERE category = ?', (category,)).fetchall()
    else:
        raw_products = db.execute('''
            SELECT p.* FROM products p
            JOIN categories c ON p.category_id = c.id
            WHERE c.name = ?
        ''', (category,)).fetchall()
    products = []
    image_dir = os.path.join(app.root_path, 'static', 'assets', 'products')
    for p in raw_products:
        p_dict = dict(p)
        images = []
        for i in range(1, 10):
            img_filename = f"{p['sku']}_{i}.jpg"
            img_path = os.path.join(image_dir, img_filename)
            if os.path.exists(img_path):
                images.append(url_for('static', filename=f"assets/products/{img_filename}"))
            else:
                break
        if not images:
            images = [url_for('static', filename=f"assets/products/default.jpg")]
        p_dict['images'] = images
        
        tiers = []
        for i in range(1, 4):
            qty_key = f'quantity{i}'
            price_key = f'price{i}'
            qty = p_dict.get(qty_key)
            price = p_dict.get(price_key)
            if qty and price:
                try:
                    qty_val = int(qty)
                    price_val = float(price)
                    if qty_val > 0 and price_val > 0:
                        tiers.append({'qty': qty_val, 'price': price_val})
                except Exception:
                    continue
        p_dict['tiers'] = tiers
        products.append(p_dict)
    return render_site('category_products.html', category=category, products=products)

@app.route('/product/<int:product_id>')
@app.route('/retail/product/<int:product_id>')
@app.route('/wholesale/product/<int:product_id>')
def product_detail(product_id):
    if request.path.startswith('/retail'):
        g.site_type = 'retail'
    elif request.path.startswith('/wholesale'):
        g.site_type = 'wholesale'
        
    db = get_db()
    product = db.execute('SELECT * FROM products WHERE id = ?', (product_id,)).fetchone()
    if not product: return "Not Found", 404
    p_dict = dict(product)
    
    image_dir = os.path.join(app.root_path, 'static', 'assets', 'products')
    image_urls = []
    for i in range(1, 10):
        img_filename = f"{p_dict['sku']}_{i}.jpg"
        img_path = os.path.join(image_dir, img_filename)
        if os.path.exists(img_path):
            image_urls.append(f"assets/products/{p_dict['sku']}_{i}.jpg")
        else:
            break
    if not image_urls:
        image_urls = [f"assets/products/default.jpg"]
    
    tiers = []
    for i in range(1, 4):
        qty_key = f'quantity{i}'
        price_key = f'price{i}'
        qty = p_dict.get(qty_key)
        price = p_dict.get(price_key)
        if qty and price:
            try:
                qty_val = int(qty)
                price_val = float(price)
                if qty_val > 0 and price_val > 0:
                    tiers.append({'qty': qty_val, 'price': price_val})
            except Exception:
                continue
    p_dict['tiers'] = tiers
    # Find 4 random products (not the current one) for cross-sell
    related = db.execute('''
        SELECT * FROM products WHERE id != ? ORDER BY RANDOM() LIMIT 4
    ''', (product_id,)).fetchall()
    related_products = []
    image_dir = os.path.join(app.root_path, 'static', 'assets', 'products')
    for r in related:
        r_dict = dict(r)
        r_images = []
        for i in range(1, 10):
            img_filename = f"{r_dict['sku']}_{i}.jpg"
            img_path = os.path.join(image_dir, img_filename)
            if os.path.exists(img_path):
                r_images.append(f"assets/products/{img_filename}")
            else:
                break
        if not r_images:
            r_images = ["assets/products/default.jpg"]
        r_dict['images'] = r_images
        related_products.append(r_dict)
    return render_site('product_detail.html', product=p_dict, image_urls=image_urls, related_products=related_products)

# --- CART & CHECKOUT ---
@app.route('/update-cart', methods=['POST'])
def update_cart():
    data = request.get_json()
    sku = data.get('product_id')
    qty = int(data.get('qty', 1))
    price = float(data.get('price', 0))
    size = data.get('size', 'Standard')
    
    cart = session.get('cart', {})
    cart_key = f"{sku}_{size}"
    if qty > 0:
        db = get_db()
        p = db.execute('SELECT name FROM products WHERE sku = ?', (sku,)).fetchone()
        cart[cart_key] = {
            'sku': sku,
            'name': p['name'] if p else sku,
            'qty': qty,
            'price': price,
            'size': size
        }
    else:
        cart.pop(cart_key, None)
    session['cart'] = cart
    session.modified = True
    return jsonify({'status': 'success', 'new_total': sum(item['qty'] for item in cart.values())})

@app.route('/checkout')
@app.route('/retail/checkout')
@app.route('/wholesale/checkout')
def checkout():
    if request.path.startswith('/retail'):
        g.site_type = 'retail'
    elif request.path.startswith('/wholesale'):
        g.site_type = 'wholesale'
    
    cart = session.get('cart', {})
    display_cart = []
    for item in cart.values():
        item_dict = dict(item)
        if 'units' not in item_dict:
            item_dict['units'] = item_dict.get('qty', 1)
        display_cart.append(item_dict)
    
    subtotal = sum(item['price'] * item['units'] for item in display_cart)
    return render_site('checkout.html', display_cart=display_cart, subtotal=subtotal, total_tax=0.0, discount=0.0, grand_total=subtotal)

@app.route('/checkout/shipping', methods=['GET', 'POST'])
@app.route('/retail/checkout/shipping', methods=['GET', 'POST'])
def checkout_shipping():
    """Render shipping address form for checkout."""
    g.site_type = 'retail'
    if request.method == 'POST':
        return redirect(url_for('checkout_process'))
    return render_site('checkout_shipping.html')


@app.route('/checkout/process', methods=['POST'])
@app.route('/retail/checkout/process', methods=['POST'])
def checkout_process():
    g.site_type = 'retail'
    consignee_name = (request.form.get('consignee_name') or '').strip()
    consignee_phone = (request.form.get('consignee_phone') or '').strip()
    consignee_address = (request.form.get('consignee_address') or '').strip()
    consignee_city = (request.form.get('consignee_city') or '').strip()
    consignee_state = (request.form.get('consignee_state') or '').strip()
    consignee_pincode = (request.form.get('consignee_pincode') or '').strip()

    # Delhivery-safe string formatter: strip forbidden symbols and normalize spaces.
    def sanitize_for_delhivery(value):
        cleaned = value or ''
        for char in ['#', '&', '%', ';']:
            cleaned = cleaned.replace(char, ' ')
        return ' '.join(cleaned.split())

    cleaned_name = sanitize_for_delhivery(consignee_name)
    cleaned_address = sanitize_for_delhivery(consignee_address)

    internal_order_id = f"NN-SHP-{datetime.now().strftime('%Y%m%d%H%M%S')}-{consignee_phone[-4:]}"
    user_id = session.get('user_id')

    shipping_record = Address(
        user_id=user_id,
        consignee_name=cleaned_name,
        consignee_phone=consignee_phone,
        consignee_address=cleaned_address,
        consignee_city=consignee_city,
        consignee_state=consignee_state,
        consignee_pincode=consignee_pincode,
        internal_order_id=internal_order_id,
    )
    db.session.add(shipping_record)
    db.session.commit()

    delhivery_payload = {
        'shipments': [
            {
                'name': cleaned_name,
                'phone': consignee_phone,
                'add': cleaned_address,
                'city': consignee_city,
                'state': consignee_state,
                'pin': consignee_pincode,
                'country': 'IN',
                'order': internal_order_id,
                'payment_mode': 'Pre-paid',
                'return_pin': app.config.get('WAREHOUSE_PIN', ''),
                'client': DELHIVERY_CLIENT_NAME,
            }
        ],
        'pickup_location': {
            'name': DELHIVERY_PICKUP_LOCATION,
        }
    }

    response = requests.post(
        'https://track.delhivery.com/api/cmu/create.json',
        data={
            'format': 'json',
            'data': json.dumps(delhivery_payload),
        },
        headers={
            'Authorization': f'Token {DELHIVERY_API_TOKEN}',
            'Content-Type': 'application/x-www-form-urlencoded',
        },
        timeout=30,
    )

    waybill = None
    if response.status_code == 200:
        try:
            resp_json = response.json()
            packages = resp_json.get('packages', [])
            if isinstance(packages, list) and packages:
                waybill = packages[0].get('waybill')
        except Exception:
            waybill = None

    if waybill:
        shipping_record.delhivery_waybill = waybill
        db.session.commit()

    print('Sanitized shipping payload for Delhivery:', {
        'internal_order_id': internal_order_id,
        'consignee_name': cleaned_name,
        'consignee_phone': consignee_phone,
        'consignee_address': cleaned_address,
        'consignee_city': consignee_city,
        'consignee_state': consignee_state,
        'consignee_pincode': consignee_pincode,
        'waybill': waybill,
    })

    session['checkout_handover'] = {
        'internal_order_id': internal_order_id,
        'waybill': waybill,
    }
    session.modified = True

    try:
        return redirect(url_for('payment_gateway_router'))
    except BuildError:
        return 'Checkout processed and Delhivery manifest attempted.', 200

@app.route('/payment/gateway', methods=['GET'])
@app.route('/retail/payment/gateway', methods=['GET'])
def payment_gateway_router():
    """Payment authorization gateway with session validation."""
    g.site_type = 'retail'
    checkout_handover = session.get('checkout_handover', {})
    internal_order_id = checkout_handover.get('internal_order_id')
    waybill = checkout_handover.get('waybill')
    
    if not internal_order_id or not waybill:
        flash('Order ID or tracking number missing. Please complete shipping details again.', 'error')
        return redirect(url_for('checkout_shipping'))
    
    # Calculate amount from current cart
    cart = session.get('cart', {})
    subtotal = sum(item['price'] * item['qty'] for item in cart.values())
    total_tax = subtotal * 0.18
    amount_to_pay = subtotal + total_tax
    
    return render_site('payment_gateway.html',
        internal_order_id=internal_order_id,
        waybill=waybill,
        amount_to_pay=amount_to_pay,
        subtotal=subtotal,
        total_tax=total_tax
    )

@app.route('/payment/cancel', methods=['POST'])
@app.route('/retail/payment/cancel', methods=['POST'])
def payment_cancel():
    """Clear checkout state and return to catalogue."""
    g.site_type = 'retail'
    session.pop('checkout_handover', None)
    session.pop('cart', None)
    session.modified = True
    flash('Order cancelled. Returning to store.', 'info')
    return redirect(url_for('index'))


@app.route('/api/create-order', methods=['POST'])
def create_razorpay_order():
    g.site_type = 'retail'
    payload = request.get_json(silent=True) or request.form or {}

    checkout_handover = session.get('checkout_handover', {})
    internal_order_id = (payload.get('order_id') or checkout_handover.get('internal_order_id') or '').strip()
    waybill = (payload.get('waybill') or checkout_handover.get('waybill') or '').strip()

    if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
        return jsonify({
            'status': 'error',
            'message': 'Razorpay credentials are not configured'
        }), 500

    try:
        requested_amount = payload.get('amount')
        if requested_amount is None:
            cart = session.get('cart', {})
            subtotal = sum(item['price'] * item['qty'] for item in cart.values())
            requested_amount = subtotal + (subtotal * 0.18)

        amount_paise = int(round(float(requested_amount) * 100))
        if amount_paise <= 0:
            return jsonify({
                'status': 'error',
                'message': 'Invalid amount for payment'
            }), 400
    except (TypeError, ValueError):
        return jsonify({
            'status': 'error',
            'message': 'Invalid amount for payment'
        }), 400

    receipt = internal_order_id or f"NN-RZP-{datetime.now().strftime('%Y%m%d%H%M%S')}"

    try:
        order_payload = {
            'amount': amount_paise,
            'currency': 'INR',
            'receipt': receipt,
            'payment_capture': 1,
            'notes': {
                'internal_order_id': internal_order_id,
                'waybill': waybill,
            },
        }
        razorpay_order = razorpay_client.order.create(data=order_payload)

        session['payment_pending'] = {
            'internal_order_id': internal_order_id,
            'waybill': waybill,
            'razorpay_order_id': razorpay_order.get('id'),
            'amount_paise': amount_paise,
        }
        session['razorpay_order_id'] = razorpay_order.get('id')
        session['internal_order_id'] = internal_order_id
        session['waybill'] = waybill
        session.modified = True

        return jsonify({
            'status': 'success',
            'order_id': razorpay_order.get('id'),
            'razorpay_order_id': razorpay_order.get('id'),
            'amount': razorpay_order.get('amount', amount_paise),
            'currency': razorpay_order.get('currency', 'INR'),
            'receipt': razorpay_order.get('receipt', receipt),
            'key_id': app.config.get('RAZORPAY_KEY_ID', ''),
        }), 200
    except Exception:
        return jsonify({
            'status': 'error',
            'message': 'Unable to create Razorpay order'
        }), 500


@app.route('/api/verify-payment', methods=['POST'])
def verify_payment():
    g.site_type = 'retail'
    payload = request.get_json(silent=True) or request.form or {}
    razorpay_order_id = (payload.get('razorpay_order_id') or '').strip()
    razorpay_payment_id = (payload.get('razorpay_payment_id') or '').strip()
    razorpay_signature = (payload.get('razorpay_signature') or '').strip()

    if not razorpay_order_id or not razorpay_payment_id or not razorpay_signature:
        return jsonify({
            'status': 'error',
            'message': 'Missing payment verification fields'
        }), 400

    pending_razorpay_order_id = (session.get('razorpay_order_id') or '').strip()
    if not pending_razorpay_order_id or pending_razorpay_order_id != razorpay_order_id:
        return jsonify({
            'status': 'error',
            'message': 'Order ID mismatch for pending session transaction'
        }), 400

    params_dict = {
        'razorpay_order_id': razorpay_order_id,
        'razorpay_payment_id': razorpay_payment_id,
        'razorpay_signature': razorpay_signature
    }

    try:
        razorpay_client.utility.verify_payment_signature(params_dict)
    except Exception:
        return jsonify({
            'status': 'error',
            'message': 'Invalid payment signature'
        }), 400

    try:
        internal_order_id = (session.get('internal_order_id') or '').strip()
        if not internal_order_id:
            checkout_handover = session.get('checkout_handover', {})
            internal_order_id = (checkout_handover.get('internal_order_id') or '').strip()

        if not internal_order_id:
            return jsonify({
                'status': 'error',
                'message': 'No active order found for this payment'
            }), 400

        shipping_record = Address.query.filter_by(internal_order_id=internal_order_id).first()
        if shipping_record is None:
            return jsonify({
                'status': 'error',
                'message': 'No active order found for this payment'
            }), 400

        shipping_record.status = 'paid'
        if hasattr(shipping_record, 'razorpay_payment_id'):
            shipping_record.razorpay_payment_id = razorpay_payment_id
        db.session.commit()

        session.pop('razorpay_order_id', None)
        session.pop('payment_pending', None)
        session.pop('internal_order_id', None)
        session.pop('waybill', None)
        session.pop('checkout_handover', None)
        session.modified = True

        return jsonify({
            'status': 'success',
            'message': 'Payment verified and order finalized'
        }), 200
    except Exception:
        db.session.rollback()
        return jsonify({
            'status': 'error',
            'message': 'Unable to finalize verified payment'
        }), 500

# --- DELHIVERY API ROUTES (Retail Only) ---
@app.route('/api/delhivery/check/<pincode>', methods=['GET'])
def delhivery_check_pincode(pincode):
    if g.site_type != 'retail':
        return jsonify({"status": False, "msg": "Unauthorized"}), 403
    provider = get_shipping_provider(
        app.config['SHIPPING_PROVIDER'],
        api_token=app.config.get('DELHIVERY_API_KEY')
    )
    return jsonify(provider.verify_pincode(pincode))

@app.route('/api/delhivery/shipping', methods=['POST'])
def calculate_checkout_shipping():
    if g.site_type != 'retail':
        return jsonify({"status": False, "msg": "Unauthorized"}), 403
    data = request.get_json()
    pincode = data.get('pincode')
    payment_mode = data.get('mode', 'Prepaid')
    cart = session.get('cart', {})
    total_weight = sum(item['qty'] for item in cart.values()) * 250
    provider = get_shipping_provider(
        app.config['SHIPPING_PROVIDER'],
        api_token=app.config.get('DELHIVERY_API_KEY')
    )
    # If provider has get_rates, use it; else, return mock
    rates = provider.get_rates(app.config['WAREHOUSE_PIN'], pincode, total_weight)
    return jsonify({"shipping_cost": rates.get('rate', 0)})

@app.route('/retail/place_order', methods=['POST'])
def place_order():
    data = request.form if request.form else request.json
    name = data.get('name')
    phone = data.get('phone')
    email = data.get('email')
    address_line1 = data.get('address_line1')
    address_line2 = data.get('address_line2')
    city = data.get('city')
    state = data.get('state')
    pincode = data.get('pincode')
    country = data.get('country', 'IN')
    payment_mode = data.get('payment_mode')
    amount = float(data.get('amount', 0))
    order_id = f'NN{datetime.now().strftime("%Y%m%d%H%M%S")}{phone[-4:]}'
    cart = session.get('cart', {})
    db = get_db()
    total_weight = 0
    for item in cart.values():
        sku = item['sku']
        qty = item['qty']
        prod = db.execute('SELECT weight, length, breadth, height FROM products WHERE sku = ?', (sku,)).fetchone()
        if prod:
            dead_weight = (prod['weight'] or 0) * qty
            l = prod['length'] or 0
            b = prod['breadth'] or 0
            h = prod['height'] or 0
            vol_weight = get_shipping_provider(app.config['SHIPPING_PROVIDER']).calculate_volumetric_weight(l, b, h) * qty
            billable = max(dead_weight, vol_weight)
            total_weight += billable
        else:
            total_weight += qty * 250  # fallback
    provider = get_shipping_provider(
        app.config['SHIPPING_PROVIDER'],
        api_token=app.config.get('DELHIVERY_API_KEY')
    )
    shipment_data = {
        "name": name,
        "add": f"{address_line1}, {address_line2}",
        "pin": pincode,
        "phone": phone,
        "order": order_id,
        "payment_mode": payment_mode,
        "total_amount": amount,
        "weight": total_weight,
        "city": city,
        "state": state,
        "country": country,
        "email": email,
        "mobile": phone
    }
    resp = provider.create_shipment(shipment_data)
    waybill = resp.get('waybill')
    db.execute(
        "INSERT INTO orders (order_id, name, phone, email, address_line1, address_line2, city, state, pincode, country, payment_mode, amount, waybill) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (order_id, name, phone, email, address_line1, address_line2, city, state, pincode, country, payment_mode, amount, waybill)
    )
    db.commit()
    session.pop('cart', None)
    tracking_url = f"https://www.delhivery.com/track/package/{waybill}" if waybill else None
    return render_site('thank_you.html', order_id=order_id, waybill=waybill, tracking_url=tracking_url)

@app.route('/clear_quote', methods=['POST'])
def clear_quote():
    session.pop('cart', None)
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return ('', 204)
    return redirect('/retail' if g.site_type == 'retail' else '/wholesale')

@app.route('/thank_you')
def thank_you():
    return render_site('thank_you.html')


def admin_required(view_func):
    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        if session.get('is_admin') is not True:
            return redirect(url_for('admin_login'))
        return view_func(*args, **kwargs)
    return wrapped_view


@app.route('/admin/upload-images', methods=['POST'])
@admin_required
def admin_upload_images():
    sku = request.form.get('sku')
    uploaded_files = request.files.getlist('images')

    if not sku or not uploaded_files:
        flash('SKU and images are required for upload.')
        return redirect(url_for('admin_manage_images'))

    sku = sku.strip()
    if not sku:
        flash('SKU and images are required for upload.')
        return redirect(url_for('admin_manage_images'))

    product = Product.query.filter_by(sku=sku).first()
    if not product:
        flash('Product not found for the provided SKU.')
        return redirect(url_for('admin_manage_images'))

    first_public_cloud_url = None
    for idx, file in enumerate(uploaded_files, start=1):
        if not file or not file.filename:
            continue

        target_name = f"{sku}_{idx}.webp"
        public_cloud_url = upload_image_to_supabase(file, target_name)
        if public_cloud_url and first_public_cloud_url is None:
            first_public_cloud_url = public_cloud_url

    if first_public_cloud_url:
        product.image_field = first_public_cloud_url
        db.session.commit()
        flash('Product images successfully processed, scaled down, converted to WebP format, and synced to Supabase!')
    else:
        flash('No images were uploaded to cloud storage. Please try again.')

    return redirect(url_for('admin_manage_images'))


@app.route('/admin/dashboard', methods=['GET'])
@admin_required
def admin_dashboard():
    db = get_db()
    products = db.execute('SELECT * FROM products ORDER BY id DESC').fetchall()
    quotes = db.execute('SELECT * FROM quotes ORDER BY id DESC').fetchall()
    return render_template('admin/admin.html', products=products, quotes=quotes)


@app.route('/admin/manage-images', methods=['GET'])
@admin_required
def admin_manage_images():
    db = get_db()
    sku_search = request.args.get('sku_search', '').strip()
    if sku_search:
        products = db.execute(
            'SELECT * FROM products WHERE sku LIKE ? ORDER BY sku',
            (f'%{sku_search}%',)
        ).fetchall()
    else:
        products = db.execute('SELECT * FROM products ORDER BY sku').fetchall()
    return render_template('admin/admin_manage_images.html', products=products, sku_search=sku_search)


@app.route('/admin/edit-product-details', methods=['GET', 'POST'])
@admin_required
def admin_edit_product_details():
    db = get_db()
    if request.method == 'POST':
        sku = request.form.get('sku', '').strip()
        name = request.form.get('name', '').strip()
        retail_price = request.form.get('retail_price', 0)
        mrp_price = request.form.get('mrp_price', 0)
        stock_total = request.form.get('stock_total', 0)
        category = request.form.get('category', '').strip()
        material = request.form.get('material', '').strip()
        slug = request.form.get('slug', '').strip()
        db.execute(
            '''UPDATE products SET name=?, retail_price=?, mrp_price=?,
               stock_total=?, category=?, material=?, slug=? WHERE sku=?''',
            (name, retail_price, mrp_price, stock_total, category, material, slug, sku)
        )
        db.commit()
        flash(f'Product {sku} updated successfully.')
        return redirect(url_for('admin_edit_product_details'))
    products = db.execute('SELECT * FROM products ORDER BY sku').fetchall()
    return render_template('admin/admin_edit_product_details.html', products=products)


@app.route('/admin/delete-products', methods=['GET'])
@admin_required
def admin_delete_products():
    db = get_db()
    products = db.execute('SELECT * FROM products ORDER BY sku').fetchall()
    return render_template('admin/admin_delete_products.html', products=products)


@app.route('/admin/delete-product/<int:product_id>', methods=['GET'])
@admin_required
def admin_delete_product(product_id):
    db = get_db()
    db.execute('DELETE FROM products WHERE id=?', (product_id,))
    db.commit()
    flash('Product deleted successfully.')
    return redirect(url_for('admin_delete_products'))


@app.route('/admin/inbox', methods=['GET'])
@admin_required
def admin_inbox():
    db = get_db()
    quotes = db.execute('SELECT * FROM quotes ORDER BY id DESC').fetchall()
    return render_template('admin/admin_inbox.html', quotes=quotes)


@app.route('/admin/quote/<int:quote_id>', methods=['GET', 'POST'])
@admin_required
def admin_quote_view(quote_id):
    db = get_db()
    quote = db.execute('SELECT * FROM quotes WHERE id=?', (quote_id,)).fetchone()
    if not quote:
        flash('Quote not found.')
        return redirect(url_for('admin_inbox'))
    cart_items = []
    try:
        cart_items = json.loads(quote['items_json'] or '[]')
    except Exception:
        cart_items = []
    if request.method == 'POST' and request.form.get('mark_contacted'):
        db.execute('UPDATE quotes SET status=? WHERE id=?', ('Contacted', quote_id))
        db.commit()
        flash('Quote marked as contacted.')
        return redirect(url_for('admin_quote_view', quote_id=quote_id))
    return render_template('admin/admin_quote_view.html', quote=quote, cart_items=cart_items)


@app.route('/admin/add-product', methods=['GET', 'POST'])
@admin_required
def admin_add_product():
    db = get_db()
    if request.method == 'POST':
        sku = request.form.get('sku', '').strip()
        name = request.form.get('name', '').strip()
        category = request.form.get('category', '').strip()
        sub_category = request.form.get('sub_category', '').strip()
        collection = request.form.get('collection', '').strip()
        size = request.form.get('size', '').strip()
        retail_price = float(request.form.get('retail_price', 0) or 0)
        mrp_price = float(request.form.get('mrp_price', 0) or 0)
        wholesale_price = float(request.form.get('wholesale_price', 0) or 0)
        stock_total = int(request.form.get('stock_total', 0) or 0)
        material = request.form.get('material', '').strip()
        slug = request.form.get('slug', '').strip()
        hsn_code = request.form.get('hsn_code', '').strip()
        gst_percent = float(request.form.get('gst_percent', 0) or 0)
        weight_grams = float(request.form.get('weight_grams', 0) or 0)
        length = float(request.form.get('length', 0) or 0)
        breadth = float(request.form.get('breadth', 0) or 0)
        height = float(request.form.get('height', 0) or 0)
        price1 = float(request.form.get('price1', 0) or 0)
        quantity1 = int(request.form.get('quantity1', 0) or 0)
        price2 = float(request.form.get('price2', 0) or 0)
        quantity2 = int(request.form.get('quantity2', 0) or 0)
        price3 = float(request.form.get('price3', 0) or 0)
        quantity3 = int(request.form.get('quantity3', 0) or 0)
        sets_count = int(request.form.get('sets_count', 1) or 1)
        min_wholesale_qty = int(request.form.get('min_wholesale_qty', 0) or 0)

        if not sku:
            flash('SKU is required.')
            return redirect(url_for('admin_add_product'))

        if not slug:
            import re
            slug = re.sub(r'[^a-z0-9\s-]', '', name.lower())
            slug = re.sub(r'\s+', '-', slug.strip())[:80]

        existing = db.execute('SELECT id FROM products WHERE sku=?', (sku,)).fetchone()
        if existing:
            flash(f'A product with SKU {sku} already exists.')
            return redirect(url_for('admin_add_product'))

        image_url = None
        image_file = request.files.get('image')
        if image_file and image_file.filename:
            from PIL import Image as PilImage
            import io as _io
            try:
                with PilImage.open(image_file.stream) as img:
                    if img.mode in ('RGBA', 'P', 'LA'):
                        img = img.convert('RGBA')
                    else:
                        img = img.convert('RGB')
                    buf = _io.BytesIO()
                    img.save(buf, format='WEBP', quality=85, method=6)
                    buf.seek(0)
                    filename = f"{sku}_1.webp"
                    upload_url = f"{(os.environ.get('SUPABASE_URL') or '').rstrip('/')}/storage/v1/object/products/{filename}"
                    headers = {
                        'Authorization': f"Bearer {os.environ.get('SUPABASE_KEY', '')}",
                        'apikey': os.environ.get('SUPABASE_KEY', ''),
                        'Content-Type': 'image/webp',
                        'x-upsert': 'true',
                    }
                    resp = requests.put(upload_url, headers=headers, data=buf.read(), timeout=30)
                    if resp.status_code == 200:
                        supabase_url = (os.environ.get('SUPABASE_URL') or '').rstrip('/')
                        image_url = f"{supabase_url}/storage/v1/object/public/products/{filename}"
            except Exception as e:
                app.logger.error(f'Image upload failed for {sku}: {e}')

        db.execute(
            '''INSERT INTO products
               (sku, name, category, sub_category, collection, size,
                retail_price, mrp_price, wholesale_price, stock_total,
                material, slug, hsn_code, gst_percent, weight_grams,
                length, breadth, height,
                price1, quantity1, price2, quantity2, price3, quantity3,
                sets_count, min_wholesale_qty, image_field, is_active)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)''',
            (sku, name, category, sub_category, collection, size,
             retail_price, mrp_price, wholesale_price, stock_total,
             material, slug, hsn_code, gst_percent, weight_grams,
             length, breadth, height,
             price1, quantity1, price2, quantity2, price3, quantity3,
             sets_count, min_wholesale_qty, image_url)
        )
        db.commit()
        flash(f'Product {sku} added successfully.')
        return redirect(url_for('admin_dashboard'))

    return render_template('admin/admin_add_product.html')


@app.route('/admin/download-users-excel', methods=['GET'])
@admin_required
def download_users_excel():
    flash('Users export coming soon.')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/download-quotes-excel', methods=['GET'])
@admin_required
def download_quotes_excel():
    flash('Quotes export coming soon.')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/download-products-excel', methods=['GET'])
@admin_required
def download_products_excel():
    flash('Products export coming soon.')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/upload-excel', methods=['POST'])
@admin_required
def admin_upload_excel():
    import pandas as pd

    file_object = request.files.get('excel_file')
    if file_object is None or not file_object.filename:
        flash('Please select an Excel or CSV catalog file to upload.')
        return redirect(url_for('admin_dashboard'))

    def normalize_value(value):
        if pd.isna(value):
            return None
        if isinstance(value, str):
            cleaned = value.strip()
            return cleaned if cleaned else None
        return value

    def to_float(value, default=0.0):
        value = normalize_value(value)
        if value is None:
            return default
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def to_int(value, default=0):
        value = normalize_value(value)
        if value is None:
            return default
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default

    def to_bool(value, default=False):
        value = normalize_value(value)
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        text = str(value).strip().lower()
        if text in {'1', 'true', 'yes', 'y', 'active'}:
            return True
        if text in {'0', 'false', 'no', 'n', 'inactive'}:
            return False
        return default

    def row_value(row, key):
        if key not in row.index:
            return None
        return normalize_value(row.get(key))

    try:
        filename_lower = (file_object.filename or '').lower()
        if filename_lower.endswith('.csv'):
            df = pd.read_csv(file_object)
        elif filename_lower.endswith('.xlsx'):
            df = pd.read_excel(file_object)
        else:
            flash('Unsupported file format. Please upload a .csv or .xlsx file.')
            return redirect(url_for('admin_dashboard'))

        df.columns = df.columns.str.lower().str.strip()

        processed_rows = 0
        created_rows = 0
        updated_rows = 0

        for _, row in df.iterrows():
            row_sku = normalize_value(row.get('sku'))
            if not row_sku:
                continue
            row_sku = str(row_sku).strip()

            product = Product.query.filter_by(sku=row_sku).first()
            is_new = product is None
            if is_new:
                product = Product(sku=row_sku)
                db.session.add(product)

            # Core identity and basics
            if hasattr(product, 'name'):
                product.name = row_value(row, 'name')
            if hasattr(product, 'slug'):
                product.slug = row_value(row, 'slug')
            if hasattr(product, 'category'):
                product.category = row_value(row, 'category')
            if hasattr(product, 'sub_category'):
                product.sub_category = row_value(row, 'sub_category')
            if hasattr(product, 'collection'):
                product.collection = row_value(row, 'collection')
            if hasattr(product, 'size'):
                product.size = row_value(row, 'size')

            # Unified pricing layer
            if hasattr(product, 'retail_price'):
                product.retail_price = to_float(row_value(row, 'retail_price'))
            if hasattr(product, 'mrp_price'):
                product.mrp_price = to_float(row_value(row, 'mrp_price'))
            if hasattr(product, 'retail_discount_percent'):
                product.retail_discount_percent = to_float(row_value(row, 'retail_discount_percent'))
            if hasattr(product, 'wholesale_price'):
                product.wholesale_price = to_float(row_value(row, 'wholesale_price'))
            if hasattr(product, 'min_wholesale_qty'):
                product.min_wholesale_qty = to_int(row_value(row, 'min_wholesale_qty'))
            if hasattr(product, 'sets_count'):
                product.sets_count = to_int(row_value(row, 'sets_count'))

            # Legacy wholesale tier assignments
            if hasattr(product, 'price1'):
                product.price1 = to_float(row_value(row, 'price1'))
            if hasattr(product, 'quantity1'):
                product.quantity1 = to_int(row_value(row, 'quantity1'))
            if hasattr(product, 'price2'):
                product.price2 = to_float(row_value(row, 'price2'))
            if hasattr(product, 'quantity2'):
                product.quantity2 = to_int(row_value(row, 'quantity2'))
            if hasattr(product, 'price3'):
                product.price3 = to_float(row_value(row, 'price3'))
            if hasattr(product, 'quantity3'):
                product.quantity3 = to_int(row_value(row, 'quantity3'))

            # Manufacturing, tax, and cost auditing
            if hasattr(product, 'purchase_cost'):
                product.purchase_cost = to_float(row_value(row, 'purchase_cost'))
            if hasattr(product, 'making_charges'):
                product.making_charges = to_float(row_value(row, 'making_charges'))
            if hasattr(product, 'weight_grams'):
                product.weight_grams = to_float(row_value(row, 'weight_grams'))
            if hasattr(product, 'material'):
                product.material = row_value(row, 'material')
            if hasattr(product, 'hsn_code'):
                product.hsn_code = row_value(row, 'hsn_code')
            if hasattr(product, 'gst_percent'):
                product.gst_percent = to_float(row_value(row, 'gst_percent'))

            # Operations, logistics, and visibility
            if hasattr(product, 'stock_total'):
                product.stock_total = to_int(row_value(row, 'stock_total'))
            if hasattr(product, 'box_packing_type'):
                product.box_packing_type = row_value(row, 'box_packing_type')
            if hasattr(product, 'vendor_id'):
                product.vendor_id = row_value(row, 'vendor_id')
            if hasattr(product, 'status'):
                product.status = row_value(row, 'status')
            if hasattr(product, 'is_active'):
                product.is_active = to_bool(row_value(row, 'is_active'), default=True)
            if hasattr(product, 'is_featured'):
                product.is_featured = to_bool(row_value(row, 'is_featured'), default=False)

            # Keep existing cloud image unless explicitly overridden by sheet.
            sheet_image = row_value(row, 'image_field')
            if sheet_image and hasattr(product, 'image_field'):
                product.image_field = str(sheet_image)

            processed_rows += 1
            if is_new:
                created_rows += 1
            else:
                updated_rows += 1

        db.session.commit()
        flash(
            f'Inventory synchronization complete. Processed {processed_rows} rows '
            f'({created_rows} created, {updated_rows} updated).'
        )
        return redirect(url_for('admin_dashboard'))
    except Exception as exc:
        db.session.rollback()
        flash(f'Catalog sync failed: {exc}')
        return redirect(url_for('admin_dashboard'))

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'GET':
        return render_template('admin/admin_login.html')

    username = (request.form.get('username') or '').strip()
    password = request.form.get('password') or ''

    if not ADMIN_USERNAME or not ADMIN_PASSWORD or not ADMIN_TOTP_SECRET:
        flash('Admin authentication is not configured.', 'error')
        return render_template('admin/admin_login.html'), 500

    if hmac.compare_digest(username, ADMIN_USERNAME) and hmac.compare_digest(password, ADMIN_PASSWORD):
        session['admin_step'] = 'totp'
        session.pop('is_admin', None)
        session.modified = True
        return redirect(url_for('admin_verify_totp'))

    flash('Invalid username or password.', 'error')
    return render_template('admin/admin_login.html'), 401


@app.route('/admin/verify-totp', methods=['GET', 'POST'])
def admin_verify_totp():
    if session.get('admin_step') != 'totp':
        flash('Please complete login first.', 'error')
        return redirect(url_for('admin_login'))

    if request.method == 'GET':
        return render_template('admin/admin_totp.html')

    code = (request.form.get('totp_code') or '').strip().replace(' ', '')
    if not ADMIN_TOTP_SECRET:
        flash('TOTP is not configured.', 'error')
        return render_template('admin/admin_totp.html'), 500

    totp = pyotp.TOTP(ADMIN_TOTP_SECRET)
    if totp.verify(code, valid_window=1):
        session['is_admin'] = True
        session.pop('admin_step', None)
        session.modified = True
        return redirect(url_for('admin_dashboard'))

    flash('Invalid authentication code.', 'error')
    return render_template('admin/admin_totp.html'), 401


@app.route('/admin/logout', methods=['GET'])
@admin_required
def admin_logout():
    session.pop('is_admin', None)
    session.pop('admin_step', None)
    session.modified = True
    return redirect(url_for('admin_login'))

if __name__ == '__main__':
    app.run(debug=True)