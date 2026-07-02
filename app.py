
import os
import json
import hmac
import smtplib
import requests
import razorpay
import pyotp
from datetime import datetime
from functools import wraps
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from flask import Flask, g, jsonify, redirect, render_template, request, session, url_for, flash
from werkzeug.routing import BuildError
from supabase import create_client, Client as SupabaseClient

from utils.shipping_manager import get_shipping_provider
import io
from PIL import Image as PILImage


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
def normalize_supabase_url(raw_url):
    """Strip /rest/v1 suffix if accidentally included in env var."""
    base = (raw_url or '').strip().rstrip('/')
    for suffix in ['/rest/v1', '/rest/v1/']:
        if base.endswith(suffix.rstrip('/')):
            base = base[:-len(suffix.rstrip('/'))]
    return base.rstrip('/')

SUPABASE_URL = normalize_supabase_url(os.environ.get('SUPABASE_URL', ''))
SUPABASE_KEY = os.environ.get('SUPABASE_KEY', '')

app.config['SHIPPING_PROVIDER'] = os.environ.get('SHIPPING_PROVIDER', 'mock')
app.config['DELHIVERY_API_KEY'] = os.environ.get('DELHIVERY_API_KEY', '')
app.config['WAREHOUSE_PIN'] = os.environ.get('WAREHOUSE_PIN', '482001')
app.config['RAZORPAY_KEY_ID'] = os.environ.get('RAZORPAY_KEY_ID', '')
app.config['RAZORPAY_KEY_SECRET'] = os.environ.get('RAZORPAY_KEY_SECRET', '')


# Supabase client for database operations
_supabase_client: SupabaseClient = None

def get_supabase():
    global _supabase_client
    if _supabase_client is None:
        _supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _supabase_client

