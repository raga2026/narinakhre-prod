"""Tests for utils/stocks_referrals.py -- same FakeCursor/FakeDB pattern as
tests/test_stocks_subscription_db.py, matching normalized SQL text prefixes
rather than hitting a real database."""
from datetime import datetime, timedelta, timezone

from utils.stocks_referrals import (
    FREE_MONTH_DAYS,
    REFERRAL_CODE_LENGTH,
    REFERRALS_PER_FREE_MONTH,
    apply_referral_credits_on_cancellation,
    available_referral_credits,
    count_qualified_referrals,
    find_referrer_by_code,
    generate_referral_code,
    get_or_create_referral_code,
)


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeReferralDB:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, sql, params=None):
        params = params or ()
        normalized = ' '.join(sql.split())

        if normalized.startswith('SELECT referral_code FROM stocks_admin_users WHERE id=?'):
            admin_id, = params
            matches = [r for r in self.rows if r['id'] == admin_id]
            return FakeCursor(matches[:1])

        if normalized.startswith('SELECT id FROM stocks_admin_users WHERE referral_code=?'):
            code, = params
            matches = [r for r in self.rows if r.get('referral_code') == code]
            return FakeCursor(matches[:1])

        if normalized.startswith('UPDATE stocks_admin_users SET referral_code=?'):
            code, admin_id = params
            for r in self.rows:
                if r['id'] == admin_id:
                    r['referral_code'] = code
            return FakeCursor([])

        if normalized.startswith('SELECT id, username, name FROM stocks_admin_users WHERE referral_code=?'):
            code, = params
            matches = [r for r in self.rows if r.get('referral_code') == code]
            return FakeCursor(matches[:1])

        if normalized.startswith("SELECT COUNT(*) AS n FROM stocks_admin_users WHERE referred_by_id=?"):
            referrer_id, = params
            n = sum(
                1 for r in self.rows
                if r.get('referred_by_id') == referrer_id and r.get('subscription_status') not in ('none', 'pending')
            )
            return FakeCursor([{'n': n}])

        if normalized.startswith('SELECT referral_credits_redeemed, subscription_current_period_end FROM stocks_admin_users WHERE id=?'):
            admin_id, = params
            matches = [r for r in self.rows if r['id'] == admin_id]
            return FakeCursor(matches[:1])

        if normalized.startswith('SELECT referral_credits_redeemed FROM stocks_admin_users WHERE id=?'):
            admin_id, = params
            matches = [r for r in self.rows if r['id'] == admin_id]
            return FakeCursor(matches[:1])

        if normalized.startswith('UPDATE stocks_admin_users SET subscription_current_period_end=?, referral_credits_redeemed=?'):
            new_end, new_redeemed, admin_id = params
            for r in self.rows:
                if r['id'] == admin_id:
                    r['subscription_current_period_end'] = new_end
                    r['referral_credits_redeemed'] = new_redeemed
            return FakeCursor([])

        raise AssertionError(f'Unexpected SQL in test: {sql}')

    def commit(self):
        pass


def test_generate_referral_code_length_and_alphabet():
    code = generate_referral_code()
    assert len(code) == REFERRAL_CODE_LENGTH
    assert code == code.upper()
    # No ambiguous characters (0/O/1/l/I) -- reused from stock_auth's own
    # password alphabet, which already excludes them.
    assert not any(c in code for c in '0O1lI')


def test_get_or_create_referral_code_generates_once_then_reuses():
    db = FakeReferralDB([{'id': 1, 'referral_code': None}])

    first = get_or_create_referral_code(db, 1)
    assert len(first) == REFERRAL_CODE_LENGTH
    assert db.rows[0]['referral_code'] == first

    second = get_or_create_referral_code(db, 1)
    assert second == first  # not regenerated


def test_find_referrer_by_code_matches_and_rejects_blank_or_unknown():
    db = FakeReferralDB([{'id': 1, 'username': 'ref@example.com', 'name': 'Ref', 'referral_code': 'ABCD1234'}])

    found = find_referrer_by_code(db, 'abcd1234')  # case-insensitive
    assert found['username'] == 'ref@example.com'

    assert find_referrer_by_code(db, '') is None
    assert find_referrer_by_code(db, None) is None
    assert find_referrer_by_code(db, 'NOTREAL1') is None


