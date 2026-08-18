import hmac
import os
import secrets
import string
from functools import wraps

from flask import flash, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from utils.stocks_subscription import has_stocks_access

# Letters/digits only, and the commonly-confused ones (0/O, 1/l/I) dropped --
# these are emailed in plaintext to a small trusted circle (see
# create_viewer_account) and sometimes read aloud or typed by hand, so a
# dense symbol-and-mixed-case token_urlsafe() string is worse than it needs
# to be for that. Still plenty of entropy at PASSWORD_LENGTH for this
# threat model (not a public product).
_PASSWORD_ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789'
PASSWORD_LENGTH = 10


def _generate_simple_password():
    return ''.join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(PASSWORD_LENGTH))


def has_valid_cron_secret(request_headers, secret):
    """Checks the X-Cron-Secret header against secret using constant-time
    comparison -- shared by every Stocks cron-triggered route in app.py so
    none of them duplicate the hmac call by hand. Returns False if secret
    is empty/unset."""
    if not secret:
        return False
    provided = request_headers.get('X-Cron-Secret', '')
    return hmac.compare_digest(provided, secret)


def legacy_stocks_redirect(new_endpoint, code=301):
    """Builds a view function that redirects a retired /admin/stocks/...
    path to its /stocks/... replacement, preserving any URL params (e.g.
    admin_id) and query string (needed for kite/callback, which gets
    request_token/status appended by Zerodha). code=308 should be used for
    routes that accept POST -- 301/302 aren't guaranteed to preserve the
    method and body on redirect, so a POST could silently become a GET
    partway through (Kite's postback caller, or a login form submission);
    308 fixes that. Transition-period only, see app.py's
    _LEGACY_STOCKS_ROUTES for where these are registered."""
    def _redirect(**kwargs):
        target = url_for(new_endpoint, **kwargs)
        if request.query_string:
            target = f'{target}?{request.query_string.decode()}'
        return redirect(target, code=code)
    return _redirect

# Nari Nakhre Stocks has its own login, separate from the storefront's
# /admin/login -- one super_admin (full access, manages child admins and the
# Kite API token) plus any number of child admins (scoped access, created by
# super_admin). Kept in its own table/file so it never touches the
# storefront's single admin_required/session['is_admin'] flow.
STOCKS_AUTH_TABLES_SQL = [
    '''CREATE TABLE IF NOT EXISTS stocks_admin_users (
        id BIGSERIAL PRIMARY KEY,
        username TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'child_admin' CHECK (role IN ('super_admin', 'child_admin')),
        is_active INTEGER DEFAULT 1,
        created_by BIGINT REFERENCES stocks_admin_users(id),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )'''
]


