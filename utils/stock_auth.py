import hmac
import os
import secrets
from functools import wraps

from flask import flash, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash


def has_valid_cron_secret(request_headers, secret):
    """Checks the X-Cron-Secret header against secret using constant-time
    comparison -- the exact same check /cron/stocks-fundamentals-sync does
    inline in app.py, extracted here only so /admin/stocks/sync (and its
    test) can reuse it without duplicating the hmac call by hand. Returns
    False if secret is empty/unset, same as the inline version."""
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
    runs inside a request."""
    row = db.execute(
        'SELECT id, username, password_hash, role, is_active FROM stocks_admin_users WHERE username=?',
        (username,)
    ).fetchone()
    if not row or not row['is_active']:
        return None
    if not check_password_hash(row['password_hash'], password):
        return None
    return row


def stocks_login_required(view_func):
    """Any active stocks_admin_users account (super_admin or child_admin)."""
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not session.get('stocks_admin_id'):
            return redirect(url_for('stocks_admin_login'))
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
            if session.get('stocks_admin_role') not in roles:
                flash('You do not have access to that page.', 'error')
                return ('Forbidden', 403)
            return view_func(*args, **kwargs)
        return wrapped
    return decorator


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
        "SELECT id, username, name, is_active, created_at FROM stocks_admin_users WHERE role='viewer' ORDER BY created_at DESC"
    ).fetchall()


def create_viewer_account(db, email, name, created_by_id):
    """Creates a new role='viewer' account -- email stored in the username
    column (stocks_admin_users has no separate email field, and username is
    already the login field for every role). Generates a real, random,
    usable password (there's still no password-reset/change flow anywhere
    in this codebase, so this is the only way the account gets one at all)
    -- the caller is expected to email it to the new viewer immediately
    (see app.py's /stocks/users POST handler and
    utils/suggestion_email.send_viewer_welcome_email); it's returned here,
    in plaintext, only so that send can happen -- nothing else stores or
    logs the plaintext, only its hash persists in the database.

    Returns (row, plaintext_password, error_message) -- password is None
    on error."""
    email = (email or '').strip()
    if not email:
        return None, None, 'Email is required.'

    existing = db.execute('SELECT id FROM stocks_admin_users WHERE username=?', (email,)).fetchone()
    if existing:
        return None, None, 'That email is already registered.'

    password = secrets.token_urlsafe(12)
    password_hash = generate_password_hash(password)
    db.execute(
        '''INSERT INTO stocks_admin_users (username, password_hash, role, name, created_by)
           VALUES (?, ?, 'viewer', ?, ?)''',
        (email, password_hash, (name or '').strip() or None, created_by_id)
    )
    db.commit()

    row = db.execute(
        'SELECT id, username, name, role, is_active, created_at FROM stocks_admin_users WHERE username=?',
        (email,)
    ).fetchone()
    return row, password, None


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
