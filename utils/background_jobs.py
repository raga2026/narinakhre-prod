"""Runs a long admin-triggered action (price sync, fundamentals fetch,
shortlist refresh, Kite instrument map sync, suggestion email) on a
background thread instead of blocking the request that triggered it.

Why this exists: these actions call external APIs (Kite, Screener.in) once
per symbol, sequentially -- with 80+ watchlist rows that can take well over
a minute. Render runs this app under a single gunicorn worker (see
render.yaml), so a long synchronous request doesn't just make its own
button spin -- it blocks every other request against the same process,
including a plain page view like /stocks/watchlist that only reads data
already sitting in the database. Moving the actual work onto a background
thread lets the HTTP request that triggered it return immediately, freeing
the worker to keep serving other pages while the job runs.

Cron-triggered runs (GitHub Actions, via X-Cron-Secret) deliberately do NOT
go through this -- see each route in app.py. Those need to run
synchronously so alert_job_error/record_job_success (utils/stock_alerting.py)
still fire within the same request the workflow is watching for
success/failure. Background jobs are for the dashboard's own buttons only.
"""
import json
import threading
from datetime import datetime, timedelta, timezone

from utils.job_progress import bind as bind_progress, clear as clear_progress

# If a 'running' row is older than this with no update, the process that
# was running it almost certainly died (a Render restart/deploy/OOM kill --
# this daemon thread has no way to survive that, and nothing else was ever
# going to transition its row out of 'running') rather than still
# genuinely being in progress. Without this, start_background_job's dedup
# check below would refuse to ever start that job again -- exactly what
# happened in production (Super Sync stuck showing "running" since the
# previous day, with no way to retry it from the dashboard). Generous on
# purpose: even the slowest job here (Super Sync's ~1,067-symbol universe
# step) normally finishes in minutes, not hours.
STALE_JOB_MINUTES = 60

STOCK_BACKGROUND_JOBS_TABLE_SQL = [
    '''CREATE TABLE IF NOT EXISTS stock_background_jobs (
        id BIGSERIAL PRIMARY KEY,
        job_name TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'done', 'error')),
        result_json TEXT,
        error_message TEXT,
        started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        finished_at TIMESTAMP,
        triggered_by BIGINT REFERENCES stocks_admin_users(id)
    )'''
]

# Added after stock_background_jobs already had data in it -- see
# utils/job_progress.py for how these get populated (best-effort, throttled
# writes from the job's own thread while it runs) and consumed by
# get_job_status below for the dashboard's percentage-complete ticker.
STOCK_BACKGROUND_JOBS_ALTER_SQL = [
    'ALTER TABLE stock_background_jobs ADD COLUMN IF NOT EXISTS progress_current INTEGER',
    'ALTER TABLE stock_background_jobs ADD COLUMN IF NOT EXISTS progress_total INTEGER',
    'ALTER TABLE stock_background_jobs ADD COLUMN IF NOT EXISTS progress_label TEXT',
]


def initialize_background_jobs_table_if_needed(client):
    for sql in STOCK_BACKGROUND_JOBS_TABLE_SQL + STOCK_BACKGROUND_JOBS_ALTER_SQL:
        try:
            client.rpc('execute_sql', {'query': sql}).execute()
        except Exception as e:
            print(f'Background jobs table init warning (may already exist): {e}')


def _make_progress_reporter(db, job_id):
    """Best-effort -- a progress write failing must never take the actual
    job down with it, so any error here is just logged and swallowed."""
    def _report(current, total, label):
        try:
            db.execute(
                'UPDATE stock_background_jobs SET progress_current=?, progress_total=?, progress_label=? WHERE id=?',
                (current, total, label, job_id)
            )
            db.commit()
        except Exception as e:
            print(f'Progress update failed for background job {job_id}: {e}')
    return _report


def _run_and_record(build_db, job_id, target_fn):
    """Runs on the background thread -- builds its own DB handle (Flask's
    request-scoped get_db() isn't usable outside a request), runs the job,
    and records the outcome. Never lets an exception escape the thread
    (an uncaught exception in a background thread is just silently dropped
    by Python, which would leave the job stuck showing 'running' forever)."""
    db = build_db()
    bind_progress(_make_progress_reporter(db, job_id))
    try:
        summary = target_fn(db)
        db.execute(
            "UPDATE stock_background_jobs SET status='done', result_json=?, finished_at=NOW() WHERE id=?",
            (json.dumps(summary), job_id)
        )
        db.commit()
    except Exception as e:
        try:
            db.execute(
                "UPDATE stock_background_jobs SET status='error', error_message=?, finished_at=NOW() WHERE id=?",
                (str(e), job_id)
            )
            db.commit()
        except Exception as inner:
            print(f'background job {job_id} failed AND failed to record the failure: {inner}')
        print(f'Background job {job_id} failed: {type(e).__name__}: {e}')
    finally:
        clear_progress()