# Added after stocks_admin_users already had data in it -- kept separate so
# ADD COLUMN IF NOT EXISTS / the constraint swap apply cleanly, same
# additive-migration pattern used everywhere else in this codebase.
# 'viewer' is a read-only role for the small trusted circle previously only
# in stocks_email_recipients (see utils/suggestion_email.py) -- they can log
# in and see their own suggestions, nothing else. name is nullable and only
# really used by viewer accounts; super_admin/child_admin rows leave it null.
STOCKS_AUTH_ALTER_SQL = [
    'ALTER TABLE stocks_admin_users ADD COLUMN IF NOT EXISTS name TEXT',
    'ALTER TABLE stocks_admin_users DROP CONSTRAINT IF EXISTS stocks_admin_users_role_check',
    "ALTER TABLE stocks_admin_users ADD CONSTRAINT stocks_admin_users_role_check "
    "CHECK (role IN ('super_admin', 'child_admin', 'viewer'))",
    # Per-viewer override letting a specific viewer see the (staff)
    # watchlist page -- off by default; only meaningful for role='viewer'
    # rows, super_admin/child_admin already have full watchlist access
    # regardless of this flag. Set at account-creation time (see
    # create_viewer_account) via a toggle on /stocks/users.
    'ALTER TABLE stocks_admin_users ADD COLUMN IF NOT EXISTS can_view_watchlist INTEGER DEFAULT 0',
    # Forces a viewer to set their own password before reaching anything
    # else, instead of continuing to use the auto-generated one that went
    # out in plaintext over email (see create_viewer_account). Left
    # unspecified (NULL) rather than defaulted here on purpose: that lets
    # the one-time backfill below tell "never resolved yet" apart from
    # "already resolved to false" -- every row is 0 or 1 after it runs
    # once, so on every later app start it's a no-op and never re-flags a
    # viewer who's already changed their password. Applies retroactively
    # to every viewer that predates this column too, per instruction --
    # they were already emailed an auto-generated password same as any
    # new viewer would be.
    'ALTER TABLE stocks_admin_users ADD COLUMN IF NOT EXISTS must_change_password INTEGER',
    "UPDATE stocks_admin_users SET must_change_password=1 WHERE role='viewer' AND must_change_password IS NULL",
    'UPDATE stocks_admin_users SET must_change_password=0 WHERE must_change_password IS NULL',
    # Self-serve paid subscribers (see utils/stocks_subscription.py and
    # app.py's /stocks/signup, /stocks/subscribe/verify, /stocks/razorpay/webhook)
    # -- a role='viewer' row same as an admin-created one, just with these
    # extra columns populated. 'none' means "not a paid account" (every
    # viewer created via /stocks/users stays 'none' forever) so the login
    # subscription check in authenticate_stocks_admin only ever applies to
    # rows that actually went through Razorpay.
    "ALTER TABLE stocks_admin_users ADD COLUMN IF NOT EXISTS subscription_status TEXT "
    "DEFAULT 'none' CHECK (subscription_status IN ('none', 'pending', 'active', 'cancelled', 'halted'))",
    'ALTER TABLE stocks_admin_users ADD COLUMN IF NOT EXISTS razorpay_customer_id TEXT',
    'ALTER TABLE stocks_admin_users ADD COLUMN IF NOT EXISTS razorpay_subscription_id TEXT',
    # When the current paid period ends -- for 'active' this is the next
    # auto-renewal date; for 'cancelled' it's the date access actually
    # stops (Razorpay's own cancellation still honours whatever period was
    # already paid for, see mark_subscription_cancelled).
    'ALTER TABLE stocks_admin_users ADD COLUMN IF NOT EXISTS subscription_current_period_end TIMESTAMPTZ',
    # Dedups the expiry-reminder cron (see find_expiring_subscribers) --
    # compared against subscription_current_period_end itself, not a fixed
    # cooldown window, so a renewed subscription's NEXT expiry can still
    # trigger a fresh reminder later.
    'ALTER TABLE stocks_admin_users ADD COLUMN IF NOT EXISTS subscription_reminder_sent_for TIMESTAMPTZ',
    # Free-of-charge full access, independent of subscription_status
    # entirely -- see has_stocks_access in utils/stocks_subscription.py,
    # which short-circuits to True whenever this is set, before it even
    # looks at subscription_status. DEFAULT 1 (not the NULL-sentinel +
    # backfill pattern used elsewhere in this file) deliberately -- Postgres
    # applies a column's DEFAULT to every existing row too, not just future
    # ones, so this single ALTER retroactively marks every account that
    # already existed pro (per instruction -- nobody who already had access
    # should suddenly need to pay) with no separate backfill UPDATE needed,
    # and with no gap where a freshly-seeded super_admin (see
    # _seed_super_admin, whose INSERT doesn't mention this column at all)
    # could land on NULL between this ALTER running and a later backfill.
    # Every self-serve signup path explicitly overrides this default to 0
    # at INSERT time instead (see create_pending_subscriber in
    # utils/stocks_subscription.py and create_pending_google_subscriber
    # below) -- only manually-added/admin accounts stay pro by default.
    'ALTER TABLE stocks_admin_users ADD COLUMN IF NOT EXISTS is_pro INTEGER DEFAULT 1',
    # Sign in/up with Google (see app.py's /stocks/auth/google/login and
    # /stocks/auth/google/callback) -- reuses the same GoogleAuthProvider
    # already wired up for the storefront's own Google login
    # (auth_providers/google.py), just against this table instead of the
    # storefront's users table. UNIQUE so two accounts can never end up
    # claiming the same Google identity.
    'ALTER TABLE stocks_admin_users ADD COLUMN IF NOT EXISTS google_sub TEXT UNIQUE',
    # A Google-only signup never sets a password at all -- relaxed from the
    # original NOT NULL (every row before Google sign-in existed had one:
    # the bootstrap super_admin, admin-created child admins/viewers, and
    # self-serve email/password signups all still set one, this only
    # affects new Google-only rows).
    'ALTER TABLE stocks_admin_users ALTER COLUMN password_hash DROP NOT NULL',
    # Referral system (see utils/stocks_referrals.py) -- every viewer gets
    # their own shareable code, generated lazily (signup, profile view, or
    # first email send) rather than backfilled here, so this column starts
    # NULL for every existing row. referred_by_id is set once at signup if
    # a valid code was supplied and never changes after -- self-referral
    # (a code owner using their own code) is rejected at signup time, not
    # by a constraint here. referral_credits_redeemed tracks how many
    # free-month credits have already been applied at a past cancellation
    # (see apply_referral_credits_on_cancellation) so re-subscribing later
    # never double-spends them -- qualified-referral count and available
    # credits are computed on the fly, not stored.
    'ALTER TABLE stocks_admin_users ADD COLUMN IF NOT EXISTS referral_code TEXT UNIQUE',
    'ALTER TABLE stocks_admin_users ADD COLUMN IF NOT EXISTS referred_by_id BIGINT REFERENCES stocks_admin_users(id)',
    'ALTER TABLE stocks_admin_users ADD COLUMN IF NOT EXISTS referral_credits_redeemed INTEGER DEFAULT 0',
]


