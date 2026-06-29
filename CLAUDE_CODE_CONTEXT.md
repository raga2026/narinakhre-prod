# NariNakhre — Claude Code Knowledge Transfer
*Last updated: 29 June 2026. This file is the single source of truth for Claude Code.*

---

## 1. ABOUT THE DEVELOPER

- Name: Raghavendran G
- Blind software developer using JAWS screen reader
- Banking professional at Union Bank of India

### ACCESSIBILITY — ALWAYS FOLLOW THESE RULES
- Use Windows cmd.exe syntax only — NEVER PowerShell or bash
- Give explicit numbered step-by-step instructions
- Use labeled code blocks for all file content
- No dense visual descriptions
- Short clear sentences

---

## 2. THIS PROJECT

Nari Nakhre / Mohini Cosmetics — e-commerce cosmetics and wholesale apparel.
FanDeck / Arena is a SEPARATE project. Do not mix them.

- Domain registrar: GoDaddy
- Primary domain: narinakhre.com

---

## 3. LOCAL PROJECT PATH

```
C:\Users\ragha\Documents\NariNakhre\
```

Key files at root:
- app.py — main Flask application (NOT in zip — get from Git or Render)
- requirements.txt
- render.yaml
- .env — never commit this
- bulk_upload_images.py — image upload script
- CLAUDE_CODE_CONTEXT.md — this file

Templates:
- templates/base.html — shared header/footer for ALL pages
- templates/retail/index.html — retail homepage
- templates/retail/base.html — retail-specific base
- templates/wholesale/index.html — wholesale homepage
- templates/wholesale/base.html — wholesale-specific base
- templates/admin/ — all admin panel templates

---

## 4. TECH STACK

- Backend: Python 3, Flask, Gunicorn
- Database: Supabase PostgreSQL (via REST API)
- Image storage: Supabase Storage bucket named `products`
- CSS: Tailwind CSS 2.2.19 via CDN
- Fonts: Playfair Display, Merriweather via Google Fonts
- Hosting: Render (Singapore region)
- Payment: Razorpay
- Shipping: Delhivery

---

## 5. RENDER DEPLOYMENT

Two services from one repo:

| Service | Plan | Domains |
|---|---|---|
| narinakhre-test | Starter $7/mo | test-retail.narinakhre.com, test-wholesale.narinakhre.com |
| narinakhre-production | Free (upgrade before going live) | narinakhre.com, www.narinakhre.com, wholesale.narinakhre.com |

Current render.yaml (already fixed):
```yaml
services:
  - type: web
    name: narinakhre-test
    plan: starter
    runtime: python
    region: singapore
    buildCommand: "pip install -r requirements.txt"
    startCommand: "gunicorn app:app"
    domains:
      - test-retail.narinakhre.com
      - test-wholesale.narinakhre.com

  - type: web
    name: narinakhre-production
    plan: free
    runtime: python
    region: singapore
    buildCommand: "pip install -r requirements.txt"
    startCommand: "gunicorn app:app"
    domains:
      - narinakhre.com
      - www.narinakhre.com
      - wholesale.narinakhre.com
```

---

## 6. SUPABASE CONFIGURATION

- Project ref: eopqwvssznmxfxrfzqbx
- Project URL: https://eopqwvssznmxfxrfzqbx.supabase.co
- Storage bucket: products (public, stores WebP images)
- Database: PostgreSQL accessed via execute_sql RPC function
- Schemas exposed in Data API: public, graphql_public

### Critical RPC function — must exist in Supabase:
```sql
CREATE OR REPLACE FUNCTION execute_sql(query TEXT)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE result JSONB;
BEGIN
    EXECUTE 'SELECT jsonb_agg(row_to_json(t)) FROM (' || query || ') t'
    INTO result;
    RETURN COALESCE(result, '[]'::JSONB);
EXCEPTION WHEN OTHERS THEN
    RETURN jsonb_build_object('error', SQLERRM, 'query', query);
END;
$$;
GRANT EXECUTE ON FUNCTION execute_sql(TEXT) TO anon;
GRANT EXECUTE ON FUNCTION execute_sql(TEXT) TO authenticated;
GRANT EXECUTE ON FUNCTION execute_sql(TEXT) TO service_role;
```

---

## 7. ENVIRONMENT VARIABLES

Set in .env (local) AND Render dashboard for both services:

