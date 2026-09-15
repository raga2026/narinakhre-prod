"""Export order tracking and invoicing -- schema + data access only.

Covers the wholesale-export side (buyer abroad, GST zero-rated under LUT):
an export_orders header row per deal, one or more export_invoices
(proforma then commercial) against it, and export_shipments once goods
move. Additive alongside the existing order_shipping (retail/domestic)
schema -- order_shipping is never altered here. export_orders links back
to it optionally via order_shipping_id for the (probably rare) case an
export deal started life as a normal storefront order; it is nullable
because most export orders are created manually outside the cart flow.

No routes/UI here yet -- callers wire this module in themselves. Follows
the same table-init-function + db.execute(?, params) style as
stoqbell/utils/announcements.py: line_items is stored as a TEXT column
holding a JSON string (json.dumps/json.loads at the call site), matching
order_shipping.cart_items_json and quotes.items_json elsewhere in this
codebase, rather than a native jsonb column.
"""
import json
from datetime import datetime

# Single source of truth for the status enum -- reused for the CHECK
# constraint below, for admin-form validation, and for the ordered
# stage timeline on the /admin/export-orders detail page.
EXPORT_ORDER_STATUSES = [
    'design_shared', 'advance_pending', 'advance_received', 'invoiced',
    'shipped', 'balance_pending', 'completed',
]
EXPORT_ORDER_STATUS_LABELS = {
    'design_shared': 'Design Shared',
    'advance_pending': 'Advance Pending',
    'advance_received': 'Advance Received',
    'invoiced': 'Invoiced',
    'shipped': 'Shipped',
    'balance_pending': 'Balance Pending',
    'completed': 'Completed',
}
_STATUS_CHECK_SQL = ', '.join(f"'{s}'" for s in EXPORT_ORDER_STATUSES)

EXPORT_ORDERS_TABLES_SQL = [
    f'''CREATE TABLE IF NOT EXISTS export_orders (
        id BIGSERIAL PRIMARY KEY,
        order_shipping_id BIGINT REFERENCES order_shipping(id) ON DELETE SET NULL,
        buyer_name TEXT NOT NULL,
        buyer_email TEXT,
        buyer_address TEXT NOT NULL,
        buyer_country TEXT NOT NULL,
        currency TEXT NOT NULL DEFAULT 'USD',
        invoice_value_foreign NUMERIC,
        exchange_rate NUMERIC,
        invoice_value_inr NUMERIC,
        status TEXT NOT NULL DEFAULT 'design_shared'
            CHECK (status IN ({_STATUS_CHECK_SQL})),
        advance_percent NUMERIC NOT NULL DEFAULT 50,
        advance_received_at TIMESTAMP,
        balance_received_at TIMESTAMP,
        firc_reference TEXT,
        ad_code_reference TEXT,
        lut_reference TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''',
    '''CREATE TABLE IF NOT EXISTS export_invoices (
        id BIGSERIAL PRIMARY KEY,
        export_order_id BIGINT NOT NULL REFERENCES export_orders(id) ON DELETE CASCADE,
        invoice_type TEXT NOT NULL CHECK (invoice_type IN ('proforma', 'commercial')),
        invoice_number TEXT NOT NULL UNIQUE,
        invoice_date DATE NOT NULL DEFAULT CURRENT_DATE,
        gst_treatment TEXT NOT NULL DEFAULT 'zero_rated_lut',
        line_items TEXT NOT NULL,
        subtotal NUMERIC NOT NULL,
        total NUMERIC NOT NULL,
        pdf_url TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''',
    '''CREATE TABLE IF NOT EXISTS export_shipments (
        id BIGSERIAL PRIMARY KEY,
        export_order_id BIGINT NOT NULL REFERENCES export_orders(id) ON DELETE CASCADE,
        courier TEXT,
        awb_number TEXT,
        shipping_bill_number TEXT,
        shipped_at TIMESTAMP,
        estimated_delivery TIMESTAMP,
        delivered_at TIMESTAMP,
        tracking_url TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''',
]

EXPORT_ORDERS_INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_export_orders_status ON export_orders (status)",
    "CREATE INDEX IF NOT EXISTS idx_export_orders_order_shipping_id ON export_orders (order_shipping_id)",
    "CREATE INDEX IF NOT EXISTS idx_export_invoices_export_order_id ON export_invoices (export_order_id)",
    "CREATE INDEX IF NOT EXISTS idx_export_shipments_export_order_id ON export_shipments (export_order_id)",
]


def initialize_export_tables_if_needed(client):
    for sql in EXPORT_ORDERS_TABLES_SQL:
        try:
            client.rpc('execute_sql', {'query': sql}).execute()
        except Exception as e:
            print(f'Export tables init warning (may already exist): {e}')
    for sql in EXPORT_ORDERS_INDEXES_SQL:
        try:
            client.rpc('execute_sql', {'query': sql}).execute()
        except Exception as e:
            print(f'Export tables index warning (may already exist): {e}')


# ---------------------------------------------------------------------------
# export_orders
# ---------------------------------------------------------------------------

def create_export_order(db, buyer_name, buyer_address, buyer_country,
                         buyer_email=None, currency='USD',
                         invoice_value_foreign=None, exchange_rate=None,
                         invoice_value_inr=None, advance_percent=50,
                         lut_reference=None, order_shipping_id=None):
    db.execute(
        '''INSERT INTO export_orders
           (order_shipping_id, buyer_name, buyer_email, buyer_address, buyer_country,
            currency, invoice_value_foreign, exchange_rate, invoice_value_inr,
            advance_percent, lut_reference)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
        (order_shipping_id, buyer_name.strip(), buyer_email, buyer_address.strip(),
         buyer_country.strip(), currency, invoice_value_foreign, exchange_rate,
         invoice_value_inr, advance_percent, lut_reference)
    )
    db.commit()
    return db.execute(
        "SELECT * FROM export_orders WHERE id = (SELECT MAX(id) FROM export_orders)"
    ).fetchone()


def get_export_order(db, export_order_id):
    return db.execute(
        "SELECT * FROM export_orders WHERE id = ?", (export_order_id,)
    ).fetchone()


def list_export_orders(db, status=None):
    if status:
        return db.execute(
            "SELECT * FROM export_orders WHERE status = ? ORDER BY created_at DESC",
            (status,)
        ).fetchall()
    return db.execute(
        "SELECT * FROM export_orders ORDER BY created_at DESC"
    ).fetchall()


def update_export_order_status(db, export_order_id, status):
    db.execute(
        "UPDATE export_orders SET status = ?, updated_at = NOW() WHERE id = ?",
        (status, export_order_id)
    )
    db.commit()


def mark_export_order_advance_received(db, export_order_id):
    db.execute(
        '''UPDATE export_orders
           SET status = 'advance_received', advance_received_at = NOW(), updated_at = NOW()
           WHERE id = ?''',
        (export_order_id,)
    )
    db.commit()


def mark_export_order_balance_received(db, export_order_id):
    db.execute(
        '''UPDATE export_orders
           SET status = 'completed', balance_received_at = NOW(), updated_at = NOW()
           WHERE id = ?''',
        (export_order_id,)
    )
    db.commit()


def update_export_order_compliance_refs(db, export_order_id, firc_reference=None,
                                         ad_code_reference=None, lut_reference=None):
    db.execute(
        '''UPDATE export_orders
           SET firc_reference = COALESCE(?, firc_reference),
               ad_code_reference = COALESCE(?, ad_code_reference),
               lut_reference = COALESCE(?, lut_reference),
               updated_at = NOW()
           WHERE id = ?''',
        (firc_reference, ad_code_reference, lut_reference, export_order_id)
    )
    db.commit()


def admin_update_export_order(db, export_order_id, status=None, advance_received_at=None,
                               firc_reference=None, ad_code_reference=None):
    """Manual edit from the /admin/export-orders detail page. Each param is
    None when the admin left that field untouched -- COALESCE keeps the
    existing value in that case, so this can't clear a field back to NULL,
    only fill one in or change status. Caller validates `status` against
    EXPORT_ORDER_STATUSES before calling."""
    db.execute(
        '''UPDATE export_orders
           SET status = COALESCE(?, status),
               advance_received_at = COALESCE(?, advance_received_at),
               firc_reference = COALESCE(?, firc_reference),
               ad_code_reference = COALESCE(?, ad_code_reference),
               updated_at = NOW()
           WHERE id = ?''',
        (status, advance_received_at, firc_reference, ad_code_reference, export_order_id)
    )
    db.commit()


# ---------------------------------------------------------------------------
# export_invoices
# ---------------------------------------------------------------------------

def generate_export_invoice_number(db, year=None):
    """Next sequential NN/EXP/<year>/<seq> number for the given year.

    Counts existing invoices for the year rather than holding a lock, so a
    concurrent write could in theory collide -- the UNIQUE constraint on
    invoice_number is the backstop, same tradeoff internal_order_id makes
    elsewhere in this codebase (see app.py's NN-SHP-<timestamp> ids).
    """
    year = year or datetime.now().year
    prefix = f'NN/EXP/{year}/'
    row = db.execute(
        "SELECT COUNT(*) as cnt FROM export_invoices WHERE invoice_number LIKE ?",
        (f'{prefix}%',)
    ).fetchone()
    count = int((row or {}).get('cnt', 0) or 0)
    return f'{prefix}{count + 1:03d}'


def create_export_invoice(db, export_order_id, invoice_type, line_items, subtotal, total,
                           gst_treatment='zero_rated_lut', invoice_date=None, invoice_number=None):
    invoice_number = invoice_number or generate_export_invoice_number(db)
    invoice_date = invoice_date or datetime.now().strftime('%Y-%m-%d')
    db.execute(
        '''INSERT INTO export_invoices
           (export_order_id, invoice_type, invoice_number, invoice_date,
            gst_treatment, line_items, subtotal, total)
           VALUES (?,?,?,?,?,?,?,?)''',
        (export_order_id, invoice_type, invoice_number, invoice_date,
         gst_treatment, json.dumps(line_items), subtotal, total)
    )
    db.commit()
    if invoice_type == 'commercial':
        db.execute(
            "UPDATE export_orders SET status = 'invoiced', updated_at = NOW() WHERE id = ?",
            (export_order_id,)
        )
        db.commit()
    return db.execute(
        "SELECT * FROM export_invoices WHERE invoice_number = ?", (invoice_number,)
    ).fetchone()


def get_export_invoice(db, invoice_id):
    return db.execute(
        "SELECT * FROM export_invoices WHERE id = ?", (invoice_id,)
    ).fetchone()


def list_export_invoices(db, export_order_id):
    return db.execute(
        "SELECT * FROM export_invoices WHERE export_order_id = ? ORDER BY created_at DESC",
        (export_order_id,)
    ).fetchall()


def update_export_invoice_pdf_url(db, invoice_id, pdf_url):
    db.execute(
        "UPDATE export_invoices SET pdf_url = ? WHERE id = ?",
        (pdf_url, invoice_id)
    )
    db.commit()


# ---------------------------------------------------------------------------
# export_shipments
# ---------------------------------------------------------------------------

def create_export_shipment(db, export_order_id, courier=None, awb_number=None,
                            shipping_bill_number=None, estimated_delivery=None, tracking_url=None):
    db.execute(
        '''INSERT INTO export_shipments
           (export_order_id, courier, awb_number, shipping_bill_number,
            shipped_at, estimated_delivery, tracking_url)
           VALUES (?,?,?,?,NOW(),?,?)''',
        (export_order_id, courier, awb_number, shipping_bill_number,
         estimated_delivery, tracking_url)
    )
    db.commit()
    db.execute(
        "UPDATE export_orders SET status = 'shipped', updated_at = NOW() WHERE id = ?",
        (export_order_id,)
    )
    db.commit()
    return db.execute(
        '''SELECT * FROM export_shipments WHERE export_order_id = ?
           ORDER BY id DESC LIMIT 1''',
        (export_order_id,)
    ).fetchone()


def get_export_shipment(db, shipment_id):
    return db.execute(
        "SELECT * FROM export_shipments WHERE id = ?", (shipment_id,)
    ).fetchone()


def list_export_shipments(db, export_order_id):
    return db.execute(
        "SELECT * FROM export_shipments WHERE export_order_id = ? ORDER BY created_at DESC",
        (export_order_id,)
    ).fetchall()


def mark_export_shipment_delivered(db, shipment_id):
    db.execute(
        "UPDATE export_shipments SET delivered_at = NOW() WHERE id = ?",
        (shipment_id,)
    )
    db.commit()