def _parse_timestamp(value):
    """started_at comes back from the Supabase RPC bridge as an ISO8601
    string, not a native datetime -- same normalization as
    utils/stock_alerting.py's own _parse_timestamp."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _mark_errored(db, job_id, message):
    db.execute(
        "UPDATE stock_background_jobs SET status='error', error_message=?, finished_at=NOW() WHERE id=?",
        (message, job_id)
    )
    db.commit()


def start_background_job(db, build_db, job_name, target_fn, triggered_by_id):
    """Starts target_fn(db) on a background thread under job_name, unless
    a job with that name is already running -- returns
    {'started': bool, 'status': 'running'|..., 'job_id': int}.
    'started' is False (no new thread) if one was already in flight; the
    caller should treat that the same as a fresh start from the requester's
    point of view (there's a job running, come back for its result).

    A 'running' row older than STALE_JOB_MINUTES is treated as abandoned
    rather than genuinely in flight -- see that constant's comment -- and
    gets marked 'error' here so a fresh run can start instead of being
    blocked by it forever.

    db is the caller's request-scoped connection, used only for the
    dedup check and the initial INSERT -- build_db is a zero-arg callable
    the background thread uses to get its OWN connection once the request
    (and db) may no longer be alive by the time the thread actually runs."""
    existing = db.execute(
        "SELECT id, started_at FROM stock_background_jobs WHERE job_name=? AND status='running' LIMIT 1",
        (job_name,)
    ).fetchone()
    if existing:
        age = datetime.now(timezone.utc) - _parse_timestamp(existing['started_at'])
        if age <= timedelta(minutes=STALE_JOB_MINUTES):
            return {'started': False, 'status': 'running', 'job_id': existing['id']}
        _mark_errored(
            db, existing['id'],
            'Marked as stale/abandoned -- the process that started it likely restarted mid-run.'
        )

    db.execute(
        'INSERT INTO stock_background_jobs (job_name, status, triggered_by) VALUES (?, ?, ?)',
        (job_name, 'running', triggered_by_id)
    )
    db.commit()
    row = db.execute(
        "SELECT id FROM stock_background_jobs WHERE job_name=? AND status='running' ORDER BY id DESC LIMIT 1",
        (job_name,)
    ).fetchone()
    job_id = row['id']

    thread = threading.Thread(
        target=_run_and_record, args=(build_db, job_id, target_fn), daemon=True
    )
    thread.start()

    # 'thread' is exposed only so tests can .join() it for a deterministic
    # wait -- the HTTP route ignores it and returns immediately either way.
    return {'started': True, 'status': 'running', 'job_id': job_id, 'thread': thread}


def cancel_job(db, job_name):
    """Immediately marks job_name's latest 'running' row as 'error' --
    the manual escape hatch for a stuck job, rather than waiting out
    STALE_JOB_MINUTES for start_background_job's own auto-recovery to kick
    in. Does NOT and cannot actually stop a real, still-executing Python
    thread (there's no interrupt mechanism here, and once one exists, the
    daemon thread dies with the process anyway) -- this only clears the
    dedup lock stopping a fresh run, and updates what the dashboard shows.
    If the underlying process is somehow still genuinely running (rather
    than the far more common case of it having already died when the
    server restarted), that thread will still finish and separately
    overwrite this row's status when it does. Returns True if a running
    row was found and cancelled, False if there was nothing to cancel."""
    row = db.execute(
        "SELECT id FROM stock_background_jobs WHERE job_name=? AND status='running' ORDER BY id DESC LIMIT 1",
        (job_name,)
    ).fetchone()
    if not row:
        return False
    _mark_errored(db, row['id'], 'Manually cancelled from the dashboard.')
    return True


def get_job_status(db, job_name):
    """Returns the most recent stock_background_jobs row for job_name as
    {'status': 'running'|'done'|'error'|'never_run', 'result': dict|None,
    'error': str|None, 'started_at': ..., 'finished_at': ...,
    'progress': {'current', 'total', 'label'}|None, 'last_success_at': ...}
    -- for the dashboard to poll after start_background_job. result_json is
    parsed back into a dict here so callers never touch the raw column.

    'progress' reflects the latest run regardless of its status (only
    meaningful while status is 'running' -- see utils/job_progress.py).
    'last_success_at' is deliberately looked up separately from the latest
    row: while a job is running, the latest row's own finished_at is still
    None, but the dashboard still wants to show when this job last actually
    completed -- i.e. the previous 'done' row, not this in-flight one."""
    row = db.execute(
        'SELECT status, result_json, error_message, started_at, finished_at, '
        'progress_current, progress_total, progress_label '
        'FROM stock_background_jobs WHERE job_name=? ORDER BY id DESC LIMIT 1',
        (job_name,)
    ).fetchone()
    if not row:
        return {
            'status': 'never_run', 'result': None, 'error': None, 'started_at': None,
            'finished_at': None, 'progress': None, 'last_success_at': None,
        }

    result = json.loads(row['result_json']) if row.get('result_json') else None

    progress = None
    if row.get('progress_total'):
        progress = {
            'current': row.get('progress_current') or 0,
            'total': row['progress_total'],
            'label': row.get('progress_label'),
        }

    last_success_row = db.execute(
        "SELECT finished_at FROM stock_background_jobs WHERE job_name=? AND status='done' ORDER BY id DESC LIMIT 1",
        (job_name,)
    ).fetchone()

    return {
        'status': row['status'],
        'result': result,
        'error': row.get('error_message'),
        'started_at': row.get('started_at'),
        'finished_at': row.get('finished_at'),
        'progress': progress,
        'last_success_at': last_success_row['finished_at'] if last_success_row else None,
    }
