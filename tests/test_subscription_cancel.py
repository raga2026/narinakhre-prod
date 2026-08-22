"""Exercises stocks_subscription_cancel's guard/cancel logic in isolation
(see test_suggestion_email_trading_day_gate.py's docstring for why this
mirrors the route's logic rather than importing app.py directly). Only a
real, currently 'active' paid subscription with a razorpay_subscription_id
on file should ever reach the Razorpay cancel call; everything else (a
free trial, an already-cancelled/halted/pending row) is refused up front."""
from flask import Flask, flash, get_flashed_messages, redirect, url_for


def _build_test_app(row, calls, razorpay_should_fail=False):
    app = Flask(__name__)
    app.secret_key = 'test-secret-key'

    def razorpay_cancel(subscription_id, data):
        calls.append(('razorpay_cancel', subscription_id, data))
        if razorpay_should_fail:
            raise Exception('Razorpay API error')

    def mark_subscription_cancelled(subscription_id):
        calls.append(('mark_subscription_cancelled', subscription_id))

    @app.route('/stocks/profile')
    def profile():
        return 'profile-page'

    @app.route('/stocks/subscription/cancel', methods=['POST'])
    def cancel():
        if row.get('subscription_status') != 'active' or not row.get('razorpay_subscription_id'):
            flash('No active subscription to cancel.', 'error')
            return redirect(url_for('profile'))
        try:
            razorpay_cancel(row['razorpay_subscription_id'], data={'cancel_at_cycle_end': 1})
        except Exception:
            flash('Could not cancel right now -- please try again shortly.', 'error')
            return redirect(url_for('profile'))
        mark_subscription_cancelled(row['razorpay_subscription_id'])
        flash('Your subscription has been cancelled -- access continues until your current billing period ends.', 'info')
        return redirect(url_for('profile'))

    return app


def test_active_subscription_cancels_at_cycle_end():
    calls = []
    row = {'subscription_status': 'active', 'razorpay_subscription_id': 'sub_123'}
    app = _build_test_app(row, calls)
    client = app.test_client()

    response = client.post('/stocks/subscription/cancel', follow_redirects=True)

    assert response.status_code == 200
    assert calls == [
        ('razorpay_cancel', 'sub_123', {'cancel_at_cycle_end': 1}),
        ('mark_subscription_cancelled', 'sub_123'),
    ]


def test_trial_has_nothing_to_cancel():
    calls = []
    row = {'subscription_status': 'trialing', 'razorpay_subscription_id': None}
    app = _build_test_app(row, calls)
    client = app.test_client()

    client.post('/stocks/subscription/cancel')

    assert calls == []


def test_already_cancelled_row_is_a_no_op():
    calls = []
    row = {'subscription_status': 'cancelled', 'razorpay_subscription_id': 'sub_123'}
    app = _build_test_app(row, calls)
    client = app.test_client()

    client.post('/stocks/subscription/cancel')

    assert calls == []


def test_razorpay_failure_does_not_mark_cancelled_locally():
    calls = []
    row = {'subscription_status': 'active', 'razorpay_subscription_id': 'sub_123'}
    app = _build_test_app(row, calls, razorpay_should_fail=True)
    client = app.test_client()

    client.post('/stocks/subscription/cancel')

    assert ('razorpay_cancel', 'sub_123', {'cancel_at_cycle_end': 1}) in calls
    assert not any(c[0] == 'mark_subscription_cancelled' for c in calls)
