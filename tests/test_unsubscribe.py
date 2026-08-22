"""Tests for the one-click unsubscribe link -- utils/stock_auth.py's
build_unsubscribe_url (used by send_zeptomail_stocks_email) and
verify_and_apply_unsubscribe (used by app.py's /stocks/unsubscribe)."""
import os
from unittest.mock import patch

from stoqbell.utils.stock_auth import build_unsubscribe_url, verify_and_apply_unsubscribe


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeUnsubDB:
    def __init__(self, rows=None):
        self.rows = rows or []

    def execute(self, sql, params=None):
        params = params or ()
        normalized = ' '.join(sql.split())
        if normalized.startswith('UPDATE stocks_admin_users SET email_unsubscribed_at=NOW()'):
            username, = params
            for r in self.rows:
                if r['username'] == username:
                    r['email_unsubscribed_at'] = 'now'
            return FakeCursor([])
        raise AssertionError(f'Unexpected SQL in test: {sql}')

    def commit(self):
        pass


def test_build_and_verify_round_trip_applies_unsubscribe():
    with patch.dict(os.environ, {'FLASK_SECRET_KEY': 'test-secret'}, clear=False):
        url = build_unsubscribe_url('a@example.com')
        assert '/stocks/unsubscribe?email=a%40example.com&token=' in url
        token = url.split('token=')[1]

        db = FakeUnsubDB(rows=[{'username': 'a@example.com', 'email_unsubscribed_at': None}])
        applied = verify_and_apply_unsubscribe(db, 'a@example.com', token)

    assert applied is True
    assert db.rows[0]['email_unsubscribed_at'] == 'now'


def test_verify_rejects_tampered_token():
    with patch.dict(os.environ, {'FLASK_SECRET_KEY': 'test-secret'}, clear=False):
        url = build_unsubscribe_url('a@example.com')
        token = url.split('token=')[1]

        db = FakeUnsubDB(rows=[{'username': 'a@example.com', 'email_unsubscribed_at': None}])
        applied = verify_and_apply_unsubscribe(db, 'someone-else@example.com', token)

    assert applied is False
    assert db.rows[0]['email_unsubscribed_at'] is None


def test_verify_rejects_missing_token():
    db = FakeUnsubDB(rows=[{'username': 'a@example.com', 'email_unsubscribed_at': None}])
    assert verify_and_apply_unsubscribe(db, 'a@example.com', '') is False
    assert verify_and_apply_unsubscribe(db, '', 'sometoken') is False
