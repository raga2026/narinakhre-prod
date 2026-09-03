"""DB-orchestration tests for utils/stocks_subscription.py's helpers -- same
FakeCursor/FakeDB pattern as tests/test_stock_auth_viewers.py, matching
normalized SQL text prefixes rather than hitting a real database."""
from datetime import datetime, timezone

from stoqbell.utils.stocks_subscription import (
    activate_subscription,
    activate_trial,
    attach_razorpay_subscription,
    create_pending_subscriber,
    find_account_by_razorpay_subscription_id,
    find_expired_trials,
    find_expiring_subscribers,
    has_stocks_access,
    mark_reminder_sent,
    mark_subscription_cancelled,
    mark_subscription_halted,
    mark_trial_ended_email_sent,
    record_recurring_charge,
)


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeSubscriberDB:
    def __init__(self, rows=None):
        self.rows = rows or []
        self._next_id = 1

    def execute(self, sql, params=None):
        params = params or ()
        normalized = ' '.join(sql.split())

        if normalized.startswith(
            'SELECT id, username, name, is_active, is_pro, subscription_status, subscription_current_period_end, '
            'trial_ends_at, referred_by_id, stocks_plan FROM stocks_admin_users WHERE username=?'
        ):
            username, = params
            matches = [r for r in self.rows if r['username'] == username]
            return FakeCursor(matches[:1])

        if normalized.startswith(
            "INSERT INTO stocks_admin_users (username, password_hash, role, name, is_active, must_change_password, "
            "subscription_status, trial_ends_at, is_pro, referred_by_id, stocks_plan)"
        ):
            # Pro signup -- 7-day trial, plan literal 'pro' in the SQL.
            email, password_hash, name, referred_by_id = params
            self.rows.append({
                'id': self._next_id, 'username': email, 'password_hash': password_hash, 'name': name,
                'role': 'viewer', 'is_active': 1, 'must_change_password': 0, 'is_pro': 0,
                'subscription_status': 'trialing', 'subscription_current_period_end': None,
                'trial_ends_at': 'fake-trial-end', 'trial_ended_email_sent_at': None,
                'razorpay_subscription_id': None, 'referred_by_id': referred_by_id, 'stocks_plan': 'pro',
            })
            self._next_id += 1
            return FakeCursor([])

        if normalized.startswith('INSERT INTO stocks_admin_users'):
            # Regular signup -- free, active immediately, no trial.
            email, password_hash, name, referred_by_id = params
            self.rows.append({
                'id': self._next_id, 'username': email, 'password_hash': password_hash, 'name': name,
                'role': 'viewer', 'is_active': 1, 'must_change_password': 0, 'is_pro': 0,
                'subscription_status': 'none', 'subscription_current_period_end': None,
                'trial_ends_at': None, 'trial_ended_email_sent_at': None,
                'razorpay_subscription_id': None, 'referred_by_id': referred_by_id, 'stocks_plan': 'regular',
            })
            self._next_id += 1
            return FakeCursor([])

        if normalized.startswith(
            'SELECT id, username, name, can_view_watchlist, must_change_password, subscription_status, '
            'trial_ends_at, razorpay_subscription_id, referred_by_id, stocks_plan '
            'FROM stocks_admin_users WHERE username=?'
        ):
            username, = params
            matches = [r for r in self.rows if r['username'] == username]
            return FakeCursor(matches[:1])

        if normalized.startswith('UPDATE stocks_admin_users SET razorpay_customer_id=?, razorpay_subscription_id=?'):
            customer_id, subscription_id, admin_id = params
            for r in self.rows:
                if r['id'] == admin_id:
                    r['razorpay_customer_id'] = customer_id
                    r['razorpay_subscription_id'] = subscription_id
            return FakeCursor([])

        if normalized.startswith("UPDATE stocks_admin_users SET is_active=1, subscription_status='active', "
                                  "subscription_current_period_end=?, updated_at=NOW() WHERE id=?"):
            period_end, admin_id = params
            for r in self.rows:
                if r['id'] == admin_id:
                    r['is_active'] = 1
                    r['subscription_status'] = 'active'
                    r['subscription_current_period_end'] = period_end
            return FakeCursor([])

        if normalized.startswith("UPDATE stocks_admin_users SET is_active=1, subscription_status='active', "
                                  "subscription_current_period_end=?, updated_at=NOW() WHERE razorpay_subscription_id=?"):
            period_end, subscription_id = params
            for r in self.rows:
                if r.get('razorpay_subscription_id') == subscription_id:
                    r['is_active'] = 1
                    r['subscription_status'] = 'active'
                    r['subscription_current_period_end'] = period_end
            return FakeCursor([])

        if normalized.startswith('SELECT id, username, name FROM stocks_admin_users WHERE razorpay_subscription_id=?'):
            subscription_id, = params
            matches = [r for r in self.rows if r.get('razorpay_subscription_id') == subscription_id]
            return FakeCursor(matches[:1])

        if normalized.startswith("UPDATE stocks_admin_users SET subscription_status='cancelled'"):
            subscription_id, = params
            for r in self.rows:
                if r.get('razorpay_subscription_id') == subscription_id:
                    r['subscription_status'] = 'cancelled'
            return FakeCursor([])

        if normalized.startswith("UPDATE stocks_admin_users SET subscription_status='halted'"):
            subscription_id, = params
            for r in self.rows:
                if r.get('razorpay_subscription_id') == subscription_id:
                    r['subscription_status'] = 'halted'
            return FakeCursor([])

        if normalized.startswith('UPDATE stocks_admin_users SET subscription_reminder_sent_for=?'):
            period_end, admin_id = params
            for r in self.rows:
                if r['id'] == admin_id:
                    r['subscription_reminder_sent_for'] = period_end
            return FakeCursor([])

        if normalized.startswith("UPDATE stocks_admin_users SET is_active=1, subscription_status='trialing', "
                                  "trial_ends_at=NOW() + INTERVAL '7 days', updated_at=NOW() WHERE id=?"):
            admin_id, = params
            for r in self.rows:
                if r['id'] == admin_id:
                    r['is_active'] = 1
                    r['subscription_status'] = 'trialing'
                    r['trial_ends_at'] = 'fake-trial-end'
            return FakeCursor([])

        if normalized.startswith(
            "SELECT id, username, name FROM stocks_admin_users WHERE subscription_status='trialing' "
            "AND trial_ends_at <= NOW() AND trial_ended_email_sent_at IS NULL"
        ):
            matches = [
                r for r in self.rows
                if r.get('subscription_status') == 'trialing' and r.get('trial_ends_at') is not None
                and r.get('trial_ended_email_sent_at') is None
            ]
            return FakeCursor(matches)

        if normalized.startswith('UPDATE stocks_admin_users SET trial_ended_email_sent_at=NOW()'):
            admin_id, = params
            for r in self.rows:
                if r['id'] == admin_id:
                    r['trial_ended_email_sent_at'] = 'sent'
            return FakeCursor([])

        if normalized.startswith("SELECT id, username, name, subscription_status, subscription_current_period_end "
                                  "FROM stocks_admin_users WHERE subscription_status IN"):
            window_str, = params
            window_days = int(window_str.split()[0])
            now = datetime.now(timezone.utc)
            matches = []
            for r in self.rows:
                if r.get('subscription_status') not in ('active', 'cancelled'):
                    continue
                end = r.get('subscription_current_period_end')
                if end is None:
                    continue
                remaining_days = (end - now).days
                if not (0 <= remaining_days <= window_days):
                    continue
                if r.get('subscription_reminder_sent_for') == end:
                    continue
                matches.append(r)
            return FakeCursor(matches)

        raise AssertionError(f'Unexpected SQL in test: {sql}')

    def commit(self):
        pass