DELHIVERY_API_TOKEN = os.environ.get('DELHIVERY_API_TOKEN', '')
DELHIVERY_CLIENT_NAME = os.environ.get('DELHIVERY_CLIENT_NAME', '')
DELHIVERY_PICKUP_LOCATION = os.environ.get('DELHIVERY_PICKUP_LOCATION', '')
DELHIVERY_SELLER_GST = os.environ.get('DELHIVERY_SELLER_GST', '')
WAREHOUSE_CITY = os.environ.get('WAREHOUSE_CITY', 'Jabalpur')
WAREHOUSE_STATE = os.environ.get('WAREHOUSE_STATE', 'Madhya Pradesh')
WAREHOUSE_ADDRESS = os.environ.get('WAREHOUSE_ADDRESS', '')
WAREHOUSE_PHONE = os.environ.get('WAREHOUSE_PHONE', '')
ADMIN_EMAIL = os.environ.get('ADMIN_EMAIL', 'mohinicosmetics.india@gmail.com')
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
        # Read raw bytes from the file-like object
        if hasattr(file_storage_object, 'stream') and hasattr(file_storage_object.stream, 'seek'):
            file_storage_object.stream.seek(0)
        elif hasattr(file_storage_object, 'seek'):
            file_storage_object.seek(0)
        raw_bytes = file_storage_object.read()

        # Convert & compress to WebP using PIL
        try:
            img = PILImage.open(io.BytesIO(raw_bytes))
            if img.mode in ('RGBA', 'P', 'LA'):
                img = img.convert('RGBA')
            else:
                img = img.convert('RGB')
            buf = io.BytesIO()
            img.save(buf, format='WEBP', quality=85, method=6)
            binary_payload = buf.getvalue()
            content_type = 'image/webp'
            # Always use .webp extension in the stored filename
            if not filename.lower().endswith('.webp'):
                filename = filename.rsplit('.', 1)[0] + '.webp'
        except Exception as pil_exc:
            app.logger.warning('WebP conversion failed, uploading original: %s', pil_exc)
            binary_payload = raw_bytes
            content_type = getattr(file_storage_object, 'mimetype', 'application/octet-stream')

        upload_url = f"{supabase_url}/storage/v1/object/{bucket_name}/{filename}"
        headers = {
            'Authorization': f'Bearer {supabase_key}',
            'apikey': supabase_key,
            'Content-Type': content_type,
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


class SupabaseDB:
    """
    Wrapper around the Supabase REST API that mimics the sqlite3
    connection interface used throughout the app.
    Uses Supabase PostgREST for SELECT queries and
    direct SQL execution via the rpc/sql endpoint for
    INSERT, UPDATE, DELETE, CREATE TABLE operations.
    """

    def __init__(self, client):
        self._client = client
        self._pending = []

    def execute(self, sql, params=None):
        return SupabaseCursor(self._client, sql, params)

    def commit(self):
        pass  # Supabase REST is auto-commit

    def rollback(self):
        pass

    def close(self):
        pass


class SupabaseCursor:
    """
    Executes SQL via Supabase REST API.
    Uses the execute_sql RPC and correctly unwraps the JSONB response.
    """

    def __init__(self, client, sql, params=None):
        self._client = client
        self._rows = []
        self._rowcount = 0
        self._execute(sql.strip(), params or ())

    def _format_sql(self, sql, params):
        if not params:
            return sql
        parts = sql.split('?')
        if len(parts) - 1 != len(params):
            return sql
        result = ''
        for i, part in enumerate(parts):
            result += part
            if i < len(params):
                val = params[i]
                if val is None:
                    result += 'NULL'
                elif isinstance(val, bool):
                    result += '1' if val else '0'
                elif isinstance(val, (int, float)):
                    result += str(val)
                else:
                    escaped = str(val).replace("'", "''")
                    result += f"'{escaped}'"
        return result

    def _execute(self, sql, params):
        formatted = self._format_sql(sql, params)
        sql_upper = formatted.strip().upper()

        try:
            # For non-SELECT statements use execute_sql RPC
            if not sql_upper.startswith('SELECT') and not sql_upper.startswith('WITH'):
                self._client.rpc('execute_sql', {'query': formatted}).execute()
                self._rows = []
                return

            # For SELECT use execute_sql and parse the JSONB response
            result = self._client.rpc('execute_sql', {'query': formatted}).execute()
            raw = result.data

            if raw is None:
                self._rows = []
                return

            # Supabase returns: [{'execute_sql': '[{row1}, {row2}]'}]
            if isinstance(raw, list) and len(raw) > 0:
                first = raw[0]
                if isinstance(first, dict) and 'execute_sql' in first:
                    inner = first['execute_sql']
                    if inner is None:
                        self._rows = []
                    elif isinstance(inner, list):
                        self._rows = inner
                    elif isinstance(inner, str):
                        try:
                            parsed = json.loads(inner)
                            self._rows = parsed if isinstance(parsed, list) else []
                        except Exception:
                            self._rows = []
                    else:
                        self._rows = []
                    return

            # Fallback: raw is already a list of rows
            if isinstance(raw, list):
                self._rows = raw
            else:
                self._rows = []

        except Exception as e:
            app.logger.error(f'SupabaseCursor error: {e} | SQL: {formatted[:300]}')
            self._rows = []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows

    def __iter__(self):
        return iter(self._rows)


def get_db():
    if 'db' not in g:
        g.db = SupabaseDB(get_supabase())
    return g.db


@app.teardown_appcontext
def close_db(error=None):
    g.pop('db', None)


def initialize_database_if_needed():
    """
    Create all tables in Supabase via the SQL editor RPC.
    This runs once on app startup. Tables are created only if
    they do not already exist so existing data is never touched.
    """
    tables_sql = [
        '''CREATE TABLE IF NOT EXISTS categories (
            id BIGSERIAL PRIMARY KEY,
            name TEXT
        )''',
        '''CREATE TABLE IF NOT EXISTS products (
            id BIGSERIAL PRIMARY KEY,
            sku TEXT NOT NULL UNIQUE,
            name TEXT,
            slug TEXT,
            category TEXT,
            sub_category TEXT,
            collection TEXT,
            size TEXT,
            retail_price FLOAT DEFAULT 0.0,
            mrp_price FLOAT DEFAULT 0.0,
            retail_discount_percent FLOAT DEFAULT 0.0,
            wholesale_price FLOAT DEFAULT 0.0,
            min_wholesale_qty INTEGER DEFAULT 0,
            sets_count INTEGER DEFAULT 0,
            image_field TEXT,
            quantity1 INTEGER DEFAULT 0,
            price1 FLOAT DEFAULT 0.0,
            quantity2 INTEGER DEFAULT 0,
            price2 FLOAT DEFAULT 0.0,
            quantity3 INTEGER DEFAULT 0,
            price3 FLOAT DEFAULT 0.0,
            purchase_cost FLOAT DEFAULT 0.0,
            making_charges FLOAT DEFAULT 0.0,
            weight_grams FLOAT DEFAULT 0.0,
            material TEXT,
            hsn_code TEXT,
            gst_percent FLOAT DEFAULT 0.0,
            stock_total INTEGER DEFAULT 0,
            box_packing_type TEXT,
            vendor_id TEXT,
            status TEXT,
            is_active INTEGER DEFAULT 1,
            is_featured INTEGER DEFAULT 0,
            category_id BIGINT REFERENCES categories(id),
            weight FLOAT DEFAULT 0.0,
            length FLOAT DEFAULT 0.0,
            breadth FLOAT DEFAULT 0.0,
            height FLOAT DEFAULT 0.0
        )''',
        '''CREATE TABLE IF NOT EXISTS quotes (
            id BIGSERIAL PRIMARY KEY,
            request_id TEXT UNIQUE,
            name TEXT,
            whatsapp TEXT,
            email TEXT,
            items_json TEXT,
            total_amount FLOAT DEFAULT 0.0,
            status TEXT DEFAULT 'New',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''',
        '''CREATE TABLE IF NOT EXISTS users (
            id BIGSERIAL PRIMARY KEY
        )''',
        '''CREATE TABLE IF NOT EXISTS order_shipping (
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT REFERENCES users(id),
            status TEXT NOT NULL DEFAULT 'pending',
            consignee_name TEXT NOT NULL,
            consignee_phone TEXT NOT NULL,
            consignee_address TEXT NOT NULL,
            consignee_city TEXT NOT NULL,
            consignee_state TEXT NOT NULL,
            consignee_pincode TEXT NOT NULL,
            internal_order_id TEXT NOT NULL UNIQUE,
            delhivery_waybill TEXT
        )''',
        '''CREATE TABLE IF NOT EXISTS coupons (
            id BIGSERIAL PRIMARY KEY,
            code TEXT NOT NULL UNIQUE,
            discount_percent FLOAT DEFAULT 0.0,
            min_order_amount FLOAT DEFAULT 0.0,
            category TEXT,
            sub_category TEXT,
            expiry_date DATE,
            is_active INTEGER DEFAULT 1,
            usage_limit INTEGER DEFAULT 0,
            times_used INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )'''
    ]
    client = get_supabase()
    for sql in tables_sql:
        try:
            client.rpc('execute_sql', {'query': sql}).execute()
        except Exception as e:
            app.logger.warning(f'Table init warning (may already exist): {e}')
    app.logger.info('Database tables verified/created via Supabase RPC.')


def ensure_checkout_tables_exist():
    """No-op — all tables created in initialize_database_if_needed."""
    pass


initialize_database_if_needed()
ensure_checkout_tables_exist()


def calculate_inclusive_gst(display_cart, discount=0.0, full_subtotal=0.0):
    """Extract GST already included in retail prices (GST-inclusive pricing)."""
    db = get_db()
    total_gst = 0.0
    for item in display_cart:
        line_total = item.get('price', 0) * item.get('units', item.get('qty', 1))
        if line_total <= 0:
            continue
        gst_rate = 3.0
        sku = item.get('sku')
        if sku:
            try:
                prod = db.execute('SELECT gst_percent FROM products WHERE sku=?', (sku,)).fetchone()
                if prod and prod['gst_percent']:
                    gst_rate = float(prod['gst_percent'])
            except Exception:
                pass
        line_gst = line_total - (line_total / (1 + gst_rate / 100.0))
        total_gst += line_gst
    if discount and full_subtotal and full_subtotal > 0:
        discount_ratio = min(discount / full_subtotal, 1.0)
        total_gst = total_gst * (1 - discount_ratio)
    total_gst = round(total_gst, 2)
    half = round(total_gst / 2, 2)
    return {'total_gst': total_gst, 'cgst': half, 'sgst': round(total_gst - half, 2)}


def create_delhivery_shipment(order_row, cart_items):
    """Create a Delhivery shipment after payment confirmation. Returns (waybill, error_msg)."""
    if not DELHIVERY_API_TOKEN:
        return None, "Delhivery API token not configured"
    order_row_dict = dict(order_row) if not isinstance(order_row, dict) else order_row
    consignee_name = order_row_dict.get('consignee_name', '')
    phone = order_row_dict.get('consignee_phone', '')
    address = order_row_dict.get('consignee_address', '')
    city = order_row_dict.get('consignee_city', '')
    state = order_row_dict.get('consignee_state', '') or ''
    pincode = str(order_row_dict.get('consignee_pincode', ''))
    internal_order_id = order_row_dict.get('internal_order_id', '')
    payment_mode = order_row_dict.get('payment_mode', 'Prepaid')
    total_amount = float(order_row_dict.get('total_amount', 0) or 0)
    total_qty = max(sum(int(item.get('units', item.get('qty', 1))) for item in cart_items), 1) if cart_items else 1
    weight_grams = max(total_qty * 250, 250)
    delhivery_payment_mode = 'COD' if payment_mode == 'COD' else 'Prepaid'
    shipment = {
        'name': consignee_name, 'phone': phone, 'add': address,
        'city': city, 'state': state, 'pin': pincode, 'country': 'IN',
        'order': internal_order_id,
        'payment_mode': delhivery_payment_mode,
        'cod_amount': total_amount if delhivery_payment_mode == 'COD' else 0,
        'weight': weight_grams,
        'shipment_width': 15, 'shipment_height': 10, 'shipment_length': 20,
        'quantity': total_qty, 'hsn_code': '7117',
        'seller_gst_tin': DELHIVERY_SELLER_GST,
        'client': DELHIVERY_CLIENT_NAME,
        'return_pin': app.config.get('WAREHOUSE_PIN', '482001'),
        'return_city': WAREHOUSE_CITY, 'return_state': WAREHOUSE_STATE,
        'return_add': WAREHOUSE_ADDRESS or address,
        'return_phone': WAREHOUSE_PHONE or phone,
        'return_name': DELHIVERY_CLIENT_NAME or consignee_name,
        'return_country': 'IN',
    }
    payload = {'shipments': [shipment], 'pickup_location': {'name': DELHIVERY_PICKUP_LOCATION}}
    try:
        response = requests.post(
            'https://track.delhivery.com/api/cmu/create.json',
            data={'format': 'json', 'data': json.dumps(payload)},
            headers={'Authorization': f'Token {DELHIVERY_API_TOKEN}',
                     'Content-Type': 'application/x-www-form-urlencoded'},
            timeout=30,
        )
        resp_json = response.json()
        app.logger.info(f"Delhivery create response: {resp_json}")
        packages = resp_json.get('packages', [])
        if packages and isinstance(packages, list):
            waybill = packages[0].get('waybill')
            if waybill:
                return waybill, None
        error_msg = resp_json.get('rmk') or resp_json.get('error') or str(resp_json)
        return None, f"Delhivery error: {error_msg}"
    except Exception as e:
        app.logger.error(f"Delhivery shipment creation exception: {e}")
        return None, str(e)


def send_contact_email(to_email, subject, body, html_body=None):
    """
    Send email via Zoho Mail SMTP (info@narinakhre.com).
    Uses SMTP_SSL on port 465 — same as the working wholesale site.
    Credentials can be overridden via env vars SMTP_USER / SMTP_PASS,
    but fall back to the known working Zoho credentials if not set.
    """
    SMTP_SERVER = os.environ.get('SMTP_SERVER', 'smtp.zoho.in')
    SMTP_PORT   = int(os.environ.get('SMTP_PORT', '465'))
    SMTP_USER   = os.environ.get('SMTP_USER', '')
    SMTP_PASS   = os.environ.get('SMTP_PASS', '')
    if not SMTP_USER or not SMTP_PASS:
        app.logger.warning('Email send skipped: SMTP_USER/SMTP_PASS not configured')
        return False
    try:
        msg = MIMEMultipart('alternative')
        msg['From']     = f'Nari Nakhre <{SMTP_USER}>'
        msg['To']       = to_email
        msg['Subject']  = subject
        msg['Reply-To'] = SMTP_USER
        msg.attach(MIMEText(body, 'plain', 'utf-8'))
        if html_body:
            msg.attach(MIMEText(html_body, 'html', 'utf-8'))
        # Zoho uses SSL on port 465 (not STARTTLS on 587)
        server = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT)
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SMTP_USER, to_email, msg.as_string())
        server.quit()
        app.logger.info(f'Email sent to {to_email}: {subject}')
        return True
    except Exception as e:
        app.logger.error(f'Email send failed to {to_email}: {type(e).__name__}: {e}')
        return False

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

