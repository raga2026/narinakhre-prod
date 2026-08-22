"""Exercises _finish_stocks_signup's trial-vs-checkout fork in isolation
(see test_suggestion_email_trading_day_gate.py's docstring for why this
mirrors the route's logic rather than importing app.py directly). A
'trialing' row (just granted a trial by create_pending_subscriber/
create_pending_google_subscriber) should log straight in and land on
/stocks/my/suggestions with no Razorpay step; any other row (Starters, or
a resubmitted signup that didn't get a fresh trial) should be sent to
checkout."""
from flask import Flask, flash, get_flashed_messages, redirect, session, url_for


def _build_test_app(checkout_calls):
    app = Flask(__name__)
    app.secret_key = 'test-secret-key'

    def _render_stocks_checkout(admin_id, email, name, plan='standard', referral_plan=False):
        checkout_calls.append((admin_id, email, name, plan, referral_plan))
        return 'checkout-page'

    def _finish_stocks_signup(row, email, name, plan):
        if row.get('subscription_status') == 'trialing':
            session['stocks_admin_id'] = row['id']
            session['stocks_admin_username'] = row['username']
            session['stocks_plan'] = plan
            flash('Your 7-day free trial has started', 'info')
            return redirect(url_for('my_suggestions'))
        return _render_stocks_checkout(
            row['id'], email, name, plan=plan,
            referral_plan=bool(row.get('referred_by_id')) and plan == 'standard',
        )

    @app.route('/stocks/my/suggestions')
    def my_suggestions():
        return 'suggestions-page'

    @app.route('/finish/<mode>')
    def finish(mode):
        if mode == 'trialing':
            row = {'id': 1, 'username': 'a@example.com', 'subscription_status': 'trialing'}
            return _finish_stocks_signup(row, 'a@example.com', 'A', 'standard')
        if mode == 'starters':
            row = {'id': 2, 'username': 'b@example.com', 'subscription_status': 'pending'}
            return _finish_stocks_signup(row, 'b@example.com', 'B', 'starters')
        if mode == 'resubmitted-no-trial':
            row = {'id': 3, 'username': 'c@example.com', 'subscription_status': 'pending', 'referred_by_id': 9}
            return _finish_stocks_signup(row, 'c@example.com', 'C', 'standard')

    return app


def test_trialing_row_logs_straight_in_no_checkout():
    checkout_calls = []
    app = _build_test_app(checkout_calls)
    client = app.test_client()

    response = client.get('/finish/trialing', follow_redirects=True)

    assert response.status_code == 200
    assert response.data == b'suggestions-page'
    assert checkout_calls == []
    with client.session_transaction() as sess:
        assert sess['stocks_admin_id'] == 1
        assert sess['stocks_plan'] == 'standard'


def test_starters_row_goes_to_checkout_not_trial():
    checkout_calls = []
    app = _build_test_app(checkout_calls)
    client = app.test_client()

    response = client.get('/finish/starters')

    assert response.data == b'checkout-page'
    assert checkout_calls == [(2, 'b@example.com', 'B', 'starters', False)]


def test_resubmitted_pending_row_goes_to_checkout_with_referral_plan():
    # A row that already existed (didn't get a fresh trial this call) still
    # gets the referral-plan discount it originally qualified for.
    checkout_calls = []
    app = _build_test_app(checkout_calls)
    client = app.test_client()

    response = client.get('/finish/resubmitted-no-trial')

    assert response.data == b'checkout-page'
    assert checkout_calls == [(3, 'c@example.com', 'C', 'standard', True)]