def initialize_stocks_auth_if_needed(client):
    """Create stocks_admin_users if needed and seed the one bootstrap
    super_admin from env vars. Call once at app startup, same as
    initialize_stock_tables_if_needed(). Runs against the raw Supabase
    client (no app/request context exists yet at startup, so get_db()'s
    flask.g isn't available here -- same reason app.py's own
    initialize_database_if_needed() uses client.rpc directly instead)."""
    for sql in STOCKS_AUTH_TABLES_SQL + STOCKS_AUTH_ALTER_SQL:
        try:
            client.rpc('execute_sql', {'query': sql}).execute()
        except Exception as e:
            print(f'Stocks auth table init warning (may already exist): {e}')
    _seed_super_admin(client)


def _seed_super_admin(client):
    """One-time bootstrap: if stocks_admin_users is completely empty and
    STOCKS_SUPER_ADMIN_USERNAME/PASSWORD are set, create the first
    super_admin row from them. After that first row exists, this is a
    no-op forever -- further accounts are created through the app, not env
    vars."""
    username = os.environ.get('STOCKS_SUPER_ADMIN_USERNAME', '').strip()
    password = os.environ.get('STOCKS_SUPER_ADMIN_PASSWORD', '')
    if not username or not password:
        return
    try:
        existing = client.rpc('execute_sql', {'query': 'SELECT id FROM stocks_admin_users LIMIT 1'}).execute()
        rows = getattr(existing, 'data', existing)
        if rows:
            return
        password_hash = generate_password_hash(password)
        escaped_username = username.replace("'", "''")
        escaped_hash = password_hash.replace("'", "''")
        client.rpc('execute_sql', {
            'query': (
                "INSERT INTO stocks_admin_users (username, password_hash, role) "
                f"VALUES ('{escaped_username}', '{escaped_hash}', 'super_admin') "
                "ON CONFLICT (username) DO NOTHING"
            )
        }).execute()
        print(f'Stocks: seeded initial super_admin "{username}".')
    except Exception as e:
        print(f'Stocks super_admin seed warning: {e}')


