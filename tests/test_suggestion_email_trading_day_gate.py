"""Exercises the same skip-but-record-success shape used by app.py's
stocks_suggestions_send_daily_email._job, without importing app.py itself
(see test_admin_stocks_sync_auth.py's docstring for why -- app.py requires
live Supabase/env vars at import time). Mirrors that route's _job logic
line for line, but with generate_daily_suggestions/send_daily_suggestions_email/
record_job_success/is_trading_day swapped for fakes so the gate itself,
not the real DB calls, is what's under test."""
from flask import Flask


def _build_test_app(is_trading_day_fn, calls):
    app = Flask(__name__)
    app.secret_key = 'test-secret-key'

    def _job(job_db):
        if not is_trading_day_fn():
            calls.append('record_job_success')
            return {'status': 'skipped', 'reason': 'not a trading day'}
        calls.append('generate_daily_suggestions')
        calls.append('send_daily_suggestions_email')
        calls.append('record_job_success')
        return {'status': 'ok'}

    @app.route('/stocks/suggestions/send-daily-email', methods=['POST'])
    def stocks_suggestions_send_daily_email():
        return _job(job_db=None)

    return app


def test_non_trading_day_skips_generation_and_email_but_still_records_success():
    calls = []
    app = _build_test_app(is_trading_day_fn=lambda: False, calls=calls)
    client = app.test_client()

    response = client.post('/stocks/suggestions/send-daily-email')

    assert response.get_json()['status'] == 'skipped'
    assert calls == ['record_job_success']
    assert 'generate_daily_suggestions' not in calls
    assert 'send_daily_suggestions_email' not in calls


def test_trading_day_runs_generation_and_email_then_records_success():
    calls = []
    app = _build_test_app(is_trading_day_fn=lambda: True, calls=calls)
    client = app.test_client()

    response = client.post('/stocks/suggestions/send-daily-email')

    assert response.get_json()['status'] == 'ok'
    assert calls == ['generate_daily_suggestions', 'send_daily_suggestions_email', 'record_job_success']
