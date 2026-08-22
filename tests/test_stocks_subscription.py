import hashlib
import hmac
from datetime import datetime, timedelta, timezone

from stoqbell.utils.stocks_subscription import (
    days_until,
    has_stocks_access,
    is_within_reminder_window,
    subscription_is_current,
    verify_signature,
    verify_subscription_payment_signature,
    verify_webhook_signature,
)

SECRET = 'whsec_test_secret'


def _sign(message, secret=SECRET):
    return hmac.new(secret.encode('utf-8'), message.encode('utf-8'), hashlib.sha256).hexdigest()


# --- verify_signature / verify_subscription_payment_signature --------------

def test_verify_signature_accepts_a_correctly_signed_message():
    message = 'pay_123|sub_456'
    assert verify_signature(message, _sign(message), SECRET) is True


def test_verify_signature_rejects_a_tampered_message():
    message = 'pay_123|sub_456'
    signature = _sign(message)
    assert verify_signature('pay_123|sub_999', signature, SECRET) is False


def test_verify_signature_rejects_wrong_secret():
    message = 'pay_123|sub_456'
    assert verify_signature(message, _sign(message, secret='wrong-secret'), SECRET) is False


def test_verify_signature_rejects_empty_signature():
    assert verify_signature('pay_123|sub_456', '', SECRET) is False


def test_subscription_payment_signature_uses_payment_id_then_subscription_id_order():
    # The message format is payment_id|subscription_id -- NOT the reverse.
    # A signature computed over the reversed order must NOT verify.
    payment_id, subscription_id = 'pay_123', 'sub_456'
    correct_message = f'{payment_id}|{subscription_id}'
    reversed_message = f'{subscription_id}|{payment_id}'

    assert verify_subscription_payment_signature(
        payment_id, subscription_id, _sign(correct_message), SECRET
    ) is True
    assert verify_subscription_payment_signature(
        payment_id, subscription_id, _sign(reversed_message), SECRET
    ) is False


# --- verify_webhook_signature ------------------------------------------------

def test_webhook_signature_verifies_over_the_raw_body_string():
    body = '{"event":"subscription.charged","payload":{}}'
    assert verify_webhook_signature(body, _sign(body), SECRET) is True


def test_webhook_signature_accepts_bytes_body():
    body = '{"event":"subscription.charged"}'
    assert verify_webhook_signature(body.encode('utf-8'), _sign(body), SECRET) is True


def test_webhook_signature_rejects_a_body_that_was_re_serialized():
    # Even semantically-identical JSON with different whitespace/key order
    # must fail -- the signature covers the exact bytes Razorpay sent.
    original = '{"event":"subscription.charged"}'
    reserialized = '{"event": "subscription.charged"}'
    assert verify_webhook_signature(reserialized, _sign(original), SECRET) is False


# --- subscription_is_current -------------------------------------------------

def test_none_status_always_passes_admin_created_viewer():
    assert subscription_is_current('none', None) is True
    assert subscription_is_current(None, None) is True


def test_active_status_passes_regardless_of_period_end():
    # Even a stale/past period_end still passes for 'active' -- the webhook,
    # not this date, is what would have flipped status away from 'active'.
    past = datetime.now(timezone.utc) - timedelta(days=30)
    assert subscription_is_current('active', past) is True


def test_pending_status_never_passes():
    future = datetime.now(timezone.utc) + timedelta(days=30)
    assert subscription_is_current('pending', future) is False


def test_halted_status_never_passes():
    future = datetime.now(timezone.utc) + timedelta(days=30)
    assert subscription_is_current('halted', future) is False


def test_cancelled_status_passes_until_period_end():
    future = datetime.now(timezone.utc) + timedelta(days=1)
    assert subscription_is_current('cancelled', future) is True


def test_cancelled_status_fails_after_period_end():
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert subscription_is_current('cancelled', past) is False


def test_cancelled_status_with_no_period_end_fails():
    assert subscription_is_current('cancelled', None) is False


def test_subscription_is_current_parses_iso_string_timestamps():
    # Supabase returns TIMESTAMPTZ columns as ISO strings, not datetimes --
    # authenticate_stocks_admin passes the row value straight through.
    future_str = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat().replace('+00:00', 'Z')
    assert subscription_is_current('cancelled', future_str) is True


def test_trialing_status_passes_until_trial_ends_at():
    future = datetime.now(timezone.utc) + timedelta(days=3)
    assert subscription_is_current('trialing', None, trial_ends_at=future) is True


def test_trialing_status_fails_after_trial_ends_at():
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert subscription_is_current('trialing', None, trial_ends_at=past) is False


def test_trialing_status_with_no_trial_ends_at_fails():
    # Defensive -- should never happen in practice (activate_trial always
    # stamps trial_ends_at in the same statement that sets 'trialing'), but
    # a trial with no recorded end must not silently grant access forever.
    assert subscription_is_current('trialing', None, trial_ends_at=None) is False


def test_trialing_status_parses_iso_string_trial_ends_at():
    future_str = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat().replace('+00:00', 'Z')
    assert subscription_is_current('trialing', None, trial_ends_at=future_str) is True


# --- has_stocks_access -------------------------------------------------------

def test_is_pro_grants_access_regardless_of_subscription_status():
    assert has_stocks_access(True, 'halted', None) is True
    assert has_stocks_access(True, 'pending', None) is True
    assert has_stocks_access(1, None, None) is True  # is_pro stored as INTEGER 1, not bool True


def test_not_pro_falls_through_to_subscription_check():
    future = datetime.now(timezone.utc) + timedelta(days=10)
    assert has_stocks_access(False, 'active', None) is True
    assert has_stocks_access(False, 'pending', future) is False
    assert has_stocks_access(0, 'none', None) is True  # is_pro=0 but never a paid account either


def test_has_stocks_access_threads_trial_ends_at_through():
    future = datetime.now(timezone.utc) + timedelta(days=3)
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert has_stocks_access(False, 'trialing', None, trial_ends_at=future) is True
    assert has_stocks_access(False, 'trialing', None, trial_ends_at=past) is False


# --- days_until / is_within_reminder_window ---------------------------------

def test_days_until_positive_for_future_date():
    future = datetime.now(timezone.utc) + timedelta(days=5)
    assert days_until(future) in (4, 5)  # tolerate the boundary depending on time-of-day


def test_days_until_negative_for_past_date():
    past = datetime.now(timezone.utc) - timedelta(days=5)
    assert days_until(past) < 0


def test_reminder_window_true_within_window():
    soon = datetime.now(timezone.utc) + timedelta(days=2)
    assert is_within_reminder_window(soon, window_days=3) is True


def test_reminder_window_false_outside_window():
    far = datetime.now(timezone.utc) + timedelta(days=10)
    assert is_within_reminder_window(far, window_days=3) is False


def test_reminder_window_false_for_already_passed_date():
    past = datetime.now(timezone.utc) - timedelta(days=1)
    assert is_within_reminder_window(past, window_days=3) is False