# --- IMAGE HELPERS ---
def get_supabase_image_urls(sku):
    """Build Supabase image URLs for a SKU — _1 through _9."""
    base = (os.environ.get('SUPABASE_URL') or '').rstrip('/')
    if not base:
        return []
    bucket = 'products'
    return [f"{base}/storage/v1/object/public/{bucket}/{sku}_{i}.webp" for i in range(1, 10)]


def get_product_images(p_dict):
    """
    Return image URL list for a product.
    Uses image_field from database (Supabase URL) if available.
    Falls back to building Supabase URLs from SKU.
    Never uses local static paths.
    """
    sku = p_dict.get('sku', '')
    image_field = p_dict.get('image_field') or ''
    if image_field.startswith('http'):
        # Has a known first image — build the full series from SKU
        all_urls = get_supabase_image_urls(sku)
        return [image_field] + [u for u in all_urls if u != image_field]
    # No image_field — build from SKU
    urls = get_supabase_image_urls(sku)
    return urls if urls else ['/static/assets/products/default.jpg']


def get_product_tiers(p_dict):
    """Extract wholesale tier pricing from a product dict."""
    tiers = []
    for i in range(1, 4):
        qty = p_dict.get(f'quantity{i}')
        price = p_dict.get(f'price{i}')
        if qty and price:
            try:
                if int(qty) > 0 and float(price) > 0:
                    tiers.append({'qty': int(qty), 'price': float(price)})
            except Exception:
                continue
    return tiers if tiers else [{'qty': 1, 'price': 0}]


def get_random_hero_images(db, count=4):
    """Pick random product images from Supabase for hero banners."""
    rows = db.execute(
        "SELECT image_field, sku FROM products WHERE image_field IS NOT NULL AND image_field LIKE 'http%' ORDER BY RANDOM() LIMIT ?",
        (count,)
    ).fetchall()
    images = [r['image_field'] for r in rows if r['image_field']]
    # If not enough from DB, build from SKUs
    if len(images) < count:
        skus = db.execute("SELECT sku FROM products ORDER BY RANDOM() LIMIT ?", (count,)).fetchall()
        for row in skus:
            url = get_supabase_image_urls(row['sku'])
            if url:
                images.append(url[0])
            if len(images) >= count:
                break
    return images[:count]


# --- KEEP-ALIVE: Ping Supabase to prevent free plan pausing ---
import threading
import time as _time

def _supabase_keepalive():
    """Background thread that pings Supabase every 3 days to keep the project active."""
    _time.sleep(30)  # Wait 30 seconds after startup before first ping
    while True:
        try:
            client = get_supabase()
            client.rpc('execute_sql', {'query': 'SELECT 1'}).execute()
            app.logger.info('Supabase keep-alive ping sent.')
        except Exception as e:
            app.logger.warning(f'Supabase keep-alive failed: {e}')
        _time.sleep(3 * 24 * 60 * 60)  # 3 days

# Start keepalive only in the main Gunicorn worker process
if os.environ.get('SERVER_SOFTWARE', '').startswith('gunicorn') or True:
    _t = threading.Thread(target=_supabase_keepalive, daemon=True)
    _t.start()


# --- ROUTES: HOME & CATEGORY ---
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
            p_dict = dict(p)
            p_dict['images'] = get_product_images(p_dict)
            p_dict['tiers'] = get_product_tiers(p_dict)
            grouped_products[cat].append(p_dict)
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
        p_dict = dict(p)
        p_dict['images'] = get_product_images(p_dict)
        p_dict['tiers'] = get_product_tiers(p_dict)
        grouped_products[cat].append(p_dict)
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
    for p in raw_products:
        p_dict = dict(p)
        p_dict['images'] = get_product_images(p_dict)
        p_dict['tiers'] = get_product_tiers(p_dict)
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
    image_urls = get_product_images(p_dict)
    p_dict['tiers'] = get_product_tiers(p_dict)

    related = db.execute(
        'SELECT * FROM products WHERE id != ? ORDER BY RANDOM() LIMIT 4',
        (product_id,)
    ).fetchall()
    related_products = []
    for r in related:
        r_dict = dict(r)
        r_dict['images'] = get_product_images(r_dict)
        related_products.append(r_dict)
    return render_site('product_detail.html', product=p_dict, image_urls=image_urls, related_products=related_products)

@app.route('/favicon.ico')
def favicon():
    return app.send_static_file('assets/favicon.ico')

@app.route('/robots.txt')
def robots():
    return app.response_class(
        "User-agent: *\nAllow: /\nDisallow: /admin/\nDisallow: /checkout/\nSitemap: https://narinakhre.com/sitemap.xml\n",
        mimetype='text/plain'
    )

