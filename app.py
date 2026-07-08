
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

@app.template_filter('fromjson')
def fromjson_filter(value):
    """Jinja2 filter to parse a JSON string into a Python object."""
    if not value:
        return []
    try:
        return json.loads(value)
    except Exception:
        return []
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
            description TEXT,
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
            stock_alert_threshold INTEGER DEFAULT 5,
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


def send_contact_email(to_email, subject, body, html_body=None, from_email=None):
    """
    Send email via Zeptomail SMTP (narinakhre.com domain).
    Supports both port 465 (SSL) and port 587 (STARTTLS).
    Credentials from Render environment variables:
        SMTP_SERVER = smtp.zeptomail.in
        SMTP_PORT   = 587
        SMTP_USER   = emailapikey
        SMTP_PASS   = <Zeptomail API key>
    The From address must be a verified sender in Zeptomail.
    """
    SMTP_SERVER = os.environ.get('SMTP_SERVER', 'smtp.zeptomail.in')
    SMTP_PORT   = int(os.environ.get('SMTP_PORT', '587'))
    SMTP_USER   = os.environ.get('SMTP_USER', '')
    SMTP_PASS   = os.environ.get('SMTP_PASS', '')
    FROM_EMAIL  = from_email or os.environ.get('SMTP_FROM', 'info@narinakhre.com')
    ORDERS_FROM = os.environ.get('SMTP_FROM_ORDERS', 'order-noreply@narinakhre.com')

    if not SMTP_USER or not SMTP_PASS:
        app.logger.warning('Email send skipped: SMTP_USER/SMTP_PASS not set in Render env vars')
        return False
    try:
        msg = MIMEMultipart('alternative')
        msg['From']     = f'Nari Nakhre <{FROM_EMAIL}>'
        msg['To']       = to_email
        msg['Subject']  = subject
        msg['Reply-To'] = FROM_EMAIL
        msg.attach(MIMEText(body, 'plain', 'utf-8'))
        if html_body:
            msg.attach(MIMEText(html_body, 'html', 'utf-8'))

        if SMTP_PORT == 465:
            # SSL connection
            server = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT)
        else:
            # STARTTLS connection (port 587 — Zeptomail default)
            server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
            server.ehlo()
            server.starttls()
            server.ehlo()

        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(FROM_EMAIL, to_email, msg.as_string())
        server.quit()
        app.logger.info(f'Email sent to {to_email}: {subject}')
        return True
    except smtplib.SMTPAuthenticationError as e:
        app.logger.error(f'SMTP auth failed — check SMTP_USER/SMTP_PASS in Render env vars: {e}')
        return False
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
    if 'wholesale' in host:
        g.site_type = 'wholesale'
    elif 'retail' in host:
        g.site_type = 'retail'
    elif path.startswith('/retail'):
        g.site_type = 'retail'
    elif path.startswith('/wholesale'):
        g.site_type = 'wholesale'
    else:
        g.site_type = 'retail'  # default to retail for shared paths like /admin, /track

