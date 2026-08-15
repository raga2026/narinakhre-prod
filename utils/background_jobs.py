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


def initialize_background_jobs_table_if_needed(client):
    for sql in STOCK_BACKGROUND_JOBS_TABLE_SQL:
        try:
            client.rpc('execute_sql', {'query': sql}).execute()
        except Exception as e:
            print(f'Background jobs table init warning (may already exist): {e}')


def _run_and_record(build_db, job_id, target_fn):
    """Runs on the background thread -- builds its own DB handle (Flask's
    request-scoped get_db() isn't usable outside a request), runs the job,
    and records the outcome. Never lets an exception escape the thread
    (an uncaught exception in a background thread is just silently dropped
    by Python, which would leave the job stuck showing 'running' forever)."""
    db = build_db()
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


def start_background_job(db, build_db, job_name, target_fn, triggered_by_id):
    """Starts target_fn(db) on a background thread under job_name, unless
    a job with that name is already running -- returns
    {'started': bool, 'status': 'running'|..., 'job_id': int}.
    'started' is False (no new thread) if one was already in flight; the
    caller should treat that the same as a fresh start from the requester's
    point of view (there's a job running, come back for its result).

    db is the caller's request-scoped connection, used only for the
    dedup check and the initial INSERT -- build_db is a zero-arg callable
    the background thread uses to get its OWN connection once the request
    (and db) may no longer be alive by the time the thread actually runs."""
    existing = db.execute(
        "SELECT id FROM stock_background_jobs WHERE job_name=? AND status='running' LIMIT 1",
        (job_name,)
    ).fetchone()
    if existing:
        return {'started': False, 'status': 'running', 'job_id': existing['id']}

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


def get_job_status(db, job_name):
    """Returns the most recent stock_background_jobs row for job_name as
    {'status': 'running'|'done'|'error'|'never_run', 'result': dict|None,
    'error': str|None, 'started_at': ..., 'finished_at': ...} -- for the
    dashboard to poll after start_background_job. result_json is parsed
    back into a dict here so callers never touch the raw column."""
    row = db.execute(
        'SELECT status, result_json, error_message, started_at, finished_at '
        'FROM stock_background_jobs WHERE job_name=? ORDER BY id DESC LIMIT 1',
        (job_name,)
    ).fetchone()
    if not row:
        return {'status': 'never_run', 'result': None, 'error': None, 'started_at': None, 'finished_at': None}

    result = json.loads(row['result_json']) if row.get('result_json') else None
    return {
        'status': row['status'],
        'result': result,
        'error': row.get('error_message'),
        'started_at': row.get('started_at'),
        'finished_at': row.get('finished_at'),
    }