@app.route('/sitemap.xml')
def sitemap():
    db = get_db()
    products = db.execute("SELECT id, slug, name FROM products WHERE is_active=1").fetchall()
    categories = db.execute("SELECT DISTINCT category FROM products WHERE is_active=1").fetchall()
    base = "https://narinakhre.com"
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for path in ['/', '/retail', '/wholesale/contact', '/retail/contact']:
        lines.append(f'  <url><loc>{base}{path}</loc><changefreq>weekly</changefreq><priority>1.0</priority></url>')
    for cat in categories:
        lines.append(f'  <url><loc>{base}/retail/category/{cat["category"]}</loc><changefreq>daily</changefreq><priority>0.8</priority></url>')
        lines.append(f'  <url><loc>{base}/category/{cat["category"]}</loc><changefreq>daily</changefreq><priority>0.8</priority></url>')
    for p in products:
        slug = p["slug"] or str(p["id"])
        lines.append(f'  <url><loc>{base}/retail/product/{p["id"]}</loc><changefreq>weekly</changefreq><priority>0.7</priority></url>')
        lines.append(f'  <url><loc>{base}/wholesale/product/{p["id"]}</loc><changefreq>weekly</changefreq><priority>0.6</priority></url>')
    lines.append('</urlset>')
    return app.response_class('\n'.join(lines), mimetype='application/xml')

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
    db = get_db()
    for item in cart.values():
        item_dict = dict(item)
        if 'units' not in item_dict:
            item_dict['units'] = item_dict.get('qty', 1)
        # Look up product image for display using the same helper as product_detail
        if not item_dict.get('image_url'):
            p = db.execute('SELECT sku, image_field FROM products WHERE sku = ?', (item_dict.get('sku'),)).fetchone()
            if p:
                try:
                    imgs = get_product_images(dict(p))
                    if imgs and len(imgs) > 0 and imgs[0].startswith('http'):
                        item_dict['image_url'] = imgs[0]
                except Exception:
                    pass
        display_cart.append(item_dict)
    
    subtotal = sum(item['price'] * item['units'] for item in display_cart)
    applied_coupon = session.get('applied_coupon')
    discount = applied_coupon['discount_amount'] if applied_coupon else 0.0
    coupon_code = applied_coupon['code'] if applied_coupon else ''
    grand_total = max(subtotal - discount, 0)

    return render_site('checkout.html', display_cart=display_cart, subtotal=subtotal, total_tax=0.0,
                        discount=discount, grand_total=grand_total, coupon_code=coupon_code)

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

    conn = get_db()
    conn.execute(
        '''INSERT INTO order_shipping
           (user_id, consignee_name, consignee_phone, consignee_address,
            consignee_city, consignee_state, consignee_pincode, internal_order_id, status)
           VALUES (?,?,?,?,?,?,?,?,'pending')''',
        (user_id, cleaned_name, consignee_phone, cleaned_address,
         consignee_city, consignee_state, consignee_pincode, internal_order_id)
    )
    conn.commit()

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
        conn.execute(
            'UPDATE order_shipping SET delhivery_waybill=? WHERE internal_order_id=?',
            (waybill, internal_order_id)
        )
        conn.commit()

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

    # Return 200 OK for the fetch() call from checkout.html AJAX
    return jsonify({
        'status': 'ok',
        'internal_order_id': internal_order_id,
        'waybill': waybill
    }), 200

@app.route('/payment/gateway', methods=['GET'])
@app.route('/retail/payment/gateway', methods=['GET'])
def payment_gateway_router():
    """Payment authorization gateway with session validation."""
    g.site_type = 'retail'
    checkout_handover = session.get('checkout_handover', {})
    internal_order_id = checkout_handover.get('internal_order_id')
    waybill = checkout_handover.get('waybill')
    
    if not internal_order_id:
        flash('Order ID missing. Please complete shipping details again.', 'error')
        return redirect(url_for('checkout_shipping'))
    # waybill may be None if Delhivery API was unavailable — allow checkout to proceed
    
    # Calculate amount from current cart
    cart = session.get('cart', {})
    subtotal = sum(item['price'] * item['qty'] for item in cart.values())
    # Use the persisted order total (GST-inclusive, discount applied) from the DB
    # NEVER add 18% GST here — prices are already GST-inclusive at 3%
    handover = session.get('checkout_handover', {})
    order_id_for_amount = handover.get('internal_order_id', '')
    amount_to_pay = subtotal  # fallback
    total_tax = 0.0
    if order_id_for_amount:
        conn = get_db()
        orow = conn.execute(
            'SELECT total_amount, gst_amount FROM order_shipping WHERE internal_order_id=?',
            (order_id_for_amount,)
        ).fetchone()
        if orow and orow['total_amount']:
            amount_to_pay = float(orow['total_amount'])
            total_tax = float(orow['gst_amount'] or 0)
    
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
            requested_amount = amount_to_pay  # from DB lookup above

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


@app.route('/api/confirm-cod', methods=['POST'])
def confirm_cod_order():
    """Confirm a COD order immediately after address submission."""
    g.site_type = 'retail'
    checkout_handover = session.get('checkout_handover', {})
    internal_order_id = checkout_handover.get('internal_order_id', '').strip()
    if not internal_order_id:
        return jsonify({'status': 'error', 'message': 'No active order found'}), 400
    conn = get_db()
    order_row = conn.execute(
        'SELECT * FROM order_shipping WHERE internal_order_id=? AND status=?',
        (internal_order_id, 'pending')
    ).fetchone()
    if not order_row:
        return jsonify({'status': 'error', 'message': 'Order not found or already processed'}), 400
    order_row_dict = dict(order_row)
    cart = session.get('cart', {})
    cart_items = list(cart.values()) if cart else []
    waybill, del_error = create_delhivery_shipment(order_row_dict, cart_items)
    if waybill:
        conn.execute('UPDATE order_shipping SET status=?, delhivery_waybill=? WHERE internal_order_id=?',
                     ('cod_confirmed', waybill, internal_order_id))
    else:
        app.logger.error(f"Delhivery failed for COD {internal_order_id}: {del_error}")
        conn.execute('UPDATE order_shipping SET status=? WHERE internal_order_id=?',
                     ('cod_confirmed', internal_order_id))
    conn.commit()
    try:
        customer_email = order_row_dict.get('consignee_email', '')
        customer_name = order_row_dict.get('consignee_name', 'Customer')
        total = order_row_dict.get('total_amount', 0)
        tracking_url = f"{request.url_root.rstrip('/')}/track/{waybill}" if waybill else ''
        invoice_url = f"{request.url_root.rstrip('/')}/invoice/{internal_order_id}"
        if customer_email:
            send_contact_email(customer_email,
                f"Order Confirmed (COD) — {internal_order_id} | Nari Nakhre",
                f"Hi {customer_name},\n\nYour COD order is confirmed!\n\nOrder ID: {internal_order_id}\nAmount to pay on delivery: ₹{total:.2f}\n"
                + (f"Track: {tracking_url}\n" if tracking_url else "")
                + f"Invoice: {invoice_url}\n\nThank you!\n- Nari Nakhre")
        send_contact_email(ADMIN_EMAIL,
            f"🛍️ New COD Order — {internal_order_id}",
            f"COD Order\nCustomer: {customer_name}\nPhone: {order_row_dict.get('consignee_phone','')}\n"
            f"Address: {order_row_dict.get('consignee_address','')}, {order_row_dict.get('consignee_city','')}, {order_row_dict.get('consignee_pincode','')}\n"
            f"Amount: ₹{total:.2f}\n"
            + (f"Waybill: {waybill}\n" if waybill else "Waybill pending\n"))
    except Exception as e:
        app.logger.warning(f"COD email failed: {e}")
    total_for_session = float(order_row_dict.get('total_amount', 0) or 0)
    session['checkout_handover'] = {
        'internal_order_id': internal_order_id,
        'waybill': waybill,
        'amount_paid': total_for_session,
        'payment_mode': 'COD',
    }
    session.pop('cart', None)
    session.pop('applied_coupon', None)
    session.modified = True
    return jsonify({'status': 'success', 'waybill': waybill, 'internal_order_id': internal_order_id}), 200


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

        conn = get_db()
        row = conn.execute(
            'SELECT id FROM order_shipping WHERE internal_order_id=?',
            (internal_order_id,)
        ).fetchone()
        if row is None:
            return jsonify({
                'status': 'error',
                'message': 'No active order found for this payment'
            }), 400

        conn.execute(
            'UPDATE order_shipping SET status=? WHERE internal_order_id=?',
            ('paid', internal_order_id)
        )
        conn.commit()

        session.pop('razorpay_order_id', None)
        session.pop('payment_pending', None)
        session.pop('internal_order_id', None)
        session.pop('waybill', None)
        session.pop('checkout_handover', None)
        session.modified = True

        # If called via AJAX (fetch), return JSON; if form POST, redirect
        if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.content_type == 'application/json':
            return jsonify({'status': 'success', 'message': 'Payment verified and order finalized'}), 200
        return redirect(url_for('thank_you'))
    except Exception as e:
        app.logger.error(f'Payment verification error: {e}')
        if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.content_type == 'application/json':
            return jsonify({'status': 'error', 'message': 'Unable to finalize verified payment'}), 500
        flash('Payment verification failed. Please contact support.', 'error')
        return redirect(url_for('checkout'))

