from datetime import datetime as RealDatetime
from datetime import timezone
from unittest.mock import patch

from utils.stock_alerting import IST, JOB_EXPECTATIONS, alert_job_error, check_missed_jobs


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeAlertingDB:
    def __init__(self, job_runs=None):
        self.alerts = []  # list of dicts: alert_type, source, detail, sent_at, resolved
        self.job_runs = job_runs or {}  # source -> last_success_at (ISO string)

    def execute(self, sql, params=None):
        params = params or ()
        normalized = ' '.join(sql.split())

        if normalized.startswith('SELECT id FROM stock_alerts'):
            alert_type, source, cutoff = params
            matches = [
                a for a in self.alerts
                if a['alert_type'] == alert_type and a['source'] == source
                and not a['resolved'] and a['sent_at'] >= cutoff
            ]
            return FakeCursor([{'id': 1}] if matches else [])

        if normalized.startswith("INSERT INTO stock_alerts (alert_type, source, detail) VALUES ('job_error'"):
            source, detail = params
            self.alerts.append({
                'alert_type': 'job_error', 'source': source, 'detail': detail,
                'sent_at': RealDatetime.now(timezone.utc).isoformat(), 'resolved': False,
            })
            return FakeCursor([])

        if normalized.startswith("INSERT INTO stock_alerts (alert_type, source, detail) VALUES ('job_missed'"):
            source, detail = params
            self.alerts.append({
                'alert_type': 'job_missed', 'source': source, 'detail': detail,
                'sent_at': RealDatetime.now(timezone.utc).isoformat(), 'resolved': False,
            })
            return FakeCursor([])

        if normalized.startswith('SELECT last_success_at FROM stock_job_runs'):
            (source,) = params
            if source in self.job_runs:
                return FakeCursor([{'last_success_at': self.job_runs[source]}])
            return FakeCursor([])

        raise AssertionError(f'Unexpected SQL in test: {sql}')

    def commit(self):
        pass


def test_alert_job_error_sends_email_on_first_failure():
    db = FakeAlertingDB()

    with patch('utils.stock_alerting.send_alert_email') as mock_send:
        alert_job_error(db, 'price_sync', 'Kite API timeout')

    mock_send.assert_called_once()
    subject, body = mock_send.call_args[0]
    assert 'price_sync' in subject
    assert 'Kite API timeout' in body
    assert len(db.alerts) == 1


def test_alert_job_error_does_not_resend_within_6_hours_for_same_source():
    db = FakeAlertingDB()

    with patch('utils.stock_alerting.send_alert_email') as mock_send:
        alert_job_error(db, 'price_sync', 'first failure')
        alert_job_error(db, 'price_sync', 'second failure, same source, soon after')

    mock_send.assert_called_once()  # only the first call actually sent
    assert len(db.alerts) == 1


def test_alert_job_error_still_sends_for_a_different_source():
    db = FakeAlertingDB()

    with patch('utils.stock_alerting.send_alert_email') as mock_send:
        alert_job_error(db, 'price_sync', 'failure A')
        alert_job_error(db, 'indicator_calc', 'failure B')

    assert mock_send.call_count == 2


def test_check_missed_jobs_flags_a_job_with_no_success_record_today():
    # Fixed "now" well past every job's expected-by hour (IST), no
    # last_success_at recorded for anything.
    fixed_now = RealDatetime(2026, 8, 16, 16, 0, tzinfo=timezone.utc)  # 21:30 IST
    db = FakeAlertingDB(job_runs={})

    with patch('utils.stock_alerting.send_alert_email') as mock_send:
        summary = check_missed_jobs(db, now=fixed_now)

    all_sources = set(JOB_EXPECTATIONS.keys())
    assert set(summary['checked']) == all_sources
    assert set(summary['missed']) == all_sources
    assert mock_send.call_count == len(all_sources)
    assert len(db.alerts) == len(all_sources)
    assert all(a['alert_type'] == 'job_missed' for a in db.alerts)


def test_check_missed_jobs_does_not_flag_a_job_that_ran_today():
    fixed_now = RealDatetime(2026, 8, 16, 16, 0, tzinfo=timezone.utc)  # 21:30 IST, Aug 16
    # price_sync succeeded at 02:00 UTC = 07:30 IST the same day.
    db = FakeAlertingDB(job_runs={
        'price_sync': '2026-08-16T02:00:00+00:00',
    })

    with patch('utils.stock_alerting.send_alert_email') as mock_send:
        summary = check_missed_jobs(db, now=fixed_now)

    assert 'price_sync' not in summary['missed']
    # Everything else still has no success record -- still flagged.
    assert 'indicator_calc' in summary['missed']
    assert mock_send.call_count == len(JOB_EXPECTATIONS) - 1


def test_check_missed_jobs_ignores_a_success_from_a_previous_day():
    fixed_now = RealDatetime(2026, 8, 16, 16, 0, tzinfo=timezone.utc)
    # price_sync last succeeded the day before -- must not count as "ran today".
    db = FakeAlertingDB(job_runs={
        'price_sync': '2026-08-15T02:00:00+00:00',
    })

    with patch('utils.stock_alerting.send_alert_email'):
        summary = check_missed_jobs(db, now=fixed_now)

    assert 'price_sync' in summary['missed']


def test_check_missed_jobs_skips_jobs_not_due_yet():
    # 05:00 UTC = 10:30 IST -- past price_sync/indicator_calc's deadlines,
    # but before fundamentals_rotation (12 PM) and market_cap_filter (1 PM).
    fixed_now = RealDatetime(2026, 8, 16, 5, 0, tzinfo=timezone.utc)
    db = FakeAlertingDB(job_runs={})

    with patch('utils.stock_alerting.send_alert_email'):
        summary = check_missed_jobs(db, now=fixed_now)

    assert 'fundamentals_rotation' not in summary['checked']
    assert 'market_cap_filter' not in summary['checked']
    assert 'price_sync' in summary['checked']