def test_create_pending_subscriber_creates_an_active_free_regular_account_by_default():
    # 'regular' is the default: active immediately, no payment, no trial.
    db = FakeSubscriberDB()
    row, error = create_pending_subscriber(db, 'a@example.com', 'A', 'password123')

    assert error is None
    assert db.rows[0]['stocks_plan'] == 'regular'
    assert db.rows[0]['subscription_status'] == 'none'
    assert db.rows[0]['is_active'] == 1
    assert db.rows[0]['trial_ends_at'] is None
    assert db.rows[0]['is_pro'] == 0


def test_create_pending_subscriber_pro_gets_a_seven_day_trial():
    db = FakeSubscriberDB()
    row, error = create_pending_subscriber(db, 'a@example.com', 'A', 'password123', stocks_plan='pro')

    assert error is None
    assert db.rows[0]['stocks_plan'] == 'pro'
    assert db.rows[0]['subscription_status'] == 'trialing'
    assert db.rows[0]['is_active'] == 1
    assert db.rows[0]['trial_ends_at'] is not None
    assert db.rows[0]['is_pro'] == 0


def test_create_pending_subscriber_rejects_short_password():
    db = FakeSubscriberDB()
    row, error = create_pending_subscriber(db, 'a@example.com', 'A', 'short')
    assert row is None
    assert 'at least 8' in error
    assert db.rows == []


