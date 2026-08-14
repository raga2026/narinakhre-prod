import hashlib
import hmac
import json
import os

# Raw postback capture only -- verifies Kite's signature and logs the
# payload so it's not lost. Nothing here updates order/suggestion state yet;
# that depends on execute_suggestion(), which is a later phase.
STOCKS_KITE_POSTBACK_LOG_TABLE_SQL = [
    '''CREATE TABLE IF NOT EXISTS stocks_kite_postback_log (
        id BIGSERIAL PRIMARY KEY,
        payload JSONB,
        received_at TIMESTAMPTZ DEFAULT NOW()
    )'''
]


def initialize_kite_postback_log_table_if_needed(client):
    for sql in STOCKS_KITE_POSTBACK_LOG_TABLE_SQL:
        try:
            client.rpc('execute_sql', {'query': sql}).execute()
        except Exception as e:
            print(f'Kite postback log table init warning (may already exist): {e}')


def verify_postback_checksum(payload):
    """Kite's postback checksum is sha256(order_id + order_timestamp +
    api_secret) -- see Kite Connect's postback docs. Returns False (reject)
    if the secret isn't configured, the payload has no checksum, or it
    doesn't match."""
    api_secret = os.environ.get('STOCKS_KITE_API_SECRET', '').strip()
    if not api_secret:
        return False

    received_checksum = str(payload.get('checksum', ''))
    if not received_checksum:
        return False

    order_id = str(payload.get('order_id', ''))
    order_timestamp = str(payload.get('order_timestamp', ''))
    expected = hashlib.sha256((order_id + order_timestamp + api_secret).encode('utf-8')).hexdigest()
    return hmac.compare_digest(expected, received_checksum)


def log_postback(db, payload):
    db.execute(
        'INSERT INTO stocks_kite_postback_log (payload) VALUES (?)',
        (json.dumps(payload),)
    )
    db.commit()