def test_count_qualified_referrals_only_counts_actually_paid_accounts():
    db = FakeReferralDB([
        {'id': 1, 'referred_by_id': None, 'subscription_status': 'none'},
        {'id': 2, 'referred_by_id': 1, 'subscription_status': 'pending'},   # signed up, never paid -- not qualified
        {'id': 3, 'referred_by_id': 1, 'subscription_status': 'active'},    # qualified
        {'id': 4, 'referred_by_id': 1, 'subscription_status': 'cancelled'}, # qualified -- paid at some point
        {'id': 5, 'referred_by_id': 1, 'subscription_status': 'halted'},    # qualified -- paid at some point
        {'id': 6, 'referred_by_id': 99, 'subscription_status': 'active'},   # someone else's referral
    ])

    assert count_qualified_referrals(db, 1) == 3


def test_available_referral_credits_math():
    db = FakeReferralDB([
        {'id': 1, 'referral_credits_redeemed': 0},
        *[{'id': i, 'referred_by_id': 1, 'subscription_status': 'active'} for i in range(2, 2 + 7)],  # 7 qualified
    ])

    # 7 // 3 = 2 credits available, none redeemed yet.
    assert available_referral_credits(db, 1) == 2


def test_available_referral_credits_subtracts_already_redeemed():
    db = FakeReferralDB([
        {'id': 1, 'referral_credits_redeemed': 1},
        *[{'id': i, 'referred_by_id': 1, 'subscription_status': 'active'} for i in range(2, 2 + 7)],  # 7 qualified -> 2 earned
    ])

    assert available_referral_credits(db, 1) == 1  # 2 earned - 1 already redeemed


def test_available_referral_credits_never_negative():
    db = FakeReferralDB([
        {'id': 1, 'referral_credits_redeemed': 5},
        *[{'id': i, 'referred_by_id': 1, 'subscription_status': 'active'} for i in range(2, 2 + 3)],  # only 1 earned
    ])

    assert available_referral_credits(db, 1) == 0


def test_apply_referral_credits_extends_period_end_and_marks_redeemed():
    period_end = datetime(2026, 9, 1, tzinfo=timezone.utc)
    db = FakeReferralDB([
        {'id': 1, 'referral_credits_redeemed': 0, 'subscription_current_period_end': period_end},
        *[{'id': i, 'referred_by_id': 1, 'subscription_status': 'active'} for i in range(2, 2 + 3)],  # 1 credit earned
    ])

    applied = apply_referral_credits_on_cancellation(db, 1)

    assert applied == 1
    assert db.rows[0]['subscription_current_period_end'] == period_end + timedelta(days=FREE_MONTH_DAYS)
    assert db.rows[0]['referral_credits_redeemed'] == 1


def test_apply_referral_credits_is_a_noop_when_none_available():
    period_end = datetime(2026, 9, 1, tzinfo=timezone.utc)
    db = FakeReferralDB([
        {'id': 1, 'referral_credits_redeemed': 0, 'subscription_current_period_end': period_end},
        # Only 2 qualified referrals -- below the REFERRALS_PER_FREE_MONTH threshold.
        *[{'id': i, 'referred_by_id': 1, 'subscription_status': 'active'} for i in range(2, 4)],
    ])

    applied = apply_referral_credits_on_cancellation(db, 1)

    assert applied == 0
    assert db.rows[0]['subscription_current_period_end'] == period_end  # unchanged
    assert db.rows[0]['referral_credits_redeemed'] == 0


def test_apply_referral_credits_noop_when_period_end_missing():
    db = FakeReferralDB([
        {'id': 1, 'referral_credits_redeemed': 0, 'subscription_current_period_end': None},
        *[{'id': i, 'referred_by_id': 1, 'subscription_status': 'active'} for i in range(2, 2 + REFERRALS_PER_FREE_MONTH)],
    ])

    assert apply_referral_credits_on_cancellation(db, 1) == 0


def test_apply_referral_credits_handles_multiple_credits_at_once():
    period_end = datetime(2026, 9, 1, tzinfo=timezone.utc)
    db = FakeReferralDB([
        {'id': 1, 'referral_credits_redeemed': 0, 'subscription_current_period_end': period_end},
        *[{'id': i, 'referred_by_id': 1, 'subscription_status': 'active'} for i in range(2, 2 + 6)],  # 2 credits earned
    ])

    applied = apply_referral_credits_on_cancellation(db, 1)

    assert applied == 2
    assert db.rows[0]['subscription_current_period_end'] == period_end + timedelta(days=FREE_MONTH_DAYS * 2)