def authenticate_stocks_admin(db, username, password):
    """Returns the stocks_admin_users row on success, else None. db is
    app.py's get_db() -- unlike the startup-time init above, this always
    runs inside a request.

    For a self-serve paid account (subscription_status != 'none', see
    utils/stocks_subscription.py), also requires subscription_is_current --
    a 'pending' row (signed up, checkout never completed) or 'halted' row
    (Razorpay gave up retrying a failed renewal) is refused login even with
    the right password. Every other role/account is untouched by this
    check (subscription_status defaults to 'none', which always passes)."""
    row = db.execute(
        'SELECT id, username, password_hash, role, is_active, can_view_watchlist, must_change_password, '
        'is_pro, subscription_status, subscription_current_period_end '
        'FROM stocks_admin_users WHERE username=?',
        (username,)
    ).fetchone()
    if not row or not row['is_active']:
        return None
    if not row.get('password_hash'):
        return None  # Google-only account -- has no password to check against at all
    if not check_password_hash(row['password_hash'], password):
        return None
    if not has_stocks_access(row.get('is_pro'), row.get('subscription_status'), row.get('subscription_current_period_end')):
        return None
    return row


def _must_change_password_redirect():
    """Returns a redirect to /stocks/change-password if the current
    session is still flagged must_change_password (see create_viewer_account
    and the one-time migration backfill in STOCKS_AUTH_ALTER_SQL), else
    None. Checked inside every access decorator below rather than only
    right after login, so a viewer can't dodge the forced change by
    hitting some other Stocks URL directly (a bookmark, or typing it in)
    instead of following the post-login redirect. The endpoint check
    avoids redirecting the change-password page to itself."""
    if session.get('stocks_must_change_password') and request.endpoint != 'stocks_change_password':
        return redirect(url_for('stocks_change_password'))
    return None


def safe_stocks_next_url(url):
    """Validates a ?next= redirect target before stocks_admin_login uses
    it -- must be a same-site /stocks/... path, never an absolute URL or a
    protocol-relative //host one (both would be an open-redirect hole:
    login, then get bounced off-site). Returns the url unchanged if safe,
    else None."""
    if not url or not url.startswith('/stocks/') or url.startswith('//'):
        return None
    return url


def stocks_login_required(view_func):
    """Any active stocks_admin_users account (super_admin or child_admin).
    Carries the originally-requested path through as ?next= so
    stocks_admin_login can send the visitor straight there after signing
    in, instead of always dropping them on the generic default page --
    matters most for a link clicked from an email (e.g. a suggestion's
    "View full analysis" link to /stocks/universe/<id>) while logged out,
    which should land on that same stock page, not somewhere generic."""
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not session.get('stocks_admin_id'):
            return redirect(url_for('stocks_admin_login', next=request.path))
        forced = _must_change_password_redirect()
        if forced:
            return forced
        return view_func(*args, **kwargs)
    return wrapped


def stocks_role_required(*roles):
    """Restricts a route to one or more specific roles, e.g.
    @stocks_role_required('super_admin') (unchanged usage/behavior from
    before) or @stocks_role_required('super_admin', 'child_admin') for a
    staff-only page that viewer accounts shouldn't reach."""
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if not session.get('stocks_admin_id'):
                return redirect(url_for('stocks_admin_login'))
            forced = _must_change_password_redirect()
            if forced:
                return forced
            if session.get('stocks_admin_role') not in roles:
                flash('You do not have access to that page.', 'error')
                return ('Forbidden', 403)
            return view_func(*args, **kwargs)
        return wrapped
    return decorator