```
FLASK_SECRET_KEY=
SUPABASE_URL=https://eopqwvssznmxfxrfzqbx.supabase.co
SUPABASE_KEY=
SHIPPING_PROVIDER=
WAREHOUSE_PIN=
DELHIVERY_API_KEY=
DELHIVERY_API_TOKEN=
DELHIVERY_CLIENT_NAME=
DELHIVERY_PICKUP_LOCATION=
RAZORPAY_KEY_ID=
RAZORPAY_KEY_SECRET=
ADMIN_USERNAME=
ADMIN_PASSWORD=
ADMIN_TOTP_SECRET=VRCBLLFEZLVJRV3A37JXDJ4K6XEBUESE
```

NEVER put these in render.yaml or commit to Git.

---

## 8. DATABASE SCHEMA — Supabase PostgreSQL

### products table
```
id BIGSERIAL PRIMARY KEY
sku TEXT NOT NULL UNIQUE
name TEXT
slug TEXT
category TEXT
sub_category TEXT
collection TEXT
size TEXT
retail_price FLOAT DEFAULT 0
mrp_price FLOAT DEFAULT 0
retail_discount_percent FLOAT DEFAULT 0
wholesale_price FLOAT DEFAULT 0
min_wholesale_qty INTEGER DEFAULT 0
sets_count INTEGER DEFAULT 0
image_field TEXT  -- Supabase URL of first product image
quantity1 INTEGER DEFAULT 0
price1 FLOAT DEFAULT 0
quantity2 INTEGER DEFAULT 0
price2 FLOAT DEFAULT 0
quantity3 INTEGER DEFAULT 0
price3 FLOAT DEFAULT 0
purchase_cost FLOAT DEFAULT 0
making_charges FLOAT DEFAULT 0
weight_grams FLOAT DEFAULT 0
material TEXT
hsn_code TEXT
gst_percent FLOAT DEFAULT 0
stock_total INTEGER DEFAULT 0
box_packing_type TEXT
vendor_id TEXT
status TEXT
is_active INTEGER DEFAULT 1
is_featured INTEGER DEFAULT 0
category_id BIGINT
weight FLOAT DEFAULT 0
length FLOAT DEFAULT 0
breadth FLOAT DEFAULT 0
height FLOAT DEFAULT 0
```

### quotes table
```
id, request_id, name, whatsapp, email, items_json,
total_amount, status, created_at
```

### categories, users, order_shipping tables also exist.

---

## 9. KEY CODE PATTERNS IN app.py

### Supabase client setup:
```python
from supabase import create_client, Client as SupabaseClient
_supabase_client = None

def get_supabase():
    global _supabase_client
    if _supabase_client is None:
        _supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _supabase_client
```

### Database wrapper (SupabaseDB + SupabaseCursor):
- Uses ? placeholders throughout app
- SupabaseCursor converts ? to formatted SQL
- Calls execute_sql RPC to run SQL on Supabase
- Returns list of dicts accessible by column name

### Image URL pattern:
```python
def get_supabase_image_urls(sku):
    base = (os.environ.get('SUPABASE_URL') or '').rstrip('/')
    bucket = 'products'
    return [f"{base}/storage/v1/object/public/{bucket}/{sku}_{i}.webp"
            for i in range(1, 10)]
```

