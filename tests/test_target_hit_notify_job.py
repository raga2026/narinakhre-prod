"""Exercises stocks_suggestions_notify_target_hits._job's mark-as-notified
logic in isolation (see test_suggestion_email_trading_day_gate.py's
docstring for why this mirrors the route's logic rather than importing
app.py directly). The bug this guards against: mark_suggestions_target_hit
used to run unconditionally whenever hits were found, even if there were
no active Standard-plan recipients or every single send failed -- silently
burning the 'pending' status with nobody ever actually notified, and no
way to retry since the row would no longer be status='pending' the next
day. The fixed rule: mark hits as notified when at least one email sent
successfully, OR when there were no recipients to begin with (nothing to
retry for either way); leave them pending (for tomorrow's retry) only when
recipients existed but every single send failed."""
from flask import Flask


def _build_test_app(hits, recipients, send_results, calls):
    app = Flask(__name__)
    app.secret_key = 'test-secret-key'

    def find_pending_target_hit_suggestions(job_db):
        return hits

    def fetch_recipients(job_db):
        return recipients

    def send_target_achieved_email(email, name, hits):
        return send_results[email]

    def mark_suggestions_target_hit(job_db, ids):
        calls.append(('mark_suggestions_target_hit', tuple(ids)))

    def _job(job_db):
        found = find_pending_target_hit_suggestions(job_db)
        sent = 0
        failed = 0
        if found:
            recips = fetch_recipients(job_db)
            for r in recips:
                ok, detail = send_target_achieved_email(r['email'], r.get('name'), found)
                if ok:
                    sent += 1
                else:
                    failed += 1
            if sent > 0 or not recips:
                mark_suggestions_target_hit(job_db, [h['id'] for h in found])
        return {'target_hits': len(found), 'recipients_sent': sent, 'recipients_failed': failed}

    @app.route('/stocks/suggestions/notify-target-hits', methods=['POST'])
    def route():
        return _job(job_db=None)

    return app


def test_marks_hit_when_at_least_one_email_succeeds():
    calls = []
    app = _build_test_app(
        hits=[{'id': 1}, {'id': 2}],
        recipients=[{'email': 'a@x.com', 'name': 'A'}, {'email': 'b@x.com', 'name': 'B'}],
        send_results={'a@x.com': (True, None), 'b@x.com': (False, 'timeout')},
        calls=calls,
    )
    response = app.test_client().post('/stocks/suggestions/notify-target-hits')

    body = response.get_json()
    assert body == {'target_hits': 2, 'recipients_sent': 1, 'recipients_failed': 1}
    assert calls == [('mark_suggestions_target_hit', (1, 2))]


def test_marks_hit_when_there_are_no_recipients_at_all():
    # Nothing currently on the Standard plan -- there's no one to retry
    # for, so this stays marked rather than accumulating forever.
    calls = []
    app = _build_test_app(hits=[{'id': 5}], recipients=[], send_results={}, calls=calls)
    response = app.test_client().post('/stocks/suggestions/notify-target-hits')

    body = response.get_json()
    assert body == {'target_hits': 1, 'recipients_sent': 0, 'recipients_failed': 0}
    assert calls == [('mark_suggestions_target_hit', (5,))]


def test_does_not_mark_hit_when_every_send_fails():
    # Recipients existed but nobody actually got the email (e.g. a
    # transient ZeptoMail outage) -- must NOT mark as notified, so
    # tomorrow's run retries instead of losing it forever.
    calls = []
    app = _build_test_app(
        hits=[{'id': 9}],
        recipients=[{'email': 'a@x.com', 'name': 'A'}],
        send_results={'a@x.com': (False, 'connection refused')},
        calls=calls,
    )
    response = app.test_client().post('/stocks/suggestions/notify-target-hits')

    body = response.get_json()
    assert body == {'target_hits': 1, 'recipients_sent': 0, 'recipients_failed': 1}
    assert calls == []


def test_no_hits_does_nothing():
    calls = []
    app = _build_test_app(hits=[], recipients=[], send_results={}, calls=calls)
    response = app.test_client().post('/stocks/suggestions/notify-target-hits')

    body = response.get_json()
    assert body == {'target_hits': 0, 'recipients_sent': 0, 'recipients_failed': 0}
    assert calls == []
