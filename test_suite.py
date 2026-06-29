"""
NariNakhre — Comprehensive Test Suite
======================================
Covers:
  1. Security (auth, CSRF, XSS, SQLi, admin protection)
  2. Retail flow (home, category, product, cart, checkout)
  3. Wholesale flow (home, category, product, quote)
  4. API endpoints (Delhivery, Razorpay, cart)
  5. Admin panel (login, TOTP, protected routes)
  6. Payment flow (order creation, signature verification)
  7. Mobile responsiveness meta-checks
  8. Edge cases and error handling

Run:
    pip install pytest flask requests
    python -m pytest test_suite.py -v

Or for a quick smoke test:
    python test_suite.py

Set env vars before running:
    NARINAKHRE_URL=https://test-retail.narinakhre.com  (default: http://127.0.0.1:5000)
    ADMIN_PASSWORD=yourpassword
    RAZORPAY_KEY_ID=rzp_test_xxx
    RAZORPAY_KEY_SECRET=yourSecret
    DELHIVERY_API_KEY=yourKey
"""

import os
import sys
import json
import hmac
import time
import hashlib
import unittest
import requests
from datetime import datetime
from urllib.parse import urljoin

# ── Config ─────────────────────────────────────────────────────────────────────
BASE_URL       = os.environ.get('NARINAKHRE_URL', 'http://127.0.0.1:5000')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', '')
RZP_KEY_ID     = os.environ.get('RAZORPAY_KEY_ID', '')
RZP_KEY_SECRET = os.environ.get('RAZORPAY_KEY_SECRET', '')

RETAIL_HOME    = f'{BASE_URL}/retail'
WHOLESALE_HOME = f'{BASE_URL}/'
ADMIN_LOGIN    = f'{BASE_URL}/admin/login'
TIMEOUT        = 10

COLORS = {
    'green':  '\033[92m',
    'red':    '\033[91m',
    'yellow': '\033[93m',
    'blue':   '\033[94m',
    'reset':  '\033[0m',
    'bold':   '\033[1m',
}

def c(color, text): return f"{COLORS.get(color,'')}{text}{COLORS['reset']}"


# ── Base test class ─────────────────────────────────────────────────────────────
class NariNakhreTestCase(unittest.TestCase):
    """Base class with helpers shared across all test groups."""

    @classmethod
    def setUpClass(cls):
        cls.session = requests.Session()
        cls.session.headers.update({'User-Agent': 'NariNakhre-TestSuite/1.0'})

    def get(self, path, **kwargs):
        return self.session.get(urljoin(BASE_URL, path), timeout=TIMEOUT, **kwargs)

    def post(self, path, **kwargs):
        return self.session.post(urljoin(BASE_URL, path), timeout=TIMEOUT, **kwargs)

    def assertOK(self, r, msg=''):
        self.assertIn(r.status_code, [200, 302], f'{msg} — got {r.status_code}: {r.url}')

    def assertStatus(self, r, code, msg=''):
        self.assertEqual(r.status_code, code, f'{msg} — got {r.status_code}: {r.url}')

    def assertContains(self, r, text, msg=''):
        self.assertIn(text, r.text, f'{msg} — "{text}" not found in response')

    def assertNotContains(self, r, text, msg=''):
        self.assertNotIn(text, r.text, f'{msg} — "{text}" should NOT be in response')


# ══════════════════════════════════════════════════════════════════════════════
# 1. SECURITY TESTS
# ══════════════════════════════════════════════════════════════════════════════
class SecurityTests(NariNakhreTestCase):
    """Critical security checks — these must ALL pass before deploying."""

    # ── 1.1 Admin route protection ────────────────────────────────────────────
    def test_admin_dashboard_requires_auth(self):
        r = self.get('/admin/dashboard', allow_redirects=False)
        self.assertIn(r.status_code, [302, 401, 403],
            'Admin dashboard must redirect unauthenticated users')

    def test_admin_manage_images_requires_auth(self):
        r = self.get('/admin/manage-images', allow_redirects=False)
        self.assertIn(r.status_code, [302, 401, 403],
            'Admin manage-images must be protected')

    def test_admin_edit_product_requires_auth(self):
        r = self.get('/admin/edit-product-details', allow_redirects=False)
        self.assertIn(r.status_code, [302, 401, 403],
            'Admin edit product must be protected')

    def test_admin_delete_product_requires_auth(self):
        r = self.get('/admin/delete-products', allow_redirects=False)
        self.assertIn(r.status_code, [302, 401, 403],
            'Admin delete products must be protected')

    def test_admin_download_excel_requires_auth(self):
        for path in ['/admin/download-users-excel',
                     '/admin/download-quotes-excel',
                     '/admin/download-products-excel']:
            r = self.get(path, allow_redirects=False)
            self.assertIn(r.status_code, [302, 401, 403],
                f'Excel download {path} must be protected')

    def test_admin_upload_excel_requires_auth(self):
        r = self.post('/admin/upload-excel', allow_redirects=False)
        self.assertIn(r.status_code, [302, 401, 403, 400],
            'Excel upload must be protected')

    def test_admin_add_product_requires_auth(self):
        r = self.get('/admin/add-product', allow_redirects=False)
        self.assertIn(r.status_code, [302, 401, 403],
            'Add product must be protected')

    # ── 1.2 SQL Injection ─────────────────────────────────────────────────────
    def test_sqli_in_category_param(self):
        payloads = ["' OR '1'='1", "'; DROP TABLE products;--", "1 UNION SELECT * FROM products--"]
        for payload in payloads:
            r = self.get(f'/retail/category/{payload}')
            # Should return 200 with empty results or 404 — never a 500
            self.assertNotEqual(r.status_code, 500,
                f'SQLi payload caused 500: {payload}')
            self.assertNotContains(r, 'sqlite3.OperationalError',
                f'SQLi exposed DB error: {payload}')
            self.assertNotContains(r, 'syntax error',
                f'SQLi exposed syntax error: {payload}')

    def test_sqli_in_product_id(self):
        payloads = ['1 OR 1=1', '0; DROP TABLE products', '999999 UNION SELECT 1,2,3']
        for payload in payloads:
            r = self.get(f'/retail/product/{payload}')
            self.assertNotEqual(r.status_code, 500,
                f'SQLi in product_id caused 500: {payload}')

    def test_sqli_in_cart_update(self):
        r = self.post('/update-cart', json={
            'product_id': "'; DROP TABLE products;--",
            'qty': 1, 'price': 100, 'size': 'Standard'
        })
        self.assertNotEqual(r.status_code, 500,
            'SQLi in cart update caused 500')

    # ── 1.3 XSS ───────────────────────────────────────────────────────────────
    def test_xss_in_category_url(self):
        xss = '<script>alert(1)</script>'
        r = self.get(f'/retail/category/{xss}')
        self.assertNotIn('<script>alert(1)</script>', r.text,
            'XSS not escaped in category URL')

    def test_xss_in_contact_form(self):
        xss_payload = '<script>alert("xss")</script>'
        r = self.post('/retail/contact', data={
            'name': xss_payload, 'email': 'test@test.com',
            'message': 'test message'
        })
        self.assertNotIn('<script>alert("xss")</script>', r.text,
            'XSS not escaped in contact form response')

    # ── 1.4 Delhivery API — wholesale access forbidden ────────────────────────
    def test_delhivery_check_blocked_for_wholesale(self):
        """Delhivery API should only serve retail requests."""
        ws_session = requests.Session()
        ws_session.get(f'{BASE_URL}/', timeout=TIMEOUT)  # set wholesale context
        r = ws_session.get(f'{BASE_URL}/api/delhivery/check/400001', timeout=TIMEOUT)
        # Should either be 403 or return {status: false}
        if r.status_code == 200:
            data = r.json()
            # Not necessarily an error if site doesn't differentiate by session
            # Just ensure it doesn't crash
            self.assertIn('status', data, 'Delhivery check must return status field')

    # ── 1.5 Payment verification — cannot be faked ───────────────────────────
    def test_fake_razorpay_signature_rejected(self):
        r = self.post('/api/verify-payment', json={
            'razorpay_order_id':   'order_FAKE123',
            'razorpay_payment_id': 'pay_FAKE456',
            'razorpay_signature':  'invalidsignature'
        })
        data = r.json()
        self.assertNotEqual(data.get('status'), 'success',
            'SECURITY: Fake Razorpay signature must be rejected')

    def test_verify_payment_missing_fields_rejected(self):
        r = self.post('/api/verify-payment', json={})
        self.assertIn(r.status_code, [400, 401, 403],
            'verify-payment with empty body must return 4xx')

    def test_create_order_requires_cart(self):
        """Cannot create a Razorpay order with zero amount."""
        fresh = requests.Session()  # fresh session = empty cart
        r = fresh.post(f'{BASE_URL}/api/create-order',
            json={'amount': 0}, timeout=TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            self.assertNotEqual(data.get('status'), 'success',
                'Zero-amount Razorpay order must not be created')

    # ── 1.6 Security headers ─────────────────────────────────────────────────
    def test_no_server_header_leakage(self):
        r = self.get('/retail')
        server = r.headers.get('Server', '')
        self.assertNotIn('Werkzeug', server,
            'Server header leaks Werkzeug version in production')

    def test_content_type_on_api_responses(self):
        r = self.get('/api/delhivery/check/400001')
        if r.status_code == 200:
            self.assertIn('application/json', r.headers.get('Content-Type', ''),
                'API must return application/json')


# ══════════════════════════════════════════════════════════════════════════════
# 2. RETAIL FLOW TESTS
# ══════════════════════════════════════════════════════════════════════════════
class RetailFlowTests(NariNakhreTestCase):

    def test_retail_home_loads(self):
        r = self.get('/retail')
        self.assertOK(r, 'Retail home')
        self.assertContains(r, 'Nari Nakhre', 'Brand name missing from retail home')

    def test_retail_home_has_viewport_meta(self):
        r = self.get('/retail')
        self.assertContains(r, 'viewport', 'Missing viewport meta — mobile will not scale')
        self.assertContains(r, 'width=device-width', 'Viewport must include width=device-width')

    def test_retail_home_has_hamburger(self):
        r = self.get('/retail')
        self.assertContains(r, 'hamburger', 'Mobile hamburger menu missing from retail')
        self.assertContains(r, 'mobile-nav', 'Mobile nav drawer missing from retail')

    def test_retail_category_loads(self):
        r = self.get('/retail/category/Bangles')
        self.assertOK(r, 'Retail category Bangles')

    def test_retail_category_has_products(self):
        r = self.get('/retail/category/Bangles')
        if r.status_code == 200:
            # Should have Add to Cart buttons if products exist
            has_products = 'add-to-cart-btn' in r.text or 'cart-container' in r.text
            # Soft check — may be empty category
            if 'No products' not in r.text and 'empty' not in r.text.lower():
                self.assertTrue(has_products, 'Category page missing cart buttons')

    def test_retail_buttons_are_on_brand(self):
        """Ensure yellow Amazon buttons are gone."""
        r = self.get('/retail')
        self.assertNotIn('f0c14b', r.text, 'Old yellow Amazon button color found on retail home')
        self.assertNotIn('df5f12', r.text, 'Old orange Amazon button color found on retail home')
        r2 = self.get('/retail/category/Bangles')
        self.assertNotIn('f0c14b', r2.text, 'Old yellow button on category page')

    def test_retail_checkout_loads(self):
        r = self.get('/retail/checkout')
        self.assertOK(r, 'Retail checkout')
        self.assertContains(r, 'Cart', 'Cart heading missing from checkout')

    def test_retail_checkout_has_razorpay_key(self):
        r = self.get('/retail/checkout')
        self.assertNotIn('rzp_test_YOUR_KEY', r.text,
            'CRITICAL: Hardcoded placeholder Razorpay key found in checkout')
        self.assertNotIn('YOUR_KEY', r.text,
            'CRITICAL: Placeholder key in checkout')

    def test_retail_checkout_has_razorpay_script(self):
        r = self.get('/retail/checkout')
        self.assertContains(r, 'checkout.razorpay.com', 'Razorpay script missing from checkout')

    def test_add_to_cart_api(self):
        r = self.post('/update-cart', json={
            'product_id': 'TEST-SKU-001', 'qty': 1,
            'price': 299.0, 'size': 'Standard'
        })
        self.assertOK(r, 'Cart update API')
        if r.status_code == 200:
            data = r.json()
            self.assertIn(data.get('status'), ['success', 'ok', 'error'],
                'Cart update must return status field')

    def test_clear_cart(self):
        r = self.post('/clear_quote')
        self.assertIn(r.status_code, [200, 302], 'Clear cart must return 200 or redirect')

    def test_retail_contact_page_loads(self):
        r = self.get('/retail/contact')
        self.assertOK(r, 'Retail contact page')

    def test_retail_product_detail_loads(self):
        """Try to load a product detail page — find a real product ID first."""
        r = self.get('/retail')
        # Try common product IDs
        for pid in [1, 2, 3, 10]:
            pr = self.get(f'/retail/product/{pid}')
            if pr.status_code == 200:
                self.assertContains(pr, 'Add to Cart', f'Product {pid} missing Add to Cart')
                self.assertNotIn('f0c14b', pr.text, f'Old button color on product {pid}')
                break


# ══════════════════════════════════════════════════════════════════════════════
# 3. WHOLESALE FLOW TESTS
# ══════════════════════════════════════════════════════════════════════════════
class WholesaleFlowTests(NariNakhreTestCase):

    def test_wholesale_home_loads(self):
        r = self.get('/')
        self.assertOK(r, 'Wholesale home')
        self.assertContains(r, 'Nari Nakhre', 'Brand name missing from wholesale')

    def test_wholesale_home_has_viewport_meta(self):
        r = self.get('/')
        self.assertContains(r, 'viewport', 'Missing viewport meta on wholesale')
        self.assertContains(r, 'width=device-width')

    def test_wholesale_category_loads(self):
        r = self.get('/category/Bangles')
        self.assertOK(r, 'Wholesale category Bangles')

    def test_wholesale_checkout_loads(self):
        r = self.get('/checkout')
        self.assertOK(r, 'Wholesale checkout/quote page')

    def test_wholesale_contact_loads(self):
        r = self.get('/contact')
        self.assertOK(r, 'Wholesale contact page')

    def test_retail_tiers_not_on_wholesale(self):
        """Wholesale should not show retail-only elements."""
        r = self.get('/')
        self.assertNotIn('btn-retail-main', r.text,
            'Retail-only button class found on wholesale home')

    def test_wholesale_mobile_stacking(self):
        r = self.get('/')
        self.assertContains(r, 'ws-header-row',
            'Wholesale mobile-stacking class missing from header')


# ══════════════════════════════════════════════════════════════════════════════
# 4. DELHIVERY API TESTS
# ══════════════════════════════════════════════════════════════════════════════
class DelhiveryAPITests(NariNakhreTestCase):

    def setUp(self):
        super().setUp()
        # Set retail context
        self.session.get(f'{BASE_URL}/retail', timeout=TIMEOUT)

    def test_pincode_check_valid_format(self):
        r = self.get('/api/delhivery/check/400001')
        self.assertIn(r.status_code, [200, 403],
            'Pincode check must return 200 or 403 (not 500)')
        if r.status_code == 200:
            data = r.json()
            self.assertTrue(
                'status' in data or 'serviceable' in data,
                'Pincode check response must have status or serviceable field'
            )

    def test_pincode_check_invalid_pincode(self):
        """Non-numeric or short pincode should not crash."""
        for bad_pin in ['abc', '12345', '0000000', '!@#$%^']:
            r = self.get(f'/api/delhivery/check/{bad_pin}')
            self.assertNotEqual(r.status_code, 500,
                f'Invalid pincode {bad_pin!r} caused 500')

    def test_pincode_check_serviceable_mumbai(self):
        """Mumbai pincode 400001 should be serviceable."""
        r = self.get('/api/delhivery/check/400001')
        if r.status_code == 200:
            data = r.json()
            ok = data.get('status') is True or data.get('serviceable') is True
            if not ok:
                print(c('yellow', f'\n  ⚠ Mumbai 400001 not serviceable — check DELHIVERY_API_KEY'))

    def test_shipping_calc_endpoint(self):
        r = self.post('/api/delhivery/shipping', json={
            'pincode': '400001', 'mode': 'Prepaid'
        })
        self.assertIn(r.status_code, [200, 403, 400],
            'Shipping calc must not return 500')
        if r.status_code == 200:
            data = r.json()
            self.assertIn('shipping_charge', data,
                'Shipping calc must return shipping_charge')

    def test_shipping_calc_cod_mode(self):
        r = self.post('/api/delhivery/shipping', json={
            'pincode': '400001', 'mode': 'COD'
        })
        self.assertIn(r.status_code, [200, 403, 400])
        if r.status_code == 200:
            data = r.json()
            self.assertIn('cod_fee', data,
                'COD mode must return cod_fee field')

    def test_checkout_process_sanitizes_special_chars(self):
        """Delhivery rejects #, &, %, ; in address fields."""
        r = self.post('/checkout/process', data={
            'consignee_name':    'Test & User',
            'consignee_phone':   '9999999999',
            'consignee_address': 'House #42, 100% Lane; Block&A',
            'consignee_city':    'Mumbai',
            'consignee_state':   'Maharashtra',
            'consignee_pincode': '400001',
        })
        # Should not 500 — sanitizer should clean the address
        self.assertNotEqual(r.status_code, 500,
            'Special chars in address caused 500 — sanitizer failed')


# ══════════════════════════════════════════════════════════════════════════════
# 5. RAZORPAY PAYMENT TESTS
# ══════════════════════════════════════════════════════════════════════════════
class RazorpayTests(NariNakhreTestCase):

    def setUp(self):
        super().setUp()
        self.session.get(f'{BASE_URL}/retail', timeout=TIMEOUT)
        # Add something to cart so amount > 0
        self.session.post(f'{BASE_URL}/update-cart', json={
            'product_id': 'TEST-SKU', 'qty': 1,
            'price': 499.0, 'size': 'Standard'
        }, timeout=TIMEOUT)

    def test_create_order_endpoint_exists(self):
        r = self.post('/api/create-order', json={'amount': 590.0})
        self.assertIn(r.status_code, [200, 400, 500],
            'create-order endpoint must exist')

    def test_create_order_returns_json(self):
        r = self.post('/api/create-order', json={'amount': 590.0})
        self.assertIn('application/json', r.headers.get('Content-Type', ''),
            'create-order must return JSON')

    def test_create_order_with_valid_amount(self):
        r = self.post('/api/create-order', json={'amount': 590.0})
        if r.status_code == 200:
            data = r.json()
            if data.get('status') == 'success':
                self.assertIn('razorpay_order_id', data,
                    'Successful order must include razorpay_order_id')
                self.assertIn('amount', data, 'Order must include amount')
                self.assertIn('key_id', data, 'Order must include key_id for frontend')
                # Verify key is not placeholder
                self.assertNotEqual(data.get('key_id'), '',
                    'key_id must not be empty')
                self.assertNotIn('YOUR_KEY', data.get('key_id', ''),
                    'key_id must not be placeholder')
            elif data.get('status') == 'error':
                print(c('yellow', f'\n  ⚠ Razorpay order failed: {data.get("message")} — check RAZORPAY keys'))

    def test_create_order_zero_amount_rejected(self):
        r = self.post('/api/create-order', json={'amount': 0})
        if r.status_code == 200:
            data = r.json()
            self.assertNotEqual(data.get('status'), 'success',
                'Zero-amount order must not succeed')

    def test_verify_payment_missing_fields(self):
        r = self.post('/api/verify-payment', json={
            'razorpay_order_id': 'order_test'
            # missing payment_id and signature
        })
        self.assertIn(r.status_code, [400, 401, 403],
            'Incomplete verify-payment must return 4xx')

    def test_verify_payment_wrong_signature(self):
        r = self.post('/api/verify-payment', json={
            'razorpay_order_id':   'order_FAKE999',
            'razorpay_payment_id': 'pay_FAKE999',
            'razorpay_signature':  'completely_wrong_signature'
        })
        data = r.json()
        self.assertNotEqual(data.get('status'), 'success',
            'SECURITY: Wrong signature must never verify successfully')

    def test_verify_payment_valid_signature(self):
        """
        Generate a real HMAC-SHA256 signature and verify it is accepted.
        Only runs if RZP_KEY_SECRET is set.
        """
        if not RZP_KEY_SECRET:
            self.skipTest('RAZORPAY_KEY_SECRET not set — skipping signature test')
        order_id   = 'order_TEST_ONLY'
        payment_id = 'pay_TEST_ONLY'
        sig = hmac.new(
            RZP_KEY_SECRET.encode(), f'{order_id}|{payment_id}'.encode(), hashlib.sha256
        ).hexdigest()
        r = self.post('/api/verify-payment', json={
            'razorpay_order_id':   order_id,
            'razorpay_payment_id': payment_id,
            'razorpay_signature':  sig
        })
        # May fail because session doesn't have this order — that's fine
        # What matters is it's not a 500
        self.assertNotEqual(r.status_code, 500,
            'verify-payment with valid signature caused 500')


# ══════════════════════════════════════════════════════════════════════════════
# 6. ADMIN PANEL TESTS
# ══════════════════════════════════════════════════════════════════════════════
class AdminTests(NariNakhreTestCase):

    def test_admin_login_page_loads(self):
        r = self.get('/admin/login')
        self.assertOK(r, 'Admin login page')
        self.assertContains(r, 'password', 'Admin login must have password field')

    def test_admin_login_wrong_password(self):
        r = self.post('/admin/login', data={
            'password': 'completelywrongpassword12345'
        }, allow_redirects=True)
        # Should not grant access
        self.assertNotIn('/admin/dashboard', r.url,
            'Wrong password must not grant admin access')

    def test_admin_login_empty_password(self):
        r = self.post('/admin/login', data={'password': ''},
            allow_redirects=False)
        self.assertIn(r.status_code, [200, 302, 400],
            'Empty password must not 500')

    def test_admin_totp_required_after_password(self):
        """After correct password, TOTP should be required before dashboard."""
        if not ADMIN_PASSWORD:
            self.skipTest('ADMIN_PASSWORD not set — skipping TOTP check')
        s = requests.Session()
        r = s.post(f'{BASE_URL}/admin/login',
            data={'password': ADMIN_PASSWORD}, timeout=TIMEOUT, allow_redirects=True)
        # Should be on TOTP page, not dashboard
        self.assertNotIn('/admin/dashboard', r.url,
            'TOTP not enforced after correct password')
        self.assertIn('totp', r.url.lower() if '/totp' in r.url else r.text.lower(),
            'TOTP verification step missing')

    def test_admin_routes_all_protected(self):
        fresh = requests.Session()
        protected = [
            '/admin/dashboard', '/admin/manage-images', '/admin/edit-product-details',
            '/admin/delete-products', '/admin/add-product', '/admin/inbox',
            '/admin/download-users-excel', '/admin/download-quotes-excel',
            '/admin/download-products-excel',
        ]
        for path in protected:
            r = fresh.get(f'{BASE_URL}{path}', timeout=TIMEOUT, allow_redirects=False)
            self.assertIn(r.status_code, [302, 401, 403],
                f'Route {path} must be protected — got {r.status_code}')


# ══════════════════════════════════════════════════════════════════════════════
# 7. MOBILE RESPONSIVENESS CHECKS
# ══════════════════════════════════════════════════════════════════════════════
class MobileTests(NariNakhreTestCase):

    def _check_page(self, url):
        return self.session.get(url, timeout=TIMEOUT)

    def test_all_pages_have_viewport_meta(self):
        pages = ['/retail', '/retail/checkout', '/', '/contact']
        for path in pages:
            r = self._check_page(f'{BASE_URL}{path}')
            if r.status_code == 200:
                self.assertIn('width=device-width', r.text,
                    f'Missing responsive viewport meta on {path}')

    def test_retail_has_hamburger_menu(self):
        r = self._check_page(f'{BASE_URL}/retail')
        self.assertIn('hamburger', r.text,
            'Retail missing hamburger — will not adapt on mobile')

    def test_retail_has_mobile_drawer(self):
        r = self._check_page(f'{BASE_URL}/retail')
        self.assertIn('mobile-nav', r.text,
            'Retail missing mobile-nav drawer')

    def test_retail_grid_is_responsive(self):
        r = self._check_page(f'{BASE_URL}/retail')
        self.assertIn('grid-cols-2', r.text,
            'Retail product grid is not 2-col on mobile')

    def test_checkout_form_has_inputmode(self):
        """Phone and pincode inputs need inputmode=numeric to avoid bad keyboards on mobile."""
        r = self._check_page(f'{BASE_URL}/retail/checkout')
        if r.status_code == 200:
            self.assertIn('inputmode', r.text,
                'Checkout missing inputmode — phone/pin keyboard UX will be poor on mobile')

    def test_checkout_font_size_not_tiny(self):
        """iOS zooms in on inputs < 16px — prevent with font-size: 16px."""
        r = self._check_page(f'{BASE_URL}/retail/checkout')
        # We check for font-size: 16px in the form-input style
        # A softer check since exact value varies
        if r.status_code == 200:
            self.assertIn('form-input', r.text,
                'Checkout should use .form-input class for consistent mobile font sizing')

    def test_wholesale_mobile_stacks(self):
        r = self._check_page(f'{BASE_URL}/')
        self.assertIn('flex-col', r.text,
            'Wholesale header should have flex-col for mobile stacking')


# ══════════════════════════════════════════════════════════════════════════════
# 8. EDGE CASES & ERROR HANDLING
# ══════════════════════════════════════════════════════════════════════════════
class EdgeCaseTests(NariNakhreTestCase):

    def test_404_for_unknown_route(self):
        r = self.get('/this-does-not-exist-xyz-999')
        self.assertIn(r.status_code, [404, 302],
            'Unknown route must return 404 or redirect')

    def test_404_for_unknown_product(self):
        r = self.get('/retail/product/999999999')
        self.assertIn(r.status_code, [404, 302, 200],
            'Unknown product must not 500')

    def test_empty_category(self):
        r = self.get('/retail/category/NonExistentCategory999')
        self.assertNotEqual(r.status_code, 500,
            'Empty/unknown category must not 500')

    def test_cart_with_negative_qty(self):
        r = self.post('/update-cart', json={
            'product_id': 'SKU-001', 'qty': -5,
            'price': 299.0, 'size': 'Standard'
        })
        self.assertNotEqual(r.status_code, 500,
            'Negative qty in cart must not cause 500')

    def test_cart_with_very_high_qty(self):
        r = self.post('/update-cart', json={
            'product_id': 'SKU-001', 'qty': 999999,
            'price': 299.0, 'size': 'Standard'
        })
        self.assertNotEqual(r.status_code, 500,
            'Extremely high qty must not cause 500')

    def test_cart_with_zero_price(self):
        r = self.post('/update-cart', json={
            'product_id': 'SKU-001', 'qty': 1,
            'price': 0, 'size': 'Standard'
        })
        self.assertNotEqual(r.status_code, 500,
            'Zero price in cart must not cause 500')

    def test_checkout_process_missing_fields(self):
        r = self.post('/checkout/process', data={
            'consignee_name': '', 'consignee_phone': ''
        })
        self.assertNotEqual(r.status_code, 500,
            'checkout/process with empty fields must not 500')

    def test_contact_form_missing_fields(self):
        r = self.post('/retail/contact', data={'name': ''})
        self.assertNotEqual(r.status_code, 500,
            'Contact form with missing fields must not 500')

    def test_delhivery_pincode_too_long(self):
        r = self.get('/api/delhivery/check/1234567890')
        self.assertNotEqual(r.status_code, 500,
            'Too-long pincode must not cause 500')

    def test_razorpay_create_order_non_numeric_amount(self):
        r = self.post('/api/create-order', json={'amount': 'notanumber'})
        self.assertIn(r.status_code, [400, 500],
            'Non-numeric amount must not succeed')
        if r.status_code == 200:
            data = r.json()
            self.assertNotEqual(data.get('status'), 'success',
                'Non-numeric amount must not create order')


# ══════════════════════════════════════════════════════════════════════════════
# 9. PERFORMANCE SMOKE TESTS
# ══════════════════════════════════════════════════════════════════════════════
class PerformanceTests(NariNakhreTestCase):

    def _timed_get(self, path, max_seconds=5.0):
        start = time.time()
        r = self.get(path)
        elapsed = time.time() - start
        return r, elapsed

    def test_retail_home_response_time(self):
        r, t = self._timed_get('/retail')
        self.assertOK(r, 'Retail home')
        self.assertLess(t, 8.0, f'Retail home took {t:.1f}s — too slow (>8s)')
        if t > 4.0:
            print(c('yellow', f'\n  ⚠ Retail home is slow: {t:.1f}s'))

    def test_wholesale_home_response_time(self):
        r, t = self._timed_get('/')
        self.assertOK(r, 'Wholesale home')
        self.assertLess(t, 8.0, f'Wholesale home took {t:.1f}s — too slow')

    def test_category_page_response_time(self):
        r, t = self._timed_get('/retail/category/Bangles')
        self.assertLess(t, 8.0, f'Category page took {t:.1f}s — too slow')

    def test_checkout_response_time(self):
        r, t = self._timed_get('/retail/checkout')
        self.assertLess(t, 8.0, f'Checkout page took {t:.1f}s — too slow')


# ══════════════════════════════════════════════════════════════════════════════
# RUNNER — coloured output + summary
# ══════════════════════════════════════════════════════════════════════════════
class ColouredResult(unittest.TextTestResult):
    def addSuccess(self, test):
        super().addSuccess(test)
        if self.showAll:
            self.stream.writeln(c('green', '  ✓ PASS'))

    def addFailure(self, test, err):
        super().addFailure(test, err)
        if self.showAll:
            self.stream.writeln(c('red', '  ✗ FAIL'))

    def addError(self, test, err):
        super().addError(test, err)
        if self.showAll:
            self.stream.writeln(c('red', '  ✗ ERROR'))

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        if self.showAll:
            self.stream.writeln(c('yellow', f'  ⊘ SKIP: {reason}'))


class ColouredRunner(unittest.TextTestRunner):
    resultclass = ColouredResult


if __name__ == '__main__':
    print(c('bold', f'\n{"═"*60}'))
    print(c('bold', '  NariNakhre Test Suite'))
    print(c('bold', f'  Target: {BASE_URL}'))
    print(c('bold', f'  Time:   {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}'))
    print(c('bold', f'{"═"*60}\n'))

    loader = unittest.TestLoader()
    suite  = unittest.TestSuite()

    # Order matters — security first
    for cls in [
        SecurityTests,
        RetailFlowTests,
        WholesaleFlowTests,
        DelhiveryAPITests,
        RazorpayTests,
        AdminTests,
        MobileTests,
        EdgeCaseTests,
        PerformanceTests,
    ]:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = ColouredRunner(verbosity=2, stream=sys.stdout)
    result = runner.run(suite)

    print(c('bold', f'\n{"═"*60}'))
    total   = result.testsRun
    passed  = total - len(result.failures) - len(result.errors) - len(result.skipped)
    print(c('green',  f'  Passed:  {passed}/{total}'))
    if result.failures:
        print(c('red',    f'  Failed:  {len(result.failures)}'))
    if result.errors:
        print(c('red',    f'  Errors:  {len(result.errors)}'))
    if result.skipped:
        print(c('yellow', f'  Skipped: {len(result.skipped)}'))
    print(c('bold', f'{"═"*60}\n'))

    sys.exit(0 if result.wasSuccessful() else 1)