def test_create_pending_subscriber_returns_full_existing_row_not_a_partial_one():
    # Regression test: the "existing account" lookup must select every
    # field has_stocks_access needs (is_pro, subscription_current_period_end),
    # not just id/subscription_status -- a narrower SELECT here previously
    # made a paid, still-current 'cancelled' subscriber look inactive to
    # the caller purely because subscription_current_period_end came back
    # as None from the lookup itself, not from the actual account state.
    period_end = datetime.now(timezone.utc)
    db = FakeSubscriberDB(rows=[{
        'id': 1, 'username': 'a@example.com', 'name': 'A', 'is_active': 1, 'is_pro': 0,
        'subscription_status': 'cancelled', 'subscription_current_period_end': period_end,
    }])

    row, error = create_pending_subscriber(db, 'a@example.com', 'A', 'password123')

    assert error == 'existing'
    assert row['is_pro'] == 0
    assert row['subscription_current_period_end'] == period_end


def test_attach_razorpay_subscription_sets_the_ids():
    db = FakeSubscriberDB()
    create_pending_subscriber(db, 'a@example.com', 'A', 'password123')
    admin_id = db.rows[0]['id']

    attach_razorpay_subscription(db, admin_id, 'cust_1', 'sub_1')

    assert db.rows[0]['razorpay_customer_id'] == 'cust_1'
    assert db.rows[0]['razorpay_subscription_id'] == 'sub_1'


def test_activate_subscription_by_admin_id():
    db = FakeSubscriberDB()
    create_pending_subscriber(db, 'a@example.com', 'A', 'password123')
    admin_id = db.rows[0]['id']
    period_end = datetime.now(timezone.utc)

    activate_subscription(db, admin_id, period_end)

    assert db.rows[0]['is_active'] == 1
    assert db.rows[0]['subscription_status'] == 'active'
    assert db.rows[0]['subscription_current_period_end'] == period_end


def test_record_recurring_charge_looked_up_by_subscription_id():
    db = FakeSubscriberDB(rows=[{
        'id': 1, 'username': 'a@example.com', 'is_active': 1, 'is_pro': 0,
        'subscription_status': 'active', 'subscription_current_period_end': None,
        'razorpay_subscription_id': 'sub_1',
    }])
    new_period_end = datetime.now(timezone.utc)

    record_recurring_charge(db, 'sub_1', new_period_end)

    assert db.rows[0]['subscription_current_period_end'] == new_period_end
    assert db.rows[0]['subscription_status'] == 'active'