def stocks_watchlist_access_required(view_func):
    """Same access as stocks_role_required('super_admin', 'child_admin'),
    plus any viewer whose can_view_watchlist flag was granted when their
    account was created (see create_viewer_account) -- the flag is cached
    into session['stocks_can_view_watchlist'] at login time
    (stocks_admin_login in app.py), same staleness tradeoff
    session['stocks_admin_role'] already accepts everywhere else in this
    module: toggling the flag after a viewer's already logged in takes
    effect on their next login, not immediately."""
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not session.get('stocks_admin_id'):
            return redirect(url_for('stocks_admin_login'))
        forced = _must_change_password_redirect()
        if forced:
            return forced
        role = session.get('stocks_admin_role')
        if role in ('super_admin', 'child_admin'):
            return view_func(*args, **kwargs)
        if role == 'viewer' and session.get('stocks_can_view_watchlist'):
            return view_func(*args, **kwargs)
        flash('You do not have access to that page.', 'error')
        return ('Forbidden', 403)
    return wrapped


def list_stocks_admin_users(db):
    return db.execute(
        'SELECT id, username, role, is_active, created_at FROM stocks_admin_users ORDER BY created_at DESC'
    ).fetchall()


def create_child_admin(db, username, password, created_by_id):
    """Creates a new child_admin account. Returns (row, error_message) --
    error_message is None on success. Checks for an existing username first
    because SupabaseCursor swallows SQL errors (e.g. the UNIQUE constraint)
    instead of raising, so a failed INSERT can't be detected any other way."""
    username = (username or '').strip()
    if not username:
        return None, 'Username is required.'
    if not password:
        return None, 'Password is required.'

    existing = db.execute(
        'SELECT id FROM stocks_admin_users WHERE username=?', (username,)
    ).fetchone()
    if existing:
        return None, 'That username is already taken.'

    password_hash = generate_password_hash(password)
    db.execute(
        '''INSERT INTO stocks_admin_users (username, password_hash, role, created_by)
           VALUES (?, ?, 'child_admin', ?)''',
        (username, password_hash, created_by_id)
    )
    db.commit()

    row = db.execute(
        'SELECT id, username, role, is_active, created_at FROM stocks_admin_users WHERE username=?',
        (username,)
    ).fetchone()
    return row, None


def toggle_child_admin_active(db, admin_id):
    """Flips is_active for a child_admin row only -- never touches
    super_admin rows, since there's exactly one and no recovery path if it
    gets locked out. Returns True if a row was toggled, False if not found
    or not a child_admin."""
    row = db.execute(
        'SELECT id, role, is_active FROM stocks_admin_users WHERE id=?', (admin_id,)
    ).fetchone()
    if not row or row['role'] != 'child_admin':
        return False
    new_status = 0 if row['is_active'] else 1
    db.execute(
        'UPDATE stocks_admin_users SET is_active=?, updated_at=NOW() WHERE id=?',
        (new_status, admin_id)
    )
    db.commit()
    return True


def list_viewers(db):
    return db.execute(
        "SELECT id, username, name, is_active, can_view_watchlist, must_change_password, is_pro, "
        "subscription_status, subscription_current_period_end, created_at "
        "FROM stocks_admin_users WHERE role='viewer' ORDER BY created_at DESC"
    ).fetchall()


def create_viewer_account(db, email, name, created_by_id, can_view_watchlist=False):
    """Creates a new role='viewer' account -- email stored in the username
    column (stocks_admin_users has no separate email field, and username is
    already the login field for every role). Generates a real, random
    password via _generate_simple_password() -- letters/digits only, no
    ambiguous 0/O/1/l/I characters, easy to read aloud or type by hand
    (there's still no password-reset/change flow anywhere in this
    codebase, so this is the only way the account gets one at all) -- the
    caller is expected to email it to the new viewer immediately
    (see app.py's /stocks/users POST handler and
    utils/suggestion_email.send_viewer_welcome_email); it's returned here,
    in plaintext, only so that send can happen -- nothing else stores or
    logs the plaintext, only its hash persists in the database.

    can_view_watchlist grants this specific viewer access to the (staff)
    /stocks/watchlist page -- off by default; see
    stocks_watchlist_access_required. Most viewers should stay read-only
    to their own suggestions, so this is opt-in per account, not global.

    Always created with must_change_password=1 -- a freshly-generated
    password went out over email in plaintext, so the account must be
    forced through /stocks/change-password on first login rather than
    trusting that password to stay theirs alone indefinitely (see
    stocks_watchlist_access_required and the other access decorators,
    which all enforce this).

    Returns (row, plaintext_password, error_message) -- password is None
    on error."""
    email = (email or '').strip()
    if not email:
        return None, None, 'Email is required.'

    existing = db.execute('SELECT id FROM stocks_admin_users WHERE username=?', (email,)).fetchone()
    if existing:
        return None, None, 'That email is already registered.'

    password = _generate_simple_password()
    password_hash = generate_password_hash(password)
    db.execute(
        '''INSERT INTO stocks_admin_users
               (username, password_hash, role, name, created_by, can_view_watchlist, must_change_password, is_pro)
           VALUES (?, ?, 'viewer', ?, ?, ?, 1, 1)''',
        (email, password_hash, (name or '').strip() or None, created_by_id, 1 if can_view_watchlist else 0)
    )
    db.commit()

    row = db.execute(
        'SELECT id, username, name, role, is_active, can_view_watchlist, must_change_password, created_at '
        'FROM stocks_admin_users WHERE username=?',
        (email,)
    ).fetchone()
    return row, password, None


