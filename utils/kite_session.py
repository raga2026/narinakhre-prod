import os
from datetime import datetime, timedelta, timezone

from kiteconnect import KiteConnect

from utils.credential_crypto import encrypt_credentials, decrypt_credentials

IST = timezone(timedelta(hours=5, minutes=30))

# Kite Connect access tokens expire daily -- rather than a static env var, a
# super_admin refreshes this by logging into Kite through the browser (see
# /admin/stocks/kite/login and /admin/stocks/kite/callback in app.py). The
# resulting access_token is encrypted with the same Fernet helper used for
# Shiprocket/Delhivery credentials (utils/credential_crypto.py) and stored
# here -- never in an env var or in plaintext.
STOCKS_KITE_SESSION_TABLE_SQL = [
    '''CREATE TABLE IF NOT EXISTS stocks_kite_session (
        id BIGSERIAL PRIMARY KEY,
        access_token_encrypted TEXT,
        generated_at TIMESTAMPTZ,
        expires_at TIMESTAMPTZ,
        updated_by BIGINT REFERENCES stocks_admin_users(id)
    )'''
]

# Kept separate from the CREATE TABLE above so ADD COLUMN IF NOT EXISTS can
# run against a table created by an earlier version of this file (which had
# access_token/refreshed_by/refreshed_at instead) -- same additive-migration
# pattern app.py's own schema uses. The old columns are left in place unused
# rather than dropped.
STOCKS_KITE_SESSION_ALTER_SQL = [
    'ALTER TABLE stocks_kite_session ADD COLUMN IF NOT EXISTS access_token_encrypted TEXT',
    'ALTER TABLE stocks_kite_session ADD COLUMN IF NOT EXISTS generated_at TIMESTAMPTZ',
    'ALTER TABLE stocks_kite_session ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ',
    'ALTER TABLE stocks_kite_session ADD COLUMN IF NOT EXISTS updated_by BIGINT REFERENCES stocks_admin_users(id)',
]


def initialize_kite_session_table_if_needed(client):
    """Call after initialize_stocks_auth_if_needed() -- this table's FK
    depends on stocks_admin_users already existing."""
    for sql in STOCKS_KITE_SESSION_TABLE_SQL + STOCKS_KITE_SESSION_ALTER_SQL:
        try:
            client.rpc('execute_sql', {'query': sql}).execute()
        except Exception as e:
            print(f'Kite session table init warning (may already exist): {e}')


def _get_api_credentials():
    api_key = os.environ.get('STOCKS_KITE_API_KEY', '').strip()
    api_secret = os.environ.get('STOCKS_KITE_API_SECRET', '').strip()
    if not api_key or not api_secret:
        raise RuntimeError('STOCKS_KITE_API_KEY and STOCKS_KITE_API_SECRET must both be set.')
    return api_key, api_secret


def get_kite_login_url():
    api_key, _ = _get_api_credentials()
    return KiteConnect(api_key=api_key).login_url()


def exchange_request_token(request_token):
    """Exchanges a Kite login request_token (from the callback's query
    string) for an access_token. Raises on failure -- the callback route
    decides how to surface that to the user."""
    api_key, api_secret = _get_api_credentials()
    kite = KiteConnect(api_key=api_key)
    session_data = kite.generate_session(request_token, api_secret=api_secret)
    return session_data['access_token']


def _next_day_6am_ist(generated_at_utc):
    """Kite access tokens are valid until ~6 AM IST the day after they were
    issued, regardless of what time during the day login happened."""
    generated_ist = generated_at_utc.astimezone(IST)
    next_day = generated_ist.date() + timedelta(days=1)
    expiry_ist = datetime(next_day.year, next_day.month, next_day.day, 6, 0, tzinfo=IST)
    return expiry_ist.astimezone(timezone.utc)


def save_kite_access_token(db, access_token, updated_by_id):
    """Encrypts access_token with the shared Fernet helper (reused, not a
    new encryption utility -- see credential_crypto.py) and keeps exactly
    one row, since there's only ever one active Kite session. Returns the
    computed expires_at."""
    generated_at = datetime.now(timezone.utc)
    expires_at = _next_day_6am_ist(generated_at)
    encrypted = encrypt_credentials({'access_token': access_token})

    existing = db.execute('SELECT id FROM stocks_kite_session LIMIT 1').fetchone()
    if existing:
        db.execute(
            '''UPDATE stocks_kite_session
               SET access_token_encrypted=?, generated_at=?, expires_at=?, updated_by=?
               WHERE id=?''',
            (encrypted, generated_at.isoformat(), expires_at.isoformat(), updated_by_id, existing['id'])
        )
    else:
        db.execute(
            '''INSERT INTO stocks_kite_session
                   (access_token_encrypted, generated_at, expires_at, updated_by)
               VALUES (?, ?, ?, ?)''',
            (encrypted, generated_at.isoformat(), expires_at.isoformat(), updated_by_id)
        )
    db.commit()
    return expires_at


def get_kite_access_token(db):
    """Returns the decrypted access token, or None if no session has been
    stored yet. Doesn't check expires_at -- an expired token just gets
    rejected by Kite itself with a clear auth error; the dashboard's expiry
    display is what actually prompts a super_admin to reconnect."""
    row = db.execute(
        'SELECT access_token_encrypted FROM stocks_kite_session ORDER BY id DESC LIMIT 1'
    ).fetchone()
    if not row or not row['access_token_encrypted']:
        return None
    return decrypt_credentials(row['access_token_encrypted']).get('access_token')


def get_kite_session_status(db):
    """Row with access_token_encrypted/generated_at/expires_at/updated_by,
    or None if no one has ever logged in -- used to show connection status
    on the dashboard. Doesn't decrypt -- callers only need to know whether a
    token exists and when it expires, not its value."""
    return db.execute(
        'SELECT access_token_encrypted, generated_at, expires_at, updated_by '
        'FROM stocks_kite_session ORDER BY id DESC LIMIT 1'
    ).fetchone()