### Admin authentication:
- Route: /admin/login
- 2FA via Google Authenticator (TOTP)
- TOTP secret: VRCBLLFEZLVJRV3A37JXDJ4K6XEBUESE
- Authenticator entry name: Nari Nakhre Admin
- admin_required decorator protects all /admin/* routes

### Keep-alive thread (prevents Supabase free plan pausing):
```python
import threading, time as _time

def _supabase_keepalive():
    _time.sleep(30)
    while True:
        try:
            get_supabase().rpc('execute_sql', {'query': 'SELECT 1'}).execute()
        except Exception:
            pass
        _time.sleep(3 * 24 * 60 * 60)

_t = threading.Thread(target=_supabase_keepalive, daemon=True)
_t.start()
```

---

## 10. REQUIREMENTS.TXT

```
Flask
gunicorn
requests
razorpay
Flask-SQLAlchemy
Werkzeug
supabase
pyotp
pandas
openpyxl
Pillow
```

---

## 11. WHAT IS FULLY WORKING

1. Render deployment — narinakhre-test service live
2. DNS — test-retail.narinakhre.com and test-wholesale.narinakhre.com working
3. Supabase PostgreSQL — products persist permanently through redeploys
4. Admin login with 2FA Google Authenticator
5. Admin dashboard with all routes
6. Bulk image upload to Supabase (bulk_upload_images.py)
7. Excel bulk product sync via admin Run Master Sync
8. 61 products in database with images in Supabase
9. Mobile responsive base.html with hamburger menu
10. Hero images loaded from Supabase on homepage
11. Keep-alive thread for Supabase
12. render.yaml fixed (was broken — now corrected)

---

## 12. WHAT NEEDS TO BE DONE — IN ORDER

### TASK 1: Fix 502 Bad Gateway on narinakhre-test (URGENT)

The site is currently showing 502. The most likely cause is the
keepalive thread or the SupabaseDB class failing at startup.

Steps Claude Code should take:
1. Read app.py
2. Find the SupabaseCursor._execute method
3. Verify the response parsing handles the Supabase JSONB format:
   Supabase returns: [{"execute_sql": "[{row1}, {row2}]"}]
4. Find the keepalive thread start and verify it has a 30 second
   initial sleep before the first ping
5. Run syntax check: python -c "import ast; ast.parse(open('app.py').read()); print('OK')"
6. Push fix and verify Render logs show clean startup

### TASK 2: Verify images load from Supabase not static folder

After every redeploy, product images must come from Supabase URLs.
Check that:
- get_product_images() function exists in app.py
- All routes (index, category, product_detail) use get_product_images()
- Templates use product.images[0] not static paths
- Supabase has image_field set for all 61 products:
  Run this SQL in Supabase SQL Editor to verify:
  SELECT COUNT(*) FROM products WHERE image_field LIKE 'http%';
  Should return 61.

### TASK 3: Individual product add form

Route /admin/add-product exists in app.py.
Template templates/admin/admin_add_product.html exists.
Need to verify the form works end to end:
- Form submits correctly
- Image gets compressed and uploaded to Supabase
- Product appears in database after submission
- Link to Add Product is visible on admin dashboard

### TASK 4: Go live on production

Steps:
1. In Render dashboard upgrade narinakhre-production to Starter plan
2. Add all environment variables to narinakhre-production
3. Push current code — Render will deploy to production
4. Verify narinakhre.com and wholesale.narinakhre.com work
5. Upload products Excel via production admin panel

### TASK 5: Hero images not loading (minor)

The wholesale and retail index templates now use hero_images from
Supabase. If no images show in hero, run this SQL in Supabase:
```sql
UPDATE products
SET image_field = 'https://eopqwvssznmxfxrfzqbx.supabase.co/storage/v1/object/public/products/' || sku || '_1.webp'
WHERE image_field IS NULL OR image_field = '' OR image_field NOT LIKE 'http%';
```

---

## 13. GIT WORKFLOW

```cmd
cd C:\Users\ragha\Documents\NariNakhre
git add .
git commit -m "description"
git push origin main
```

Render auto-deploys on every push. Wait 2 minutes after push before testing.

---

## 14. BULK IMAGE UPLOAD

```cmd
cd C:\Users\ragha\Documents\NariNakhre
python bulk_upload_images.py
```

Reads from static\assets\products\, compresses to WebP 85% quality,
uploads to Supabase bucket products, updates image_field in database.
Safe to run multiple times — uses x-upsert so no duplicates.

---

## 15. ADMIN ROUTES REFERENCE

| Route | Function | Description |
|---|---|---|
| /admin/login | admin_login | Login page |
| /admin/verify-totp | admin_verify_totp | 2FA code entry |
| /admin/dashboard | admin_dashboard | Main dashboard |
| /admin/upload-excel | admin_upload_excel | Bulk product sync |
| /admin/manage-images | admin_manage_images | Per-SKU image upload |
| /admin/edit-product-details | admin_edit_product_details | Edit prices/stock |
| /admin/delete-products | admin_delete_products | Bulk delete |
| /admin/delete-product/<id> | admin_delete_product | Delete one product |
| /admin/add-product | admin_add_product | Add individual product |
| /admin/inbox | admin_inbox | Wholesale quotes inbox |
| /admin/quote/<id> | admin_quote_view | View quote details |
| /admin/logout | admin_logout | Logout |
| /admin/download-users-excel | download_users_excel | Export users |
| /admin/download-quotes-excel | download_quotes_excel | Export quotes |
| /admin/download-products-excel | download_products_excel | Export products |

---

## 16. IMPORTANT RULES FOR CLAUDE CODE

1. Always use cmd.exe syntax — never PowerShell
2. Never commit .env or credentials to Git
3. Never put secrets in render.yaml
4. Always run syntax check after editing app.py
5. SQL uses ? placeholders — SupabaseCursor converts them
6. Images must always use Supabase URLs
7. Push to git push origin main
8. Wait 2 minutes after push before checking Render
9. The app has both retail and wholesale on same codebase
10. Site type detected from request URL or hostname