MIN_PASSWORD_LENGTH = 8


def change_own_password(db, admin_id, new_password):
    """Sets a new password for any stocks_admin_users account (any role)
    and clears must_change_password. Used by /stocks/change-password --
    the active session is what already proves the caller is this account,
    so there's no separate 'current password' re-entry here; any logged-in
    account can reach this to change their own password, not just a
    viewer working through a forced first-login change. Returns
    (ok, error_message)."""
    if not new_password or len(new_password) < MIN_PASSWORD_LENGTH:
        return False, f'Password must be at least {MIN_PASSWORD_LENGTH} characters.'
    password_hash = generate_password_hash(new_password)
    db.execute(
        'UPDATE stocks_admin_users SET password_hash=?, must_change_password=0, updated_at=NOW() WHERE id=?',
        (password_hash, admin_id)
    )
    db.commit()
    return True, None


def toggle_viewer_active(db, admin_id):
    """Flips is_active for a viewer row only -- mirrors
    toggle_child_admin_active's safety pattern (never touches a row outside
    the role it's meant for)."""
    row = db.execute(
        'SELECT id, role, is_active FROM stocks_admin_users WHERE id=?', (admin_id,)
    ).fetchone()
    if not row or row['role'] != 'viewer':
        return False
    new_status = 0 if row['is_active'] else 1
    db.execute(
        'UPDATE stocks_admin_users SET is_active=?, updated_at=NOW() WHERE id=?',
        (new_status, admin_id)
    )
    db.commit()
    return True


def toggle_viewer_pro(db, admin_id):
    """Flips is_pro for a viewer row only -- lets a super_admin/child_admin
    comp a self-serve signup full access without going through Razorpay at
    all (see has_stocks_access in utils/stocks_subscription.py), or revoke
    that comp later. Same role-safety pattern as toggle_viewer_active:
    never touches a row that isn't actually role='viewer'."""
    row = db.execute(
        'SELECT id, role, is_pro FROM stocks_admin_users WHERE id=?', (admin_id,)
    ).fetchone()
    if not row or row['role'] != 'viewer':
        return False
    new_status = 0 if row['is_pro'] else 1
    db.execute(
        'UPDATE stocks_admin_users SET is_pro=?, updated_at=NOW() WHERE id=?',
        (new_status, admin_id)
    )
    db.commit()
    return True


def find_stocks_account_by_google_sub(db, google_sub):
    return db.execute(
        'SELECT id, username, name, role, is_active, can_view_watchlist, must_change_password, '
        'is_pro, subscription_status, subscription_current_period_end, razorpay_subscription_id, referred_by_id '
        'FROM stocks_admin_users WHERE google_sub=?',
        (google_sub,)
    ).fetchone()