def test_mark_subscription_cancelled_and_halted():
    db = FakeSubscriberDB(rows=[
        {'id': 1, 'username': 'a@example.com', 'subscription_status': 'active', 'razorpay_subscription_id': 'sub_1'},
        {'id': 2, 'username': 'b@example.com', 'subscription_status': 'active', 'razorpay_subscription_id': 'sub_2'},
    ])

    mark_subscription_cancelled(db, 'sub_1')
    mark_subscription_halted(db, 'sub_2')

    assert db.rows[0]['subscription_status'] == 'cancelled'
    assert db.rows[1]['subscription_status'] == 'halted'


def test_find_account_by_razorpay_subscription_id():
    db = FakeSubscriberDB(rows=[
        {'id': 1, 'username': 'a@example.com', 'name': 'A', 'subscription_status': 'active', 'razorpay_subscription_id': 'sub_1'},
        {'id': 2, 'username': 'b@example.com', 'name': 'B', 'subscription_status': 'active', 'razorpay_subscription_id': 'sub_2'},
    ])

    found = find_account_by_razorpay_subscription_id(db, 'sub_2')
    assert found['username'] == 'b@example.com'

    assert find_account_by_razorpay_subscription_id(db, 'sub_unknown') is None


def test_activate_trial_sets_active_trialing_and_trial_ends_at():
    db = FakeSubscriberDB(rows=[{
        'id': 1, 'username': 'a@example.com', 'is_active': 0, 'subscription_status': 'pending',
        'trial_ends_at': None,
    }])

    activate_trial(db, 1)

    assert db.rows[0]['is_active'] == 1
    assert db.rows[0]['subscription_status'] == 'trialing'
    assert db.rows[0]['trial_ends_at'] is not None


def test_find_expired_trials_and_email_dedup():
    db = FakeSubscriberDB(rows=[
        {'id': 1, 'username': 'expired@example.com', 'name': 'Expired', 'subscription_status': 'trialing',
         'trial_ends_at': 'past', 'trial_ended_email_sent_at': None},
        {'id': 2, 'username': 'stillgoing@example.com', 'name': 'Going', 'subscription_status': 'trialing',
         'trial_ends_at': None, 'trial_ended_email_sent_at': None},
        {'id': 3, 'username': 'alreadysent@example.com', 'name': 'Sent', 'subscription_status': 'trialing',
         'trial_ends_at': 'past', 'trial_ended_email_sent_at': 'already'},
        {'id': 4, 'username': 'paid@example.com', 'name': 'Paid', 'subscription_status': 'active',
         'trial_ends_at': None, 'trial_ended_email_sent_at': None},
    ])

    expired = find_expired_trials(db)
    assert {r['id'] for r in expired} == {1}

    mark_trial_ended_email_sent(db, 1)
    assert db.rows[0]['trial_ended_email_sent_at'] is not None

    assert find_expired_trials(db) == []


def test_find_expiring_subscribers_and_reminder_dedup():
    from datetime import timedelta
    soon = datetime.now(timezone.utc) + timedelta(days=1)
    far = datetime.now(timezone.utc).replace(year=datetime.now(timezone.utc).year + 1)
    db = FakeSubscriberDB(rows=[
        {'id': 1, 'username': 'soon@example.com', 'name': 'Soon', 'subscription_status': 'active',
         'subscription_current_period_end': soon, 'subscription_reminder_sent_for': None},
        {'id': 2, 'username': 'far@example.com', 'name': 'Far', 'subscription_status': 'active',
         'subscription_current_period_end': far, 'subscription_reminder_sent_for': None},
        {'id': 3, 'username': 'already@example.com', 'name': 'Already', 'subscription_status': 'active',
         'subscription_current_period_end': soon, 'subscription_reminder_sent_for': soon},
    ])

    expiring = find_expiring_subscribers(db, window_days=3)
    assert {r['id'] for r in expiring} == {1}

    mark_reminder_sent(db, 1, soon)
    assert db.rows[0]['subscription_reminder_sent_for'] == soon

    expiring_again = find_expiring_subscribers(db, window_days=3)
    assert expiring_again == []