# --- DELHIVERY API ROUTES (Retail Only) ---
@app.route('/api/delhivery/check/<pincode>', methods=['GET'])
def delhivery_check_pincode(pincode):
    if g.site_type != 'retail':
        return jsonify({"status": False, "msg": "Unauthorized"}), 403
    # Validate pincode format before hitting Delhivery API
    import re as _re
    if not _re.match(r'^\d{6}$', str(pincode)):
        return jsonify({"status": False, "serviceable": False, "msg": "Invalid pincode format"}), 400
    try:
        provider = get_shipping_provider(
            app.config['SHIPPING_PROVIDER'],
            api_token=app.config.get('DELHIVERY_API_KEY')
        )
        result = provider.verify_pincode(pincode)
        # Surface the real reason in logs for debugging (visible in Render logs)
        if not result.get('serviceable') and not result.get('status'):
            app.logger.error(f"Delhivery pincode {pincode} check failed: {result.get('msg')}")
        return jsonify(result)
    except Exception as e:
        app.logger.error(f'Delhivery pincode check exception: {type(e).__name__}: {e}')
        return jsonify({"status": False, "serviceable": False, "msg": f"Service unavailable: {type(e).__name__}"}), 200

@app.route('/api/delhivery/shipping', methods=['POST'])
def calculate_checkout_shipping():
    if g.site_type != 'retail':
        return jsonify({"status": False, "msg": "Unauthorized"}), 403
    import re as _re2
    data = request.get_json(silent=True) or {}
    pincode = str(data.get('pincode') or data.get('destination') or '').strip()
    payment_mode = data.get('mode', 'Prepaid')
    if not _re2.match(r'^[0-9]{6}$', pincode):
        return jsonify({"status": False, "shipping_charge": 0,
                        "cod_fee": 0, "msg": "Invalid pincode format"}), 400
    try:
        cart = session.get('cart', {})
        total_weight = max(sum(item.get('qty', 1) for item in cart.values()) * 250, 250)
        provider = get_shipping_provider(
            app.config['SHIPPING_PROVIDER'],
            api_token=app.config.get('DELHIVERY_API_KEY')
        )
        rates = provider.get_rates(app.config.get('WAREHOUSE_PIN', ''), pincode, total_weight, mode=payment_mode)
        shipping_charge = rates.get('rate', 0) or rates.get('shipping_charge', 0)
        cod_fee = rates.get('cod_fee', 0) if payment_mode == 'COD' else 0
        return jsonify({
            "status": True,
            "shipping_charge": shipping_charge,
            "cod_fee": cod_fee,
            "payment_mode": payment_mode
        })
    except Exception as e:
        app.logger.error(f'Delhivery shipping calc error: {e}')
        return jsonify({"status": False, "shipping_charge": 0,
                        "cod_fee": 0, "msg": "Shipping rate unavailable"}), 200


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
    conn = get_db()
    conn.execute(
        "INSERT INTO order_shipping (internal_order_id, consignee_name, consignee_phone, consignee_address, consignee_city, consignee_state, consignee_pincode, delhivery_waybill, status) VALUES (?,?,?,?,?,?,?,?,'pending')",
        (order_id, name, phone, f"{address_line1}, {address_line2}", city, state, pincode, waybill)
    )
    conn.commit()

    # Finalize coupon usage if one was applied to this order
    applied_coupon = session.get('applied_coupon')
    if applied_coupon and applied_coupon.get('code'):
        try:
            conn.execute(
                'UPDATE coupons SET times_used = times_used + 1 WHERE code=?',
                (applied_coupon['code'],)
            )
            conn.commit()
        except Exception as e:
            app.logger.warning(f'Could not increment coupon usage: {e}')

    session.pop('cart', None)
    session.pop('applied_coupon', None)

    # Use our own branded, shareable tracking page instead of the raw Delhivery URL
    tracking_url = url_for('track_order_page', waybill=waybill, _external=True) if waybill else None

    # Email the order confirmation + tracking link to the customer (best-effort, never blocks checkout)
    if email and waybill:
        try:
            track_body = (
                f"Hi {name},\n\n"
                f"Your Nari Nakhre order ({order_id}) has been placed successfully!\n\n"
                f"Track your order here: {tracking_url}\n\n"
                f"Thank you for shopping with us.\n- Team Nari Nakhre"
            )
            send_contact_email(email, "Your Nari Nakhre order is confirmed!", track_body)
        except Exception as e:
            app.logger.warning(f"Order confirmation email failed: {e}")

    return render_site('thank_you.html', order_id=order_id, waybill=waybill, tracking_url=tracking_url)

@app.route('/api/track/<waybill>', methods=['GET'])
def api_track_shipment(waybill):
    """Live tracking status for a shipment, used by the public tracking page."""
    provider = get_shipping_provider(
        app.config['SHIPPING_PROVIDER'],
        api_token=app.config.get('DELHIVERY_API_KEY')
    )
    try:
        result = provider.track_shipment(waybill)
        return jsonify(result)
    except Exception as e:
        app.logger.error(f'Tracking error: {e}')
        return jsonify({"status": False, "msg": "Could not fetch tracking info"}), 200


@app.route('/track/<waybill>')
def track_order_page(waybill):
    """Public, shareable order-tracking page for customers."""
    conn = get_db()
    order = conn.execute(
        'SELECT * FROM order_shipping WHERE delhivery_waybill=?', (waybill,)
    ).fetchone()
    return render_template('retail/track_order.html', waybill=waybill, order=order)


