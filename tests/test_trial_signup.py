"""DB-orchestration tests for the 7-day free trial's auth-side pieces --
authenticate_stocks_admin's new (row, reason) return shape and
create_pending_google_subscriber's Standard-trial/Starters-pending branch.
Same FakeCursor/FakeDB pattern as tests/test_stock_auth_viewers.py and
tests/test_stocks_subscription_db.py, matching normalized SQL text
prefixes rather than hitting a real database."""
from werkzeug.security import generate_password_hash

from stoqbell.utils.stock_auth import authenticate_stocks_admin, create_pending_google_subscriber


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeAuthDB:
    def __init__(self, rows=None):
        self.rows = rows or []
        self._next_id = 1

    def execute(self, sql, params=None):
        params = params or ()
        normalized = ' '.join(sql.split())

        if normalized.startswith(
            'SELECT id, username, password_hash, role, name, is_active, can_view_watchlist, must_change_password, '
            "is_pro, subscription_status, subscription_current_period_end, trial_ends_at, stocks_plan FROM stocks_admin_users WHERE username=?"
        ):
            username, = params
            matches = [r for r in self.rows if r['username'] == username]
            return FakeCursor(matches[:1])

        if normalized.startswith(
            "INSERT INTO stocks_admin_users (username, password_hash, role, name, is_active, must_change_password, "
            "subscription_status, trial_ends_at, is_pro, google_sub, referred_by_id, stocks_plan)"
        ):
            email, name, google_sub, referred_by_id = params
            self.rows.append({
                'id': self._next_id, 'username': email, 'name': name, 'role': 'viewer',
                'is_active': 1, 'can_view_watchlist': 0, 'must_change_password': 0, 'is_pro': 0,
                'subscription_status': 'trialing', 'subscription_current_period_end': None,
                'trial_ends_at': 'fake-trial-end', 'razorpay_subscription_id': None,
                'google_sub': google_sub, 'referred_by_id': referred_by_id, 'stocks_plan': 'pro',
            })
            self._next_id += 1
            return FakeCursor([])

        if normalized.startswith(
            "INSERT INTO stocks_admin_users (username, password_hash, role, name, is_active, must_change_password, "
            "subscription_status, is_pro, google_sub, referred_by_id, stocks_plan)"
        ):
            email, name, google_sub, referred_by_id = params
            self.rows.append({
                'id': self._next_id, 'username': email, 'name': name, 'role': 'viewer',
                'is_active': 1, 'can_view_watchlist': 0, 'must_change_password': 0, 'is_pro': 0,
                'subscription_status': 'none', 'subscription_current_period_end': None,
                'trial_ends_at': None, 'razorpay_subscription_id': None,
                'google_sub': google_sub, 'referred_by_id': referred_by_id, 'stocks_plan': 'regular',
            })
            self._next_id += 1
            return FakeCursor([])

        if normalized.startswith(
            'SELECT id, username, name, role, is_active, can_view_watchlist, must_change_password, '
            'is_pro, subscription_status, subscription_current_period_end, trial_ends_at, razorpay_subscription_id, '
            'referred_by_id, stocks_plan FROM stocks_admin_users WHERE google_sub=?'
        ):
            google_sub, = params
            matches = [r for r in self.rows if r.get('google_sub') == google_sub]
            return FakeCursor(matches[:1])

        raise AssertionError(f'Unexpected SQL in test: {sql}')

    def commit(self):
        pass


# --- authenticate_stocks_admin ------------------------------------------------

def test_authenticate_correct_credentials_returns_row_and_no_reason():
    db = FakeAuthDB(rows=[{
        'id': 1, 'username': 'a@example.com', 'password_hash': generate_password_hash('secret123'),
        'role': 'viewer', 'name': 'A', 'is_active': 1, 'can_view_watchlist': 0, 'must_change_password': 0,
        'is_pro': 0, 'subscription_status': 'active', 'subscription_current_period_end': None,
        'trial_ends_at': None, 'stocks_plan': 'regular',
    }])
    row, reason = authenticate_stocks_admin(db, 'a@example.com', 'secret123')
    assert reason is None
    assert row['id'] == 1


def test_authenticate_wrong_password_returns_invalid():
    db = FakeAuthDB(rows=[{
        'id': 1, 'username': 'a@example.com', 'password_hash': generate_password_hash('secret123'),
        'role': 'viewer', 'name': 'A', 'is_active': 1, 'can_view_watchlist': 0, 'must_change_password': 0,
        'is_pro': 0, 'subscription_status': 'none', 'subscription_current_period_end': None,
        'trial_ends_at': None, 'stocks_plan': 'regular',
    }])
    row, reason = authenticate_stocks_admin(db, 'a@example.com', 'wrong')
    assert row is None
    assert reason == 'invalid'


def test_authenticate_expired_trial_returns_trial_expired_not_invalid():
    from datetime import datetime, timedelta, timezone
    past = datetime.now(timezone.utc) - timedelta(days=1)
    db = FakeAuthDB(rows=[{
        'id': 1, 'username': 'a@example.com', 'password_hash': generate_password_hash('secret123'),
        'role': 'viewer', 'name': 'A', 'is_active': 1, 'can_view_watchlist': 0, 'must_change_password': 0,
        'is_pro': 0, 'subscription_status': 'trialing', 'subscription_current_period_end': None,
        'trial_ends_at': past, 'stocks_plan': 'pro',
    }])
    row, reason = authenticate_stocks_admin(db, 'a@example.com', 'secret123')
    assert row is None
    assert reason == 'trial_expired'


def test_authenticate_cancelled_lapsed_paid_account_still_returns_invalid():
    # Scoped deliberately: only 'trialing' gets the special reason -- a
    # lapsed PAID subscription keeps the original generic message.
    from datetime import datetime, timedelta, timezone
    past = datetime.now(timezone.utc) - timedelta(days=1)
    db = FakeAuthDB(rows=[{
        'id': 1, 'username': 'a@example.com', 'password_hash': generate_password_hash('secret123'),
        'role': 'viewer', 'name': 'A', 'is_active': 1, 'can_view_watchlist': 0, 'must_change_password': 0,
        'is_pro': 0, 'subscription_status': 'cancelled', 'subscription_current_period_end': past,
        'trial_ends_at': None, 'stocks_plan': 'regular',
    }])
    row, reason = authenticate_stocks_admin(db, 'a@example.com', 'secret123')
    assert row is None
    assert reason == 'invalid'


# --- create_pending_google_subscriber -----------------------------------------

def test_google_signup_regular_is_free_and_active_immediately():
    db = FakeAuthDB()
    row = create_pending_google_subscriber(db, 'a@example.com', 'A', 'sub_123')  # default 'regular'
    assert row['stocks_plan'] == 'regular'
    assert row['subscription_status'] == 'none'
    assert row['is_active'] == 1
    assert row['trial_ends_at'] is None


def test_google_signup_pro_gets_a_seven_day_trial():
    db = FakeAuthDB()
    row = create_pending_google_subscriber(db, 'a@example.com', 'A', 'sub_123', stocks_plan='pro')
    assert row['stocks_plan'] == 'pro'
    assert row['subscription_status'] == 'trialing'
    assert row['is_active'] == 1
    assert row['trial_ends_at'] is not None
