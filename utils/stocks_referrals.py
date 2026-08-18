"""Referral system for Nari Nakhre Stocks self-serve subscribers -- refer 3
people who actually pay and get a free month; a referred signup gets their
own first month at a discount (see app.py's /stocks/signup,
/stocks/auth/google/callback, and RAZORPAY_STOCKS_REFERRAL_PLAN_ID). Pure
counting/credit logic here, DB-orchestrating helpers alongside it, same
split as utils/stocks_subscription.py.

The reward is BANKED, not a live billing interruption: Razorpay keeps
charging the referrer normally every month while they're subscribed; when
they eventually cancel (see apply_referral_credits_on_cancellation, called
from app.py's /stocks/razorpay/webhook right after mark_subscription_cancelled),
each unredeemed credit extends subscription_current_period_end by 30 days
before access actually ends. No pause/resume of a live subscription, no
new cron job, no risk of a subscription getting stuck in a paused state --
see the plan discussion this was designed against for why that safer
option was chosen over literally skipping the next real charge.
"""
import secrets
from datetime import datetime, timedelta, timezone

from utils.stock_auth import _PASSWORD_ALPHABET

# Same alphabet create_viewer_account's auto-generated passwords use --
# letters/digits only, ambiguous characters (0/O/1/l/I) already excluded,
# which matters here too since a code gets read aloud/typed by hand when
# shared verbally rather than via the link.
REFERRAL_CODE_LENGTH = 8
REFERRALS_PER_FREE_MONTH = 3
FREE_MONTH_DAYS = 30


def _parse_timestamp(value):
    """subscription_current_period_end comes back from the Supabase RPC
    bridge as an ISO8601 string, not a native datetime (same shape
    utils/stocks_subscription.py's own _parse_timestamp normalizes) --
    kept as this module's own copy rather than imported, matching the
    established convention elsewhere in this codebase of each module
    holding its own small copy of this helper."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def generate_referral_code():
    return ''.join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(REFERRAL_CODE_LENGTH)).upper()


def get_or_create_referral_code(db, admin_id):
    """Returns this account's referral code, generating and persisting one
    (collision-checked) the first time it's needed -- existing accounts
    were never backfilled (see STOCKS_AUTH_ALTER_SQL), so for most of them
    this call IS the first time. Called from the profile page, the
    outbound-email footer, and signup-link generation alike, so whichever
    of those happens first is what actually creates the code."""
    row = db.execute('SELECT referral_code FROM stocks_admin_users WHERE id=?', (admin_id,)).fetchone()
    if row and row.get('referral_code'):
        return row['referral_code']

    for _ in range(10):  # collision retries -- astronomically unlikely to ever need more than one
        code = generate_referral_code()
        existing = db.execute('SELECT id FROM stocks_admin_users WHERE referral_code=?', (code,)).fetchone()
        if not existing:
            db.execute('UPDATE stocks_admin_users SET referral_code=?, updated_at=NOW() WHERE id=?', (code, admin_id))
            db.commit()
            return code
    raise RuntimeError('Could not generate a unique referral code after 10 attempts.')


def find_referrer_by_code(db, code):
    """Validates a referral code at signup time. Returns the referrer's
    row ({'id', 'username', 'name'}), or None if the code is blank or
    doesn't match any account -- callers treat both the same way (no
    referral applied), the distinction only matters for logging."""
    code = (code or '').strip().upper()
    if not code:
        return None
    return db.execute(
        'SELECT id, username, name FROM stocks_admin_users WHERE referral_code=?', (code,)
    ).fetchone()


def count_qualified_referrals(db, referrer_id):
    """How many of referrer_id's referrals actually completed at least one
    real payment -- subscription_status only ever leaves 'pending' once a
    payment has verified (see activate_subscription/record_recurring_charge
    in utils/stocks_subscription.py), so a signup that never pays doesn't
    count and can't be used to game the reward with fake accounts."""
    row = db.execute(
        "SELECT COUNT(*) AS n FROM stocks_admin_users WHERE referred_by_id=? AND subscription_status NOT IN ('none', 'pending')",
        (referrer_id,)
    ).fetchone()
    return (row.get('n') or 0) if row else 0


def available_referral_credits(db, referrer_id, credits_redeemed=None):
    """Unredeemed free-month credits: qualified_count // REFERRALS_PER_FREE_MONTH
    minus whatever's already been applied at a past cancellation (see
    apply_referral_credits_on_cancellation). credits_redeemed can be
    passed in directly when the caller already has the row, to skip a
    second query; otherwise it's looked up here. Never negative."""
    if credits_redeemed is None:
        row = db.execute(
            'SELECT referral_credits_redeemed FROM stocks_admin_users WHERE id=?', (referrer_id,)
        ).fetchone()
        credits_redeemed = (row.get('referral_credits_redeemed') or 0) if row else 0
    qualified = count_qualified_referrals(db, referrer_id)
    return max(0, qualified // REFERRALS_PER_FREE_MONTH - credits_redeemed)


def apply_referral_credits_on_cancellation(db, admin_id):
    """Called right after mark_subscription_cancelled (see app.py's
    /stocks/razorpay/webhook) -- extends subscription_current_period_end
    by FREE_MONTH_DAYS per unredeemed credit and marks them redeemed, so
    the referrer's access actually lasts the extra month(s) they earned
    before really ending, instead of the reward just evaporating at
    cancellation. No-op (returns 0) if there are no credits available, or
    if subscription_current_period_end is somehow unset (nothing to
    extend from -- shouldn't happen for an account that was ever active,
    but this never guesses a date rather than risk conjuring one).
    Returns the number of credits actually applied."""
    row = db.execute(
        'SELECT referral_credits_redeemed, subscription_current_period_end FROM stocks_admin_users WHERE id=?',
        (admin_id,)
    ).fetchone()
    if not row:
        return 0
    redeemed_so_far = row.get('referral_credits_redeemed') or 0
    credits = available_referral_credits(db, admin_id, credits_redeemed=redeemed_so_far)
    if credits <= 0:
        return 0

    current_end = row.get('subscription_current_period_end')
    if current_end is None:
        return 0

    new_end = _parse_timestamp(current_end) + timedelta(days=FREE_MONTH_DAYS * credits)
    db.execute(
        'UPDATE stocks_admin_users SET subscription_current_period_end=?, referral_credits_redeemed=?, updated_at=NOW() WHERE id=?',
        (new_end, redeemed_so_far + credits, admin_id)
    )
    db.commit()
    return credits