@app.route('/apply_coupon', methods=['POST'])
def apply_coupon():
    data = request.get_json(silent=True) or {}
    code = (data.get('coupon') or '').strip().upper()
    if not code:
        return jsonify({"status": "error", "message": "Please enter a coupon code"}), 400

    db = get_db()
    coupon = db.execute(
        "SELECT * FROM coupons WHERE code=? AND is_active=1", (code,)
    ).fetchone()

    if not coupon:
        return jsonify({"status": "error", "message": "Invalid or inactive coupon code"}), 200

    # Expiry check
    if coupon['expiry_date']:
        try:
            from datetime import date as _date
            expiry = coupon['expiry_date']
            if isinstance(expiry, str):
                expiry = datetime.strptime(expiry, '%Y-%m-%d').date()
            if expiry < _date.today():
                return jsonify({"status": "error", "message": "This coupon has expired"}), 200
        except Exception:
            pass

    # Usage limit check
    if coupon['usage_limit'] and coupon['usage_limit'] > 0:
        if coupon['times_used'] >= coupon['usage_limit']:
            return jsonify({"status": "error", "message": "This coupon has reached its usage limit"}), 200

    # Calculate cart subtotal, optionally filtered by category/sub_category
    cart = session.get('cart', {})
    if not cart:
        return jsonify({"status": "error", "message": "Your cart is empty"}), 200

    eligible_subtotal = 0.0
    full_subtotal = 0.0
    for item in cart.values():
        units = item.get('units', item.get('qty', 1))
        price = item.get('price', 0)
        line_total = price * units
        full_subtotal += line_total

        applies = True
        if coupon['category'] or coupon['sub_category']:
            prod = db.execute(
                'SELECT category, sub_category FROM products WHERE sku=?', (item.get('sku'),)
            ).fetchone()
            if prod:
                if coupon['category'] and prod['category'] != coupon['category']:
                    applies = False
                if coupon['sub_category'] and prod['sub_category'] != coupon['sub_category']:
                    applies = False
            else:
                applies = False
        if applies:
            eligible_subtotal += line_total

    # Minimum order amount check (checked against full cart subtotal)
    if coupon['min_order_amount'] and full_subtotal < coupon['min_order_amount']:
        return jsonify({
            "status": "error",
            "message": f"Minimum order amount of \u20b9{coupon['min_order_amount']:.0f} required for this coupon"
        }), 200

    if eligible_subtotal <= 0:
        return jsonify({
            "status": "error",
            "message": "This coupon doesn't apply to any items in your cart"
        }), 200

    raw_discount = round(eligible_subtotal * (coupon['discount_percent'] / 100.0), 2)
    max_disc = float(coupon.get('max_discount_amount') or 0)
    discount = round(min(raw_discount, max_disc) if max_disc > 0 else raw_discount, 2)

    session['applied_coupon'] = {
        "code": code,
        "discount_percent": coupon['discount_percent'],
        "discount_amount": discount,
        "min_order_amount": float(coupon.get('min_order_amount') or 0),
        "max_discount_amount": max_disc,
        "category": coupon['category'],
        "sub_category": coupon['sub_category']
    }
    session.modified = True

    return jsonify({
        "status": "success",
        "message": f"Coupon applied! You saved \u20b9{discount:.0f}",
        "discount": discount,
        "discount_amount": discount,
        "discount_percent": coupon['discount_percent'],
        "min_order_amount": float(coupon.get('min_order_amount') or 0),
        "max_discount_amount": max_disc,
        "code": code
    })


@app.route('/remove_coupon', methods=['POST'])
def remove_coupon():
    session.pop('applied_coupon', None)
    session.modified = True
    return jsonify({"status": "success"})


@app.route('/clear_quote', methods=['POST'])
def clear_quote():
    session.pop('cart', None)
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return ('', 204)
    return redirect('/retail' if g.site_type == 'retail' else '/wholesale')

