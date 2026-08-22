"""Exercises stocks_subscription_notify_trial_ended._job's mark-as-sent
logic in isolation (see test_suggestion_email_trading_day_gate.py's
docstring for why this mirrors the route's logic rather than importing
app.py directly). Same "don't silently burn the one-time notification on a
failed send" rule as test_target_hit_notify_job.py -- mark_trial_ended_email_sent
should only run for a row whose email actually succeeded, so a transient
failure retries tomorrow instead of that subscriber never hearing their
trial ended."""
from flask import Flask


def _build_test_app(expired_trials, send_results, calls):
    app = Flask(__name__)
    app.secret_key = 'test-secret-key'

    def find_expired_trials(job_db):
        return expired_trials

    def send_trial_ended_email(email, name):
        return send_results[email]

    def mark_trial_ended_email_sent(job_db, admin_id):
        calls.append(('mark_trial_ended_email_sent', admin_id))

    def _job(job_db):
        rows = find_expired_trials(job_db)
        sent = 0
        failed = 0
        for row in rows:
            ok, detail = send_trial_ended_email(row['username'], row.get('name'))
            if ok:
                mark_trial_ended_email_sent(job_db, row['id'])
                sent += 1
            else:
                failed += 1
        return {'expired_trials': len(rows), 'sent': sent, 'failed': failed}

    @app.route('/stocks/subscription/notify-trial-ended', methods=['POST'])
    def route():
        return _job(job_db=None)

    return app


def test_marks_sent_only_for_successful_sends():
    calls = []
    app = _build_test_app(
        expired_trials=[
            {'id': 1, 'username': 'a@example.com', 'name': 'A'},
            {'id': 2, 'username': 'b@example.com', 'name': 'B'},
        ],
        send_results={'a@example.com': (True, None), 'b@example.com': (False, 'timeout')},
        calls=calls,
    )
    response = app.test_client().post('/stocks/subscription/notify-trial-ended')

    body = response.get_json()
    assert body == {'expired_trials': 2, 'sent': 1, 'failed': 1}
    assert calls == [('mark_trial_ended_email_sent', 1)]


def test_no_expired_trials_does_nothing():
    calls = []
    app = _build_test_app(expired_trials=[], send_results={}, calls=calls)
    response = app.test_client().post('/stocks/subscription/notify-trial-ended')

    body = response.get_json()
    assert body == {'expired_trials': 0, 'sent': 0, 'failed': 0}
    assert calls == []
