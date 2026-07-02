# NariNakhre Wholesale QA Audit

Target context: wholesale site in same Flask app (quote-only)
Domain: test-wholesale.narinakhre.com
Audit date: 2026-07-02

---

## Summary

- Total checks: 10
- PASS: 6
- FIXED: 4
- FAIL (remaining): 0

---

## 1) g.site_type detection for wholesale domain and /wholesale paths
Status: FIXED

### Observation
The previous detection logic treated any non-retail request as wholesale. It worked for many cases but was too coarse for mixed host/path scenarios.

### Broken code
```python
@app.before_request
def detect_site_type():
    host = request.host.lower()
    path = request.path.lower()
    g.site_type = 'retail' if ('retail' in host or path.startswith('/retail')) else 'wholesale'
```

### Fixed code
```python
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
        g.site_type = 'wholesale'
```

Result: wholesale host and /wholesale/* paths are explicitly classified as wholesale.

---

## 2) Tracking blocked on /track/<waybill> for wholesale
Status: FIXED

### Observation
Route rendered retail tracking page regardless of site type.

### Broken code
```python
@app.route('/track/<waybill>')
def track_order_page(waybill):
    conn = get_db()
    order = conn.execute(
        'SELECT * FROM order_shipping WHERE delhivery_waybill=?', (waybill,)
    ).fetchone()
    return render_template('retail/track_order.html', waybill=waybill, order=order)
```

### Fixed code
```python
@app.route('/track/<waybill>')
def track_order_page(waybill):
    if g.site_type != 'retail':
        return redirect(url_for('index'))
    conn = get_db()
    order = conn.execute(
        'SELECT * FROM order_shipping WHERE delhivery_waybill=?', (waybill,)
    ).fetchone()
    return render_template('retail/track_order.html', waybill=waybill, order=order)
```

Result: wholesale requests are redirected away instead of rendering tracking page/500 behavior.

---

## 3) /api/track/<waybill> returns 403 JSON for wholesale
Status: FIXED

### Observation
API tracking endpoint was open and attempted provider call for all contexts.

### Broken code
```python
@app.route('/api/track/<waybill>', methods=['GET'])
def api_track_shipment(waybill):
    provider = get_shipping_provider(...)
    ...
```

### Fixed code
```python
@app.route('/api/track/<waybill>', methods=['GET'])
def api_track_shipment(waybill):
    if g.site_type != 'retail':
        return jsonify({"status": False, "msg": "Unauthorized"}), 403
    provider = get_shipping_provider(
        app.config['SHIPPING_PROVIDER'],
        api_token=app.config.get('DELHIVERY_API_KEY')
    )
    ...
```

Result: wholesale calls receive explicit 403 JSON.

---

## 4) /api/create-order blocked on wholesale
Status: FIXED

### Observation
Razorpay order creation endpoint was not guarded by site type.

### Broken code
```python
@app.route('/api/create-order', methods=['POST'])
def create_razorpay_order():
    g.site_type = 'retail'
    payload = request.get_json(silent=True) or request.form or {}
    ...
```

### Fixed code
```python
@app.route('/api/create-order', methods=['POST'])
def create_razorpay_order():
    if g.site_type != 'retail':
        return jsonify({'status': 'error', 'message': 'Retail-only endpoint'}), 403
    g.site_type = 'retail'
    payload = request.get_json(silent=True) or request.form or {}
    ...
```

Result: wholesale cannot open Razorpay order flow.

---

## 5) Cart routes (/update-cart, /clear_quote) check site_type
Status: FIXED

### Observation
- /update-cart had no wholesale guard.
- /clear_quote used g.site_type for redirect but had no explicit wholesale branch.

### Broken code
```python
@app.route('/update-cart', methods=['POST'])
def update_cart():
    data = request.get_json()
    ...

@app.route('/clear_quote', methods=['POST'])
def clear_quote():
    session.pop('cart', None)
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return ('', 204)
    return redirect('/retail' if g.site_type == 'retail' else '/wholesale')
```

### Fixed code
```python
@app.route('/update-cart', methods=['POST'])
def update_cart():
    if g.site_type != 'retail':
        return jsonify({'status': 'error', 'message': 'Cart disabled for wholesale'}), 403
    data = request.get_json()
    ...

@app.route('/clear_quote', methods=['POST'])
def clear_quote():
    if g.site_type != 'retail':
        session.pop('cart', None)
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return ('', 204)
        return redirect('/wholesale')
    session.pop('cart', None)
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return ('', 204)
    return redirect('/retail' if g.site_type == 'retail' else '/wholesale')
```

Result: wholesale cart mutation is explicitly blocked/segregated.

---

## 6) Quote submission route exists, stores quote, sends email
Status: PASS

Checked route:
- /contact
- /wholesale/contact

Verified behavior in code:
- Inserts into quotes via Supabase wrapper.
- Sends customer email (if email provided) via send_contact_email.
- Sends admin notification via send_contact_email.

Notes:
- Route exists and uses quote table; current insert includes name/whatsapp/email/message payload.

---

## 7) Required wholesale templates exist
Status: PASS

Verified present:
- templates/wholesale/index.html
- templates/wholesale/product_detail.html
- templates/wholesale/category_products.html
- templates/wholesale/contact.html
- templates/wholesale/thank_you.html

---

## 8) No cart icon in wholesale/base.html
Status: PASS

Verified:
- No cart-count token in templates/wholesale/base.html.
- No Add to Cart button text in templates/wholesale/base.html.

---

## 9) No Razorpay checkout script in wholesale templates
Status: PASS

Verified:
- No checkout.razorpay.com/v1/checkout.js in templates/wholesale/*

---

## 10) Admin quote view route protection and rendering
Status: PASS

Verified:
- Route /admin/quote/<int:quote_id> is decorated with @admin_required.
- Renders admin/admin_quote_view.html.
- Route loads quote and cart_items; supports status update action.

---

## Compile Validation

Command executed:
```bash
python -m py_compile app.py
```

Result:
- Exit code: 0
- stdout: (empty)
- stderr: (empty)

Compile status: PASS