@app.route('/thank_you')
def thank_you():
    # Pull order_id/waybill from session (set during checkout_process) if not passed via query
    order_id = request.args.get('ref') or session.get('internal_order_id', '')
    checkout_handover = session.get('checkout_handover', {})
    waybill = checkout_handover.get('waybill') or session.get('waybill', '')

    # If still missing, try to look up the most recent waybill for this internal_order_id
    if order_id and not waybill:
        conn = get_db()
        row = conn.execute(
            'SELECT delhivery_waybill FROM order_shipping WHERE internal_order_id=? ORDER BY id DESC LIMIT 1',
            (order_id,)
        ).fetchone()
        if row and row['delhivery_waybill']:
            waybill = row['delhivery_waybill']

    tracking_url = url_for('track_order_page', waybill=waybill, _external=True) if waybill else None
    # Pull amount_paid from session handover or DB
    amount_paid = float(checkout_handover.get('amount_paid', 0) or 0)
    if not amount_paid and order_id:
        conn = get_db()
        arow = conn.execute(
            'SELECT total_amount FROM order_shipping WHERE internal_order_id=? LIMIT 1',
            (order_id,)
        ).fetchone()
        if arow and arow['total_amount']:
            amount_paid = float(arow['total_amount'])
    payment_mode = checkout_handover.get('payment_mode', '')
    return render_template('retail/thank_you.html',
                           order_id=order_id, waybill=waybill,
                           tracking_url=tracking_url,
                           amount_paid=amount_paid,
                           payment_mode=payment_mode)


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

    conn = get_db()
    product = conn.execute('SELECT id FROM products WHERE sku=?', (sku,)).fetchone()
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
        conn.execute('UPDATE products SET image_field=? WHERE sku=?', (first_public_cloud_url, sku))
        conn.commit()
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
        retail_price = float(request.form.get('retail_price', 0) or 0)
        mrp_price = float(request.form.get('mrp_price', 0) or 0)
        wholesale_price = float(request.form.get('wholesale_price', 0) or 0)
        stock_total = int(request.form.get('stock_total', 0) or 0)
        material = request.form.get('material', '').strip()
        size = request.form.get('size', '').strip()
        hsn_code = request.form.get('hsn_code', '').strip()
        gst_percent = float(request.form.get('gst_percent', 3) or 3)
        weight_grams = float(request.form.get('weight_grams', 0) or 0)
        length = float(request.form.get('length', 0) or 0)
        breadth = float(request.form.get('breadth', 0) or 0)
        height = float(request.form.get('height', 0) or 0)
        sets_count = int(request.form.get('sets_count', 1) or 1)
        min_wholesale_qty = int(request.form.get('min_wholesale_qty', 0) or 0)
        price1 = float(request.form.get('price1', 0) or 0)
        quantity1 = int(request.form.get('quantity1', 0) or 0)
        price2 = float(request.form.get('price2', 0) or 0)
        quantity2 = int(request.form.get('quantity2', 0) or 0)
        price3 = float(request.form.get('price3', 0) or 0)
        quantity3 = int(request.form.get('quantity3', 0) or 0)

        # Auto-generate slug from name if not provided
        slug = request.form.get('slug', '').strip()
        if not slug and name:
            import re
            slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')

        if not sku:
            flash('SKU is required.')
            return redirect(url_for('admin_add_product'))

        existing = db.execute('SELECT id FROM products WHERE sku=?', (sku,)).fetchone()
        if existing:
            flash(f'A product with SKU {sku} already exists.')
            return redirect(url_for('admin_add_product'))

        image_url = None
        image_file = request.files.get('image')
        if image_file and image_file.filename:
            filename = f"{sku}_1.webp"
            image_url = upload_image_to_supabase(image_file, filename)

        db.execute(
            '''INSERT INTO products
               (sku, name, category, sub_category, collection,
                retail_price, mrp_price, wholesale_price,
                stock_total, material, size, hsn_code, gst_percent,
                weight_grams, length, breadth, height,
                sets_count, min_wholesale_qty,
                slug, price1, quantity1, price2, quantity2,
                price3, quantity3, image_field, is_active)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)''',
            (sku, name, category, sub_category, collection,
             retail_price, mrp_price, wholesale_price,
             stock_total, material, size, hsn_code, gst_percent,
             weight_grams, length, breadth, height,
             sets_count, min_wholesale_qty,
             slug, price1, quantity1, price2, quantity2,
             price3, quantity3, image_url)
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

        conn = get_db()
        for _, row in df.iterrows():
            row_sku = normalize_value(row.get('sku'))
            if not row_sku:
                continue
            row_sku = str(row_sku).strip()

            existing = conn.execute('SELECT id FROM products WHERE sku=?', (row_sku,)).fetchone()
            is_new = existing is None

            existing_image = None
            if not is_new:
                img_row = conn.execute('SELECT image_field FROM products WHERE sku=?', (row_sku,)).fetchone()
                if img_row:
                    existing_image = img_row['image_field']

            sheet_image = row_value(row, 'image_field')
            final_image = str(sheet_image) if sheet_image else existing_image

            values = (
                row_value(row, 'name'),
                row_value(row, 'slug'),
                row_value(row, 'category'),
                row_value(row, 'sub_category'),
                row_value(row, 'collection'),
                row_value(row, 'size'),
                to_float(row_value(row, 'retail_price')),
                to_float(row_value(row, 'mrp_price')),
                to_float(row_value(row, 'retail_discount_percent')),
                to_float(row_value(row, 'wholesale_price')),
                to_int(row_value(row, 'min_wholesale_qty')),
                to_int(row_value(row, 'sets_count')),
                final_image,
                to_float(row_value(row, 'price1')),
                to_int(row_value(row, 'quantity1')),
                to_float(row_value(row, 'price2')),
                to_int(row_value(row, 'quantity2')),
                to_float(row_value(row, 'price3')),
                to_int(row_value(row, 'quantity3')),
                to_float(row_value(row, 'purchase_cost')),
                to_float(row_value(row, 'making_charges')),
                to_float(row_value(row, 'weight_grams')),
                row_value(row, 'material'),
                row_value(row, 'hsn_code'),
                to_float(row_value(row, 'gst_percent')),
                to_int(row_value(row, 'stock_total'), default=0),
                row_value(row, 'box_packing_type'),
                row_value(row, 'vendor_id'),
                row_value(row, 'status'),
                1 if to_bool(row_value(row, 'is_active'), default=True) else 0,
                1 if to_bool(row_value(row, 'is_featured'), default=False) else 0,
                to_float(row_value(row, 'weight')),
                to_float(row_value(row, 'length')),
                to_float(row_value(row, 'breadth')),
                to_float(row_value(row, 'height')),
            )

            if is_new:
                conn.execute(
                    '''INSERT INTO products
                       (name, slug, category, sub_category, collection, size,
                        retail_price, mrp_price, retail_discount_percent, wholesale_price,
                        min_wholesale_qty, sets_count, image_field,
                        price1, quantity1, price2, quantity2, price3, quantity3,
                        purchase_cost, making_charges, weight_grams, material,
                        hsn_code, gst_percent, stock_total, box_packing_type,
                        vendor_id, status, is_active, is_featured,
                        weight, length, breadth, height, sku)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    values + (row_sku,)
                )
                created_rows += 1
            else:
                conn.execute(
                    '''UPDATE products SET
                       name=?, slug=?, category=?, sub_category=?, collection=?, size=?,
                       retail_price=?, mrp_price=?, retail_discount_percent=?, wholesale_price=?,
                       min_wholesale_qty=?, sets_count=?, image_field=?,
                       price1=?, quantity1=?, price2=?, quantity2=?, price3=?, quantity3=?,
                       purchase_cost=?, making_charges=?, weight_grams=?, material=?,
                       hsn_code=?, gst_percent=?, stock_total=?, box_packing_type=?,
                       vendor_id=?, status=?, is_active=?, is_featured=?,
                       weight=?, length=?, breadth=?, height=?
                       WHERE sku=?''',
                    values + (row_sku,)
                )
                updated_rows += 1

            processed_rows += 1

        conn.commit()
        flash(
            f'Inventory synchronization complete. Processed {processed_rows} rows '
            f'({created_rows} created, {updated_rows} updated).'
        )
        return redirect(url_for('admin_dashboard'))
    except Exception as exc:
        flash(f'Catalog sync failed: {exc}')
        return redirect(url_for('admin_dashboard'))

@app.route('/invoice/<order_id>')
def customer_invoice(order_id):
    """Public invoice page for customers — link sent via email."""
    conn = get_db()
    order = conn.execute(
        'SELECT * FROM order_shipping WHERE internal_order_id=?', (order_id,)
    ).fetchone()
    if not order:
        return "Invoice not found", 404
    return render_template('admin/invoice.html', order=order,
                           seller_gst=DELHIVERY_SELLER_GST,
                           seller_name='Nari Nakhre',
                           seller_address=WAREHOUSE_ADDRESS)


@app.route('/admin/orders')
@admin_required
def admin_orders():
    conn = get_db()
    current_status = request.args.get('status', 'all')
    if current_status == 'all':
        orders = conn.execute("SELECT * FROM order_shipping ORDER BY id DESC LIMIT 200").fetchall()
    else:
        orders = conn.execute("SELECT * FROM order_shipping WHERE status=? ORDER BY id DESC", (current_status,)).fetchall()
    status_counts = conn.execute("SELECT status, COUNT(*) as count FROM order_shipping GROUP BY status").fetchall()
    count_map = {r['status']: r['count'] for r in status_counts}
    stats = [
        {'label':'All','count':sum(count_map.values()),'color':'#374151'},
        {'label':'Paid','count':count_map.get('paid',0),'color':'#059669'},
        {'label':'COD','count':count_map.get('cod_confirmed',0),'color':'#2563eb'},
        {'label':'Accepted','count':count_map.get('accepted',0),'color':'#7c3aed'},
        {'label':'Dispatched','count':count_map.get('dispatched',0),'color':'#0369a1'},
        {'label':'Delivered','count':count_map.get('delivered',0),'color':'#15803d'},
        {'label':'Cancelled','count':count_map.get('cancelled',0),'color':'#b91c1c'},
    ]
    return render_template('admin/admin_orders.html', orders=orders, stats=stats, current_status=current_status)


@app.route('/admin/orders/<int:order_id>/invoice')
@admin_required
def admin_order_invoice(order_id):
    conn = get_db()
    order = conn.execute('SELECT * FROM order_shipping WHERE id=?', (order_id,)).fetchone()
    if not order:
        flash('Order not found.', 'error')
        return redirect(url_for('admin_orders'))
    return render_template('admin/invoice.html', order=order,
                           seller_gst=DELHIVERY_SELLER_GST,
                           seller_name='Nari Nakhre',
                           seller_address=WAREHOUSE_ADDRESS)


@app.route('/admin/orders/<int:order_id>/accept', methods=['POST'])
@admin_required
def admin_order_accept(order_id):
    conn = get_db()
    order = conn.execute('SELECT * FROM order_shipping WHERE id=?', (order_id,)).fetchone()
    if not order:
        flash('Order not found.', 'error')
        return redirect(url_for('admin_orders'))
    waybill = order['delhivery_waybill']
    if not waybill:
        order_dict = dict(order)
        waybill, err = create_delhivery_shipment(order_dict, [])
        if waybill:
            conn.execute('UPDATE order_shipping SET delhivery_waybill=? WHERE id=?', (waybill, order_id))
        else:
            flash(f'Could not create Delhivery shipment: {err}', 'error')
            return redirect(url_for('admin_orders'))
    pickup_scheduled = False
    pickup_id = None
    try:
        from datetime import date, timedelta
        pickup_date = (date.today() + timedelta(days=1)).strftime('%Y-%m-%d')
        payload = {'pickup_time':'10:00:00','pickup_date':pickup_date,
                   'pickup_location':DELHIVERY_PICKUP_LOCATION,'expected_package_count':1}
        resp = requests.post('https://track.delhivery.com/fm/request/new/',
            json=payload, headers={'Authorization':f'Token {DELHIVERY_API_TOKEN}'}, timeout=15)
        pickup_scheduled = resp.status_code == 200
        if pickup_scheduled:
            try:
                pr = resp.json()
                pickup_id = str(pr.get('pickup_id') or pr.get('id') or '')
                conn.execute('UPDATE order_shipping SET pickup_id=?, pickup_date=? WHERE id=?',
                             (pickup_id, pickup_date, order_id))
            except Exception:
                pass
        app.logger.info(f"Pickup for {waybill}: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        app.logger.warning(f"Pickup scheduling failed: {e}")
    conn.execute('UPDATE order_shipping SET status=? WHERE id=?', ('accepted', order_id))
    conn.commit()
    msg = f"Order accepted. Waybill: {waybill}."
    msg += " Pickup scheduled for tomorrow." if pickup_scheduled else " Note: Schedule pickup manually in Delhivery panel."
    flash(msg, 'success')
    return redirect(url_for('admin_orders'))


@app.route('/admin/orders/<int:order_id>/dispatched', methods=['POST'])
@admin_required
def admin_order_dispatched(order_id):
    conn = get_db()
    conn.execute('UPDATE order_shipping SET status=? WHERE id=?', ('dispatched', order_id))
    conn.commit()
    flash('Order marked as dispatched.', 'success')
    return redirect(url_for('admin_orders'))


@app.route('/admin/orders/<int:order_id>/cancel', methods=['POST'])
@admin_required
def admin_order_cancel(order_id):
    conn = get_db()
    order = conn.execute('SELECT * FROM order_shipping WHERE id=?', (order_id,)).fetchone()
    if not order:
        flash('Order not found.', 'error')
        return redirect(url_for('admin_orders'))
    waybill = order['delhivery_waybill']
    if waybill and DELHIVERY_API_TOKEN:
        try:
            requests.post('https://track.delhivery.com/api/p/edit',
                json={'waybill':waybill,'cancellation':True},
                headers={'Authorization':f'Token {DELHIVERY_API_TOKEN}'}, timeout=15)
        except Exception as e:
            app.logger.warning(f"Delhivery cancellation failed: {e}")
    conn.execute('UPDATE order_shipping SET status=? WHERE id=?', ('cancelled', order_id))
    conn.commit()
    flash(f"Order {order['internal_order_id']} cancelled.", 'success')
    return redirect(url_for('admin_orders'))


@app.route('/admin/orders/<int:order_id>/label')
@admin_required
def admin_shipping_label(order_id):
    conn = get_db()
    order = conn.execute('SELECT * FROM order_shipping WHERE id=?', (order_id,)).fetchone()
    if not order or not order['delhivery_waybill']:
        flash('No waybill found for this order.', 'error')
        return redirect(url_for('admin_orders'))
    return render_template('admin/shipping_label.html', order=order)


@app.route('/admin/coupons', methods=['GET'])
@admin_required
def admin_coupons():
    db = get_db()
    coupons = db.execute('SELECT * FROM coupons ORDER BY id DESC').fetchall()
    categories = db.execute(
        "SELECT DISTINCT category FROM products WHERE category IS NOT NULL AND category != '' ORDER BY category"
    ).fetchall()
    sub_categories = db.execute(
        "SELECT DISTINCT sub_category FROM products WHERE sub_category IS NOT NULL AND sub_category != '' ORDER BY sub_category"
    ).fetchall()
    today_str = datetime.now().strftime('%Y-%m-%d')
    return render_template('admin/admin_coupons.html',
                            coupons=coupons, categories=categories, sub_categories=sub_categories,
                            today_str=today_str)


@app.route('/admin/coupons/create', methods=['POST'])
@admin_required
def admin_coupon_create():
    db = get_db()
    code = (request.form.get('code') or '').strip().upper()
    discount_percent = request.form.get('discount_percent', type=float) or 0.0
    min_order_amount = request.form.get('min_order_amount', type=float) or 0.0
    category = (request.form.get('category') or '').strip()
    sub_category = (request.form.get('sub_category') or '').strip()
    expiry_date = (request.form.get('expiry_date') or '').strip() or None
    usage_limit = request.form.get('usage_limit', type=int) or 0

    if not code:
        flash('Coupon code is required.')
        return redirect(url_for('admin_coupons'))
    if discount_percent <= 0 or discount_percent > 100:
        flash('Discount percent must be between 1 and 100.')
        return redirect(url_for('admin_coupons'))

    try:
        db.execute(
            "INSERT INTO coupons (code, discount_percent, min_order_amount, category, sub_category, expiry_date, usage_limit, is_active) VALUES (?,?,?,?,?,?,?,1)",
            (code, discount_percent, min_order_amount, category or None, sub_category or None, expiry_date, usage_limit)
        )
        db.commit()
        flash('Coupon "' + code + '" created successfully.')
    except Exception as e:
        flash('Could not create coupon - code may already exist. (' + str(e) + ')')
    return redirect(url_for('admin_coupons'))


@app.route('/admin/coupons/<int:coupon_id>/toggle', methods=['POST'])
@admin_required
def admin_coupon_toggle(coupon_id):
    db = get_db()
    row = db.execute('SELECT is_active FROM coupons WHERE id=?', (coupon_id,)).fetchone()
    if row is None:
        flash('Coupon not found.')
        return redirect(url_for('admin_coupons'))
    new_status = 0 if row['is_active'] else 1
    db.execute('UPDATE coupons SET is_active=? WHERE id=?', (new_status, coupon_id))
    db.commit()
    flash('Coupon status updated.')
    return redirect(url_for('admin_coupons'))


@app.route('/admin/coupons/<int:coupon_id>/delete', methods=['POST'])
@admin_required
def admin_coupon_delete(coupon_id):
    db = get_db()
    db.execute('DELETE FROM coupons WHERE id=?', (coupon_id,))
    db.commit()
    flash('Coupon deleted.')
    return redirect(url_for('admin_coupons'))


@app.route('/admin/coupons/<int:coupon_id>/edit', methods=['POST'])
@admin_required
def admin_coupon_edit(coupon_id):
    db = get_db()
    discount_percent = request.form.get('discount_percent', type=float) or 0.0
    min_order_amount = request.form.get('min_order_amount', type=float) or 0.0
    category = (request.form.get('category') or '').strip()
    sub_category = (request.form.get('sub_category') or '').strip()
    expiry_date = (request.form.get('expiry_date') or '').strip() or None
    usage_limit = request.form.get('usage_limit', type=int) or 0

    if discount_percent <= 0 or discount_percent > 100:
        flash('Discount percent must be between 1 and 100.')
        return redirect(url_for('admin_coupons'))

    db.execute(
        "UPDATE coupons SET discount_percent=?, min_order_amount=?, category=?, sub_category=?, expiry_date=?, usage_limit=? WHERE id=?",
        (discount_percent, min_order_amount, category or None, sub_category or None, expiry_date, usage_limit, coupon_id)
    )
    db.commit()
    flash('Coupon updated.')
    return redirect(url_for('admin_coupons'))


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