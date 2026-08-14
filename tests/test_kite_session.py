import os
from unittest.mock import patch

from cryptography.fernet import Fernet

os.environ.setdefault('CREDENTIAL_ENCRYPTION_KEY', Fernet.generate_key().decode())

from utils.kite_client import KiteClient
from utils.kite_session import exchange_request_token, get_kite_access_token, save_kite_access_token


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeKiteSessionDB:
    """Minimal stand-in for app.py's SupabaseDB, just enough to run the
    exact SQL kite_session.py issues against stocks_kite_session."""

    def __init__(self):
        self.row = None

    def execute(self, sql, params=None):
        params = params or ()
        normalized = ' '.join(sql.split())

        if normalized.startswith('SELECT id FROM stocks_kite_session'):
            return FakeCursor([{'id': 1}] if self.row else [])

        if normalized.startswith('INSERT INTO stocks_kite_session'):
            encrypted, generated_at, expires_at, updated_by = params
            self.row = {
                'id': 1,
                'access_token_encrypted': encrypted,
                'generated_at': generated_at,
                'expires_at': expires_at,
                'updated_by': updated_by,
            }
            return FakeCursor([])

        if normalized.startswith('UPDATE stocks_kite_session'):
            encrypted, generated_at, expires_at, updated_by, row_id = params
            self.row.update({
                'access_token_encrypted': encrypted,
                'generated_at': generated_at,
                'expires_at': expires_at,
                'updated_by': updated_by,
            })
            return FakeCursor([])

        if normalized.startswith('SELECT access_token_encrypted FROM stocks_kite_session'):
            return FakeCursor([self.row] if self.row else [])

        raise AssertionError(f'Unexpected SQL in test: {sql}')

    def commit(self):
        pass


def test_callback_flow_stores_encrypted_token_and_kite_client_can_use_it():
    db = FakeKiteSessionDB()

    with patch('utils.kite_session.KiteConnect') as MockKiteConnect:
        MockKiteConnect.return_value.generate_session.return_value = {
            'access_token': 'real-secret-token-123',
        }
        access_token = exchange_request_token('some-request-token')
        assert access_token == 'real-secret-token-123'

        save_kite_access_token(db, access_token, updated_by_id=1)

    # Stored value must not be (or contain) the plaintext token.
    assert db.row['access_token_encrypted'] != 'real-secret-token-123'
    assert 'real-secret-token-123' not in db.row['access_token_encrypted']

    # get_kite_access_token decrypts it back to the original value.
    assert get_kite_access_token(db) == 'real-secret-token-123'

    # KiteClient builds a working client from the decrypted token without
    # needing an access_token passed in directly, and without hitting the
    # real Kite API.
    with patch('utils.kite_client.KiteConnect') as MockKiteConnectForClient:
        mock_instance = MockKiteConnectForClient.return_value
        KiteClient(db=db)
        mock_instance.set_access_token.assert_called_once_with('real-secret-token-123')


def test_save_kite_access_token_updates_existing_row_instead_of_duplicating():
    db = FakeKiteSessionDB()

    save_kite_access_token(db, 'first-token', updated_by_id=1)
    assert get_kite_access_token(db) == 'first-token'

    save_kite_access_token(db, 'second-token', updated_by_id=2)
    assert get_kite_access_token(db) == 'second-token'
    assert db.row['updated_by'] == 2
