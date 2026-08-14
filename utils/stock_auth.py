import os
from functools import wraps

from flask import flash, redirect, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

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


def initialize_stocks_auth_if_needed(client):
    """Create stocks_admin_users if needed and seed the one bootstrap
    super_admin from env vars. Call once at app startup, same as
    initialize_stock_tables_if_needed(). Runs against the raw Supabase
    client (no app/request context exists yet at startup, so get_db()'s
    flask.g isn't available here -- same reason app.py's own
    initialize_database_if_needed() uses client.rpc directly instead)."""
    for sql in STOCKS_AUTH_TABLES_SQL:
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


def stocks_role_required(role):
    """Restricts a route to one specific role, e.g. @stocks_role_required('super_admin')."""
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if not session.get('stocks_admin_id'):
                return redirect(url_for('stocks_admin_login'))
            if session.get('stocks_admin_role') != role:
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