def render_site(template_name, **kwargs):
    site_type = getattr(g, 'site_type', 'retail')
    db = get_db()
    # For retail, fetch categories from the products table's 'category' column
    try:
        cats = db.execute(
            "SELECT DISTINCT category FROM products WHERE is_active=1 AND category IS NOT NULL AND category != '' ORDER BY category"
        ).fetchall()
        categories = [c['category'] for c in cats if c['category']]
    except Exception:
        categories = []
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
    Images are stored in Supabase as {SKU}_1.webp, {SKU}_2.webp etc.
    Uses image_field as the primary/first image, then fills in the
    rest from the SKU pattern. The template uses onerror to hide
    broken images, so returning extra URLs that don't exist is safe.
    """
    sku = p_dict.get('sku', '')
    image_field = (p_dict.get('image_field') or '').strip()

    # Get all SKU-based URLs (_1 through _9)
    sku_urls = get_supabase_image_urls(sku) if sku else []

    if image_field.startswith('http'):
        # Put image_field first, then add remaining SKU urls
        others = [u for u in sku_urls if u != image_field]
        return [image_field] + others

    if sku_urls:
        return sku_urls

    return ['/static/assets/products/default.jpg']


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
    elif request.path == '/':
        # Root domain — detect from hostname
        # narinakhre.com → retail, wholesale.narinakhre.com → wholesale
        host = request.host.lower()
        if 'wholesale' in host:
            g.site_type = 'wholesale'
        else:
            g.site_type = 'retail'

    db = get_db()
    hero_images = get_random_hero_images(db, count=4)

    if g.site_type == 'retail':
        products = db.execute(
            'SELECT * FROM products WHERE is_active=1'
            ' ORDER BY CASE WHEN stock_total > 0 THEN 0 ELSE 1 END, id DESC'
        ).fetchall()

        # Build grouped products — category sorted by TOTAL product count (most first)
        grouped_products = {}
        for p in products:
            cat = p['category'] or 'New Arrivals'
            if cat not in grouped_products:
                grouped_products[cat] = []
            p_dict = dict(p)
            p_dict['images'] = get_product_images(p_dict)
            p_dict['tiers'] = get_product_tiers(p_dict)
            grouped_products[cat].append(p_dict)

        # Sort by total number of products in category (not just in-stock)
        # so Bangles (most listings) always appears first regardless of stock levels
        grouped_products = dict(
            sorted(grouped_products.items(), key=lambda x: len(x[1]), reverse=True)
        )

        # cat_counts for trending: in-stock only
        cat_counts = {
            cat: sum(1 for p in prods if p.get('stock_total', 0) and p['stock_total'] > 0)
            for cat, prods in grouped_products.items()
        }

        # Trending section: mix of highest discount + recently added in-stock products
        all_in_stock = [p for cat_prods in grouped_products.values()
                        for p in cat_prods
                        if p.get('stock_total', 0) and p['stock_total'] > 0]

        # Score: 60% weight on discount %, 40% on recency (id)
        max_id = max((p['id'] for p in all_in_stock), default=1)
        def trend_score(p):
            mrp = float(p.get('mrp_price') or 0)
            rp  = float(p.get('retail_price') or p.get('price1') or 0)
            disc_pct = ((mrp - rp) / mrp * 100) if mrp and mrp > rp else 0
            recency = (p['id'] / max_id) * 100
            return disc_pct * 0.6 + recency * 0.4

        trending = sorted(all_in_stock, key=trend_score, reverse=True)[:8]
        import random
        random.shuffle(trending)  # shuffle so it feels fresh each load

        return render_site('index.html', grouped_products=grouped_products,
                           trending=trending, hero_images=hero_images)

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
    if g.site_type != 'retail':
        return jsonify({'status': 'error', 'message': 'Cart not available on wholesale'}), 403
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
    out_of_stock_items = []
    db = get_db()
    for item in cart.values():
        item_dict = dict(item)
        if 'units' not in item_dict:
            item_dict['units'] = item_dict.get('qty', 1)
        # Check live stock from DB
        sku = item_dict.get('sku', '')
        live = db.execute('SELECT stock_total, name, image_field FROM products WHERE sku=?', (sku,)).fetchone()
        if live:
            item_dict['stock_total'] = live['stock_total'] or 0
            item_dict['is_out_of_stock'] = (live['stock_total'] or 0) == 0
            if item_dict['is_out_of_stock']:
                out_of_stock_items.append(item_dict.get('name') or live['name'] or sku)
            # Get image
            if not item_dict.get('image_url'):
                try:
                    imgs = get_product_images(dict(live))
                    if imgs and imgs[0].startswith('http'):
                        item_dict['image_url'] = imgs[0]
                except Exception:
                    pass
        else:
            item_dict['is_out_of_stock'] = False
        display_cart.append(item_dict)
    
    subtotal = sum(item['price'] * item['units'] for item in display_cart)
    applied_coupon = session.get('applied_coupon')
    discount = applied_coupon['discount_amount'] if applied_coupon else 0.0
    coupon_code = applied_coupon['code'] if applied_coupon else ''
    grand_total = max(subtotal - discount, 0)

    return render_site('checkout.html', display_cart=display_cart, subtotal=subtotal, total_tax=0.0,
                        discount=discount, grand_total=grand_total, coupon_code=coupon_code,
                        out_of_stock_items=out_of_stock_items)

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
    consignee_email = (request.form.get('email') or '').strip()
    payment_mode = (request.form.get('payment_mode') or 'Prepaid').strip()

    def sanitize_for_delhivery(value):
        cleaned = value or ''
        for char in ['#', '&', '%', ';']:
            cleaned = cleaned.replace(char, ' ')
        return ' '.join(cleaned.split())

    cleaned_name = sanitize_for_delhivery(consignee_name)
    cleaned_address = sanitize_for_delhivery(consignee_address)

    internal_order_id = f"NN-SHP-{datetime.now().strftime('%Y%m%d%H%M%S')}-{consignee_phone[-4:]}"
    user_id = session.get('user_id')

    # Calculate financials from cart
    cart = session.get('cart', {})
    if not cart:
        return jsonify({'status': 'error', 'message': 'Cart is empty'}), 400

    display_cart = list(cart.values())
    subtotal_amount = sum(float(item.get('price', 0)) * int(item.get('units', item.get('qty', 1))) for item in display_cart)
    applied_coupon = session.get('applied_coupon')
    discount_amount = float(applied_coupon.get('discount_amount', 0)) if applied_coupon else 0.0
    coupon_code = applied_coupon.get('code') if applied_coupon else None

    gst_breakdown = calculate_inclusive_gst(display_cart, discount_amount, subtotal_amount)
    gst_amount = gst_breakdown['total_gst']
    cgst_amount = gst_breakdown['cgst']
    sgst_amount = gst_breakdown['sgst']

    # Shipping FREE for customers
    actual_shipping_cost = 0.0
    try:
        provider = get_shipping_provider(
            app.config['SHIPPING_PROVIDER'],
            api_token=app.config.get('DELHIVERY_API_KEY')
        )
        cart_weight = max(sum(int(item.get('units', 1)) for item in display_cart) * 250, 250)
        rates = provider.get_rates(app.config.get('WAREHOUSE_PIN', '482001'), consignee_pincode, cart_weight, mode=payment_mode)
        actual_shipping_cost = float(rates.get('shipping_charge', 0) or 0)
    except Exception as e:
        app.logger.warning(f'Shipping rate fetch failed: {e}')

    total_amount = max(subtotal_amount - discount_amount, 0)

    # Store cart items as JSON for admin order view
    cart_items_json = json.dumps([{
        'sku': item.get('sku', ''),
        'name': item.get('name', ''),
        'price': float(item.get('price', 0)),
        'units': int(item.get('units', item.get('qty', 1))),
        'size': item.get('size', ''),
    } for item in display_cart])

    conn = get_db()
    conn.execute(
        '''INSERT INTO order_shipping
           (user_id, consignee_name, consignee_phone, consignee_email,
            consignee_address, consignee_city, consignee_state, consignee_pincode,
            internal_order_id, status, payment_mode,
            subtotal_amount, gst_amount, cgst_amount, sgst_amount,
            discount_amount, actual_shipping_cost, total_amount,
            coupon_code, cart_items_json)
           VALUES (?,?,?,?,?,?,?,?,?,'pending',?,?,?,?,?,?,?,?,?,?)''',
        (user_id, cleaned_name, consignee_phone, consignee_email,
         cleaned_address, consignee_city, consignee_state, consignee_pincode,
         internal_order_id, payment_mode,
         subtotal_amount, gst_amount, cgst_amount, sgst_amount,
         discount_amount, actual_shipping_cost, total_amount,
         coupon_code, cart_items_json)
    )
    conn.commit()

    # Delhivery shipment created AFTER payment — not here
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
    if g.site_type != 'retail':
        return jsonify({'status': 'error', 'message': 'Payment not available on wholesale'}), 403
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


@app.route('/payment-failed')
def payment_failed():
    """Page shown when Razorpay payment fails or is cancelled."""
    g.site_type = 'retail'
    order_id = request.args.get('order_id', '')
    reason = request.args.get('reason', 'Payment was not completed')
    return render_template('retail/payment_failed.html',
                           order_id=order_id, reason=reason)


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
    """Live tracking status — retail only."""
    if g.site_type != 'retail':
        return jsonify({'status': False, 'msg': 'Tracking not available'}), 403
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
    """Public, shareable order-tracking page — retail only.
    Wholesale is a quote-based service with no order tracking."""
    if g.site_type != 'retail':
        return redirect('/')
    conn = get_db()
    order = conn.execute(
        'SELECT * FROM order_shipping WHERE delhivery_waybill=?', (waybill,)
    ).fetchone()
    return render_template('retail/track_order.html', waybill=waybill, order=order)




@app.route('/api/search')
def api_search():
    q = (request.args.get('q') or '').strip()
    site = request.args.get('t') or getattr(g, 'site_type', 'retail')

    if len(q) < 2:
        return jsonify({'products': [], 'orders': [], 'query': q})

    conn = get_db()
    results = {'products': [], 'orders': [], 'query': q}

    try:
        q_low = q.lower()
        like  = f'%{q_low}%'

        rows = conn.execute(
            "SELECT id, sku, name, category, sub_category,"
            " retail_price, mrp_price, image_field"
            " FROM products"
            " WHERE is_active = 1"
            " AND ("
            f"   LOWER(name) LIKE '{like}'"
            f"   OR LOWER(category) LIKE '{like}'"
            f"   OR LOWER(sub_category) LIKE '{like}'"
            f"   OR LOWER(sku) LIKE '{like}'"
            " )"
            " LIMIT 12",
            ()
        ).fetchall()

        app.logger.info(f"Search '{q}' → {len(rows)} products")

        q_lower = q.lower()
        rows = sorted(rows, key=lambda r: (
            0 if (r['name'] or '').lower().startswith(q_lower) else
            1 if (r['sku'] or '').lower().startswith(q_lower) else
            2 if q_lower in (r['category'] or '').lower() else 3
        ))[:8]

        for r in rows:
            r_dict = dict(r)
            img = ''
            if r_dict.get('image_field'):
                parts = r_dict['image_field'].split(',')
                img = parts[0].strip() if parts else ''
            mrp = float(r_dict.get('mrp_price') or 0)
            rp  = float(r_dict.get('retail_price') or 0)
            disc = int((mrp - rp) / mrp * 100) if mrp and mrp > rp else 0
            results['products'].append({
                'id':       r_dict['id'],
                'sku':      r_dict['sku'] or '',
                'name':     r_dict['name'] or '',
                'category': r_dict['category'] or '',
                'price':    rp,
                'mrp':      mrp,
                'discount': disc,
                'image':    img,
                'url': f"/retail/product/{r_dict['id']}" if site == 'retail'
                       else f"/wholesale/product/{r_dict['id']}",
            })
    except Exception as e:
        app.logger.error(f'Search error: {type(e).__name__}: {e}')

    if site == 'retail' and len(q) >= 6:
        try:
            q_low = q.lower()
            rows = conn.execute(
                "SELECT internal_order_id, consignee_name, status,"
                " total_amount, delhivery_waybill"
                " FROM order_shipping"
                f" WHERE LOWER(internal_order_id) LIKE '%{q_low}%'"
                f" OR LOWER(delhivery_waybill) LIKE '%{q_low}%'"
                " LIMIT 2",
                ()
            ).fetchall()
            for o in rows:
                o_dict = dict(o)
                results['orders'].append({
                    'order_id': o_dict['internal_order_id'],
                    'status':   (o_dict['status'] or 'pending').replace('_',' ').title(),
                    'waybill':  o_dict['delhivery_waybill'] or '',
                    'name':     o_dict['consignee_name'] or '',
                    'total':    float(o_dict['total_amount'] or 0),
                    'url': f"/track/{o_dict['delhivery_waybill']}"
                           if o_dict['delhivery_waybill'] else '',
                })
        except Exception as e:
            app.logger.error(f'Order search error: {e}')

    return jsonify(results)


@app.route('/search')
def search_page():
    """Full search results page for longer queries or when JS is disabled."""
    q = (request.args.get('q') or '').strip()
    site = getattr(g, 'site_type', 'retail')
    if not q:
        return redirect('/' + site)

    conn = get_db()
    try:
        q_low = q.lower()
        like  = f'%{q_low}%'

        rows = conn.execute(
            "SELECT id, sku, name, category, sub_category, description,"
            " retail_price, mrp_price, image_field"
            " FROM products"
            " WHERE is_active = 1"
            " AND ("
            f" LOWER(name) LIKE '{like}'"
            f" OR LOWER(category) LIKE '{like}'"
            f" OR LOWER(sub_category) LIKE '{like}'"
            f" OR LOWER(sku) LIKE '{like}'"
            f" OR LOWER(description) LIKE '{like}'"
            " )"
            " ORDER BY name LIMIT 40",
            ()
        ).fetchall()
        products = []
        for r in rows:
            r_dict = dict(r)
            imgs = (r_dict.get('image_field') or '').split(',')
            r_dict['image'] = imgs[0].strip() if imgs else ''
            mrp = float(r_dict.get('mrp_price') or 0)
            rp  = float(r_dict.get('retail_price') or 0)
            r_dict['discount'] = int((mrp - rp) / mrp * 100) if mrp and mrp > rp else 0
            products.append(r_dict)
    except Exception as e:
        app.logger.error(f'Search page error: {e}')
        products = []

    return render_site('search_results.html', products=products, query=q)

@app.route('/clear_oos_items', methods=['POST'])
def clear_oos_items():
    """Remove out-of-stock items from cart, then redirect back to retail checkout."""
    g.site_type = 'retail'
    cart = session.get('cart', {})
    db = get_db()
    to_remove = []
    for key, item in list(cart.items()):
        sku = item.get('sku', '')
        if not sku:
            continue
        row = db.execute(
            'SELECT stock_total FROM products WHERE sku=?', (sku,)
        ).fetchone()
        if row and (row['stock_total'] or 0) == 0:
            to_remove.append(key)
    for key in to_remove:
        cart.pop(key, None)
    session['cart'] = cart
    session.modified = True
    # Always redirect to retail checkout explicitly
    return redirect('/retail/checkout')


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
        stock_total = int(request.form.get('stock_total', 0) or 0)
        stock_alert_threshold = int(request.form.get('stock_alert_threshold', 5) or 5)
        category = request.form.get('category', '').strip()
        material = request.form.get('material', '').strip()
        slug = request.form.get('slug', '').strip()
        db.execute(
            '''UPDATE products SET name=?, retail_price=?, mrp_price=?,
               stock_total=?, stock_alert_threshold=?, category=?, material=?, slug=? WHERE sku=?''',
            (name, retail_price, mrp_price, stock_total, stock_alert_threshold,
             category, material, slug, sku)
        )
        db.commit()
        # Send stock alert email if stock is at or below threshold
        if stock_total <= stock_alert_threshold:
            try:
                admin_email = os.environ.get('ADMIN_EMAIL', 'mohinicosmetics.india@gmail.com')
                send_contact_email(
                    admin_email,
                    f'⚠️ Low Stock Alert: {name} ({sku})',
                    f'Product: {name}\nSKU: {sku}\n'
                    f'Current Stock: {stock_total}\n'
                    f'Alert Threshold: {stock_alert_threshold}\n\n'
                    f'Please reorder this product soon.',
                )
                flash(f'Product {sku} updated. ⚠️ Stock alert sent — only {stock_total} units left!')
            except Exception as e:
                app.logger.warning(f'Stock alert email failed: {e}')
                flash(f'Product {sku} updated. ⚠️ Stock low ({stock_total} units).')
        else:
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
    import io
    try:
        import openpyxl
        from flask import send_file
        conn = get_db()
        products = conn.execute(
            'SELECT sku, name, category, sub_category, description, '
            'retail_price, mrp_price, wholesale_price, min_wholesale_qty, '
            'gst_percent, hsn_code, material, weight_grams, '
            'stock_total, is_active FROM products ORDER BY category, name'
        ).fetchall()
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = 'Products'
        headers = ['SKU', 'Name', 'Category', 'Sub Category', 'Description',
                   'Retail Price', 'MRP Price', 'Wholesale Price',
                   'Min Wholesale Qty', 'GST %', 'HSN Code',
                   'Material', 'Weight (g)', 'Stock', 'Active']
        ws.append(headers)
        # Bold header row
        from openpyxl.styles import Font
        for cell in ws[1]:
            cell.font = Font(bold=True)
        for p in products:
            ws.append([
                p['sku'], p['name'], p['category'], p['sub_category'],
                p['description'] or '',
                p['retail_price'], p['mrp_price'], p['wholesale_price'],
                p['min_wholesale_qty'], p['gst_percent'], p['hsn_code'],
                p['material'], p['weight_grams'], p['stock_total'],
                'Yes' if p['is_active'] else 'No'
            ])
        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        from datetime import date
        filename = f"NariNakhre_Products_{date.today().strftime('%Y%m%d')}.xlsx"
        return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                         as_attachment=True, download_name=filename)
    except Exception as e:
        app.logger.error(f'Product excel export failed: {e}')
        flash(f'Export failed: {e}')
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

        # Normalize column names to match exactly what the code expects.
        # Maps every header variant from the admin-exported Excel.
        col_aliases = {
            # As exported by download-products-excel
            'sub category':         'sub_category',
            'retail price':         'retail_price',
            'mrp price':            'mrp_price',
            'wholesale price':      'wholesale_price',
            'min wholesale qty':    'min_wholesale_qty',
            'gst %':                'gst_percent',
            'hsn code':             'hsn_code',
            'weight (g)':           'weight_grams',
            'stock':                'stock_total',
            'active':               'is_active',
            # Common alternates
            'mrp':                  'mrp_price',
            'selling price':        'retail_price',
            'sale price':           'retail_price',
            'price':                'retail_price',
            'discount %':           'retail_discount_percent',
            'discount percent':     'retail_discount_percent',
            'gst':                  'gst_percent',
            'gst percent':          'gst_percent',
            'qty':                  'stock_total',
            'quantity':             'stock_total',
            'weight g':             'weight_grams',
            'weight':               'weight_grams',
            'wt':                   'weight_grams',
            'sub_category':         'sub_category',  # already correct
        }
        # Lowercase + strip headers first, then apply alias map
        df.columns = df.columns.str.lower().str.strip()
        df.rename(columns=col_aliases, inplace=True)
        app.logger.info(f"Excel columns after normalization: {list(df.columns)}")

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
                # Only update columns present AND non-empty in this Excel row
                # Prevents zeroing prices when only stock_total column is uploaded
                def _has_val(col):
                    if col not in row.index:
                        return False
                    v = row.get(col)
                    try:
                        import math
                        if isinstance(v, float) and math.isnan(v):
                            return False
                    except Exception:
                        pass
                    return v is not None and str(v).strip() not in ('', 'nan', 'None')

                num_cols = {
                    'retail_price','mrp_price','retail_discount_percent',
                    'wholesale_price','min_wholesale_qty','sets_count',
                    'price1','price2','price3','quantity1','quantity2','quantity3',
                    'purchase_cost','making_charges','weight_grams','gst_percent',
                    'stock_total','weight','length','breadth','height'
                }
                bool_cols = {'is_active', 'is_featured'}
                col_map = [
                    ('name', row_value(row, 'name')),
                    ('slug', row_value(row, 'slug')),
                    ('category', row_value(row, 'category')),
                    ('sub_category', row_value(row, 'sub_category')),
                    ('collection', row_value(row, 'collection')),
                    ('size', row_value(row, 'size')),
                    ('retail_price', to_float(row_value(row, 'retail_price'))),
                    ('mrp_price', to_float(row_value(row, 'mrp_price'))),
                    ('retail_discount_percent', to_float(row_value(row, 'retail_discount_percent'))),
                    ('wholesale_price', to_float(row_value(row, 'wholesale_price'))),
                    ('min_wholesale_qty', to_int(row_value(row, 'min_wholesale_qty'))),
                    ('sets_count', to_int(row_value(row, 'sets_count'))),
                    ('image_field', final_image if sheet_image else None),
                    ('price1', to_float(row_value(row, 'price1'))),
                    ('quantity1', to_int(row_value(row, 'quantity1'))),
                    ('price2', to_float(row_value(row, 'price2'))),
                    ('quantity2', to_int(row_value(row, 'quantity2'))),
                    ('price3', to_float(row_value(row, 'price3'))),
                    ('quantity3', to_int(row_value(row, 'quantity3'))),
                    ('purchase_cost', to_float(row_value(row, 'purchase_cost'))),
                    ('making_charges', to_float(row_value(row, 'making_charges'))),
                    ('weight_grams', to_float(row_value(row, 'weight_grams'))),
                    ('material', row_value(row, 'material')),
                    ('hsn_code', row_value(row, 'hsn_code')),
                    ('gst_percent', to_float(row_value(row, 'gst_percent'))),
                    ('stock_total', to_int(row_value(row, 'stock_total'))),
                    ('box_packing_type', row_value(row, 'box_packing_type')),
                    ('vendor_id', row_value(row, 'vendor_id')),
                    ('status', row_value(row, 'status')),
                    ('is_active', 1 if to_bool(row_value(row, 'is_active'), default=True) else 0),
                    ('is_featured', 1 if to_bool(row_value(row, 'is_featured'), default=False) else 0),
                    ('weight', to_float(row_value(row, 'weight'))),
                    ('length', to_float(row_value(row, 'length'))),
                    ('breadth', to_float(row_value(row, 'breadth'))),
                    ('height', to_float(row_value(row, 'height'))),
                ]
                to_set = []
                for col, val in col_map:
                    if col == 'image_field' and val is None:
                        continue
                    if col in num_cols and not _has_val(col):
                        continue
                    if col in bool_cols and not _has_val(col):
                        continue
                    if col not in num_cols and col not in bool_cols and val is None:
                        continue
                    to_set.append((col, val))
                if to_set:
                    set_clause = ', '.join(f'{c}=?' for c, _ in to_set)
                    vals = [v for _, v in to_set] + [row_sku]
                    conn.execute(f'UPDATE products SET {set_clause} WHERE sku=?', vals)
                updated_rows += 1

            processed_rows += 1

        conn.commit()
        price_cols_found = [c for c in ['retail_price','mrp_price','stock_total'] if c in df.columns]
        # Send stock alerts for any products that hit the threshold during this upload
        if 'stock_total' in df.columns:
            try:
                low_stock = db.execute(
                    "SELECT sku, name, stock_total, stock_alert_threshold FROM products "
                    "WHERE stock_total <= stock_alert_threshold AND stock_total >= 0"
                ).fetchall()
                if low_stock:
                    items_str = '\n'.join(
                        f"  - {r['name']} (SKU: {r['sku']}): {r['stock_total']} units "
                        f"(alert at {r['stock_alert_threshold']})"
                        for r in low_stock
                    )
                    admin_email = os.environ.get('ADMIN_EMAIL', 'mohinicosmetics.india@gmail.com')
                    send_contact_email(
                        admin_email,
                        f'⚠️ Low Stock Alert — {len(low_stock)} product(s) need reordering',
                        f'The following products are at or below their stock alert threshold:\n\n'
                        f'{items_str}\n\nPlease reorder soon.',
                    )
            except Exception as e:
                app.logger.warning(f'Bulk stock alert email failed: {e}')
        flash(
            f'Sync complete: {processed_rows} rows processed '
            f'({created_rows} created, {updated_rows} updated). '
            f'Price columns detected: {price_cols_found or "NONE — check column headers in Excel"}'
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