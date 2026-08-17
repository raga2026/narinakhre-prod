"""Razorpay recurring-subscription support for Nari Nakhre Stocks' self-serve
signup (see app.py's /stocks/signup, /stocks/subscribe/verify and
/stocks/razorpay/webhook). Pure signature/date logic lives here so it's
directly unit-testable without a live Razorpay client or DB -- same split as
the rest of this codebase (utils/fundamental_screen.py, utils/nns_score.py,
etc.). DB-orchestrating helpers are the thin functions at the bottom.

Account rows are ordinary stocks_admin_users rows with role='viewer' -- see
utils/stock_auth.py's STOCKS_AUTH_ALTER_SQL for the subscription_* columns
this module reads and writes."""
import hashlib
import hmac
from datetime import datetime, timedelta, timezone

from werkzeug.security import generate_password_hash

IST = timezone(timedelta(hours=5, minutes=30))

# How many billing cycles a subscription is created for -- Razorpay requires
# a fixed total_count (there's no "bill forever" option), so this is just a
# large-but-bounded number standing in for "until cancelled". 240 months =
# 20 years, comfortably longer than this product will exist unmodified;
# the subscription can still be cancelled by the customer or from the
# Razorpay dashboard at any time regardless of this count.
SUBSCRIPTION_TOTAL_COUNT_MONTHS = 240

# How many days before subscription_current_period_end the reminder email
# goes out -- see find_expiring_subscribers.
REMINDER_WINDOW_DAYS = 3


def verify_signature(message, signature, secret):
    """HMAC-SHA256(message, secret) constant-time-compared against signature
    -- the exact algorithm Razorpay's own SDK uses (see
    razorpay.utility.Utility.verify_signature), reimplemented here so this
    module doesn't need a live razorpay.Client (with real auth credentials)
    just to check a signature, which matters for unit testing this without
    a network client. message/signature/secret are all str; message and
    secret are UTF-8 encoded before hashing, matching the SDK."""
    if not signature:
        return False
    digest = hmac.new(secret.encode('utf-8'), message.encode('utf-8'), hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, signature)


def verify_subscription_payment_signature(razorpay_payment_id, razorpay_subscription_id, razorpay_signature, secret):
    """Verifies the signature Razorpay Checkout hands back after a
    subscription's first payment succeeds -- message format is
    payment_id|subscription_id (NOT subscription_id|payment_id -- this is
    the opposite order from a plain order's order_id|payment_id, and
    getting it backwards just makes every signature fail to verify, so this
    exact order is deliberate, matching Razorpay's documented format and
    their own SDK's verify_subscription_payment_signature)."""
    message = f'{razorpay_payment_id}|{razorpay_subscription_id}'
    return verify_signature(message, razorpay_signature, secret)


def verify_webhook_signature(raw_body, signature, secret):
    """Verifies the X-Razorpay-Signature header on an incoming webhook POST
    -- here the raw request body itself (as received, NOT re-serialized
    JSON, which could reorder keys/whitespace and change the bytes being
    signed) is the HMAC message. raw_body may be str or bytes; decoded to
    str first since verify_signature encodes it itself."""
    if isinstance(raw_body, bytes):
        raw_body = raw_body.decode('utf-8')
    return verify_signature(raw_body, signature, secret)


def has_stocks_access(is_pro, subscription_status, subscription_current_period_end, now=None):
    """The actual login gate (see authenticate_stocks_admin in
    utils/stock_auth.py, and the Google callback in app.py) -- is_pro short-
    circuits to True before subscription_status is even looked at, since a
    manually-added/comped account's access has nothing to do with Razorpay
    at all. Falls through to subscription_is_current for everyone else."""
    if is_pro:
        return True
    return subscription_is_current(subscription_status, subscription_current_period_end, now)


def subscription_is_current(subscription_status, subscription_current_period_end, now=None):
    """True if this account's paid access should currently work. 'none'
    (never a paid account -- an admin-created viewer) always passes, since
    this check only ever means anything for accounts that went through
    Razorpay. 'pending' (signed up, checkout never completed) always fails
    -- there's nothing to grant access yet. 'active' passes as long as
    Razorpay hasn't told us otherwise (a lapsed 'active' row whose period
    end has quietly passed with no webhook yet is treated as still current
    -- see the module docstring on why the webhook, not this date alone, is
    the source of truth for cutting a still-'active' row off). 'cancelled'
    passes only through the end of whatever period was already paid for.
    'halted' (Razorpay gave up retrying a failed renewal) never passes."""
    if subscription_status in (None, 'none'):
        return True
    if subscription_status == 'active':
        return True
    if subscription_status == 'cancelled':
        if subscription_current_period_end is None:
            return False
        now = now or datetime.now(timezone.utc)
        return _parse_timestamp(subscription_current_period_end) >= now
    return False  # 'pending', 'halted', or anything unrecognized


def _parse_timestamp(value):
    """subscription_current_period_end comes back from the Supabase RPC
    bridge as an ISO8601 string, not a native datetime (same as
    stock_job_runs.last_success_at in utils/stock_alerting.py's own
    _parse_timestamp) -- normalizes either into a timezone-aware datetime
    so callers can compare it directly against datetime.now(timezone.utc)
    regardless of which shape it arrived in (a plain datetime is what
    activate_subscription/record_recurring_charge pass in and what unit
    tests construct directly)."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def days_until(period_end, now=None):
    """Whole days remaining until period_end, may be negative if it's
    already passed. Used for display ('renews in 3 days') and by
    find_expiring_subscribers' pure window check below."""
    now = now or datetime.now(timezone.utc)
    delta = _parse_timestamp(period_end) - now
    return delta.days


def is_within_reminder_window(period_end, now=None, window_days=REMINDER_WINDOW_DAYS):
    """True if period_end falls within the next window_days (inclusive of
    today, exclusive of already-passed dates) -- the pure predicate behind
    find_expiring_subscribers' SQL WHERE clause, kept here so the boundary
    logic itself (not just the query) is unit-testable."""
    remaining = days_until(period_end, now)
    return 0 <= remaining <= window_days


def hash_password(password):
    return generate_password_hash(password)


# --- DB orchestration ------------------------------------------------------

def create_pending_subscriber(db, email, name, password):
    """Creates a role='viewer' row for a self-serve signup, before payment
    has been confirmed -- is_active=0 and subscription_status='pending', so
    authenticate_stocks_admin already refuses login (is_active check) even
    before subscription_is_current is consulted at all. must_change_password
    is 0 (not 1, unlike create_viewer_account's admin-created viewers) since
    this password was chosen by the person themselves, not auto-generated
    and emailed in plaintext -- there's nothing to force a change away from.
    Returns (row, error_message)."""
    email = (email or '').strip().lower()
    if not email:
        return None, 'Email is required.'
    if not password or len(password) < 8:
        return None, 'Password must be at least 8 characters.'

    existing = db.execute(
        'SELECT id, username, name, is_active, is_pro, subscription_status, subscription_current_period_end '
        'FROM stocks_admin_users WHERE username=?',
        (email,)
    ).fetchone()
    if existing:
        return existing, 'existing'

    password_hash = hash_password(password)
    db.execute(
        '''INSERT INTO stocks_admin_users
               (username, password_hash, role, name, is_active, must_change_password, subscription_status)
           VALUES (?, ?, 'viewer', ?, 0, 0, 'pending')''',
        (email, password_hash, (name or '').strip() or None)
    )
    db.commit()

    row = db.execute(
        'SELECT id, username, name, subscription_status, razorpay_subscription_id '
        'FROM stocks_admin_users WHERE username=?',
        (email,)
    ).fetchone()
    return row, None


def attach_razorpay_subscription(db, admin_id, customer_id, subscription_id):
    db.execute(
        'UPDATE stocks_admin_users SET razorpay_customer_id=?, razorpay_subscription_id=?, updated_at=NOW() WHERE id=?',
        (customer_id, subscription_id, admin_id)
    )
    db.commit()


def activate_subscription(db, admin_id, current_period_end):
    """First successful payment (see /stocks/subscribe/verify) or any later
    renewal for an account looked up by its own id -- grants/confirms
    access and records the new period end."""
    db.execute(
        '''UPDATE stocks_admin_users
           SET is_active=1, subscription_status='active', subscription_current_period_end=?, updated_at=NOW()
           WHERE id=?''',
        (current_period_end, admin_id)
    )
    db.commit()


def record_recurring_charge(db, razorpay_subscription_id, current_period_end):
    """Same as activate_subscription, but looked up by razorpay_subscription_id
    -- this is what the webhook uses (see app.py's /stocks/razorpay/webhook
    handling 'subscription.charged'), since Razorpay's webhook payload
    carries the subscription id, not our internal admin_id. No-op (0 rows
    affected) if the subscription id isn't recognized, which the caller
    should log but not treat as fatal -- Razorpay retries webhooks on
    non-200 responses, and a genuinely unknown subscription id retrying
    forever helps no one."""
    db.execute(
        '''UPDATE stocks_admin_users
           SET is_active=1, subscription_status='active', subscription_current_period_end=?, updated_at=NOW()
           WHERE razorpay_subscription_id=?''',
        (current_period_end, razorpay_subscription_id)
    )
    db.commit()


def find_account_by_razorpay_subscription_id(db, razorpay_subscription_id):
    """Looks up the stocks_admin_users row a webhook event's subscription_id
    belongs to -- the webhook payload itself carries no other identifying
    detail (no admin_id, no email), so this is how app.py's
    /stocks/razorpay/webhook turns a bare subscription_id into a
    username/name to notify about (see send_admin_subscription_cancelled_email
    in utils/suggestion_email.py). Returns None if unrecognized."""
    return db.execute(
        'SELECT id, username, name FROM stocks_admin_users WHERE razorpay_subscription_id=?',
        (razorpay_subscription_id,)
    ).fetchone()


def mark_subscription_cancelled(db, razorpay_subscription_id):
    """'subscription.cancelled' / 'subscription.completed' webhook events --
    access keeps working (see subscription_is_current) through whatever
    subscription_current_period_end was already recorded, it just won't
    auto-renew again."""
    db.execute(
        "UPDATE stocks_admin_users SET subscription_status='cancelled', updated_at=NOW() WHERE razorpay_subscription_id=?",
        (razorpay_subscription_id,)
    )
    db.commit()


def mark_subscription_halted(db, razorpay_subscription_id):
    """'subscription.halted' -- Razorpay already exhausted its own retry
    attempts on a failed renewal charge, so this cuts access immediately
    rather than waiting out the already-recorded period end."""
    db.execute(
        "UPDATE stocks_admin_users SET subscription_status='halted', updated_at=NOW() WHERE razorpay_subscription_id=?",
        (razorpay_subscription_id,)
    )
    db.commit()


def find_expiring_subscribers(db, window_days=REMINDER_WINDOW_DAYS):
    """Rows whose paid access is about to lapse -- status 'active' (an
    upcoming renewal charge) or 'cancelled' (access actually ending, no
    renewal coming) with subscription_current_period_end within the next
    window_days, that haven't already been reminded for this same period
    end (subscription_reminder_sent_for IS DISTINCT FROM the period end --
    so a renewed subscription's later expiry still gets its own reminder)."""
    return db.execute(
        '''SELECT id, username, name, subscription_status, subscription_current_period_end
           FROM stocks_admin_users
           WHERE subscription_status IN ('active', 'cancelled')
             AND subscription_current_period_end IS NOT NULL
             AND subscription_current_period_end >= NOW()
             AND subscription_current_period_end <= NOW() + (?)::interval
             AND subscription_reminder_sent_for IS DISTINCT FROM subscription_current_period_end''',
        (f'{window_days} days',)
    ).fetchall()


def mark_reminder_sent(db, admin_id, period_end):
    db.execute(
        'UPDATE stocks_admin_users SET subscription_reminder_sent_for=?, updated_at=NOW() WHERE id=?',
        (period_end, admin_id)
    )
    db.commit()