def find_stocks_account_by_username(db, username):
    return db.execute(
        'SELECT id, username, name, role, is_active, can_view_watchlist, must_change_password, '
        'is_pro, subscription_status, subscription_current_period_end, razorpay_subscription_id, google_sub, referred_by_id '
        'FROM stocks_admin_users WHERE username=?',
        (username,)
    ).fetchone()


def link_google_sub(db, admin_id, google_sub):
    """Backfills google_sub onto an account that already existed under this
    email (an admin-created viewer, or one that originally signed up with a
    password) the first time they use 'Sign in with Google' -- so the same
    person doesn't end up with two separate accounts under one email."""
    db.execute(
        'UPDATE stocks_admin_users SET google_sub=?, updated_at=NOW() WHERE id=?',
        (google_sub, admin_id)
    )
    db.commit()


def create_pending_google_subscriber(db, email, name, google_sub, referred_by_id=None):
    """Brand-new self-serve signup via 'Sign up with Google' -- no password
    at all (password_hash stays NULL; see the relaxed NOT NULL constraint
    in STOCKS_AUTH_ALTER_SQL and authenticate_stocks_admin's check for a
    missing password_hash). Same pending/unpaid shape as
    utils.stocks_subscription.create_pending_subscriber's email/password
    version: is_active=0, subscription_status='pending', is_pro=0 -- the
    caller (app.py's Google callback) still has to send this account
    through Razorpay checkout before it can log in. referred_by_id (see
    utils/stocks_referrals.py), when given, is stamped once here and never
    changes after -- app.py's Google callback resolves the code (carried
    across the OAuth round-trip via session, since form fields don't
    survive it) into this id before calling in. Returns the new row."""
    email = (email or '').strip().lower()
    db.execute(
        '''INSERT INTO stocks_admin_users
               (username, password_hash, role, name, is_active, must_change_password,
                subscription_status, is_pro, google_sub, referred_by_id)
           VALUES (?, NULL, 'viewer', ?, 0, 0, 'pending', 0, ?, ?)''',
        (email, (name or '').strip() or None, google_sub, referred_by_id)
    )
    db.commit()
    return find_stocks_account_by_google_sub(db, google_sub)


def delete_viewer_account(db, admin_id):
    """Permanently deletes a viewer row -- unlike toggle_viewer_active
    (reversible), this is not. Mirrors the same role safety pattern as
    toggle_viewer_active/toggle_child_admin_active: only ever touches a
    row that's actually role='viewer', regardless of what id is passed in,
    so this can never be used to delete a super_admin or child_admin by
    mistake (e.g. a wrong/tampered id). Returns True if a row was deleted,
    False if not found or not a viewer."""
    row = db.execute(
        'SELECT id, role FROM stocks_admin_users WHERE id=?', (admin_id,)
    ).fetchone()
    if not row or row['role'] != 'viewer':
        return False
    db.execute('DELETE FROM stocks_admin_users WHERE id=? AND role=?', (admin_id, 'viewer'))
    db.commit()
    return True


def migrate_email_recipients_to_viewers(db, created_by_id):
    """One-time (safely re-runnable) migration: for each stocks_email_recipients
    row, creates a matching role='viewer' account if one doesn't already
    exist (by email/username). Does not touch or delete
    stocks_email_recipients -- kept for rollback. Returns each newly-created
    account's (email, name, password) too, alongside the plain migrated list
    of emails, so the caller (app.py) can send each of them the same
    welcome/credentials email a manually-created viewer gets -- otherwise
    migrated accounts would be stuck unable to log in while new ones work."""
    recipients = db.execute('SELECT email, name FROM stocks_email_recipients').fetchall()

    migrated = []
    created_accounts = []
    skipped = []
    for r in recipients:
        row, password, error = create_viewer_account(db, r['email'], r.get('name'), created_by_id)
        if row:
            migrated.append(r['email'])
            created_accounts.append({'email': r['email'], 'name': r.get('name'), 'password': password})
        else:
            skipped.append({'email': r['email'], 'reason': error})

    return {'migrated': migrated, 'created_accounts': created_accounts, 'skipped': skipped}
