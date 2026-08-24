"""Per-recipient delivery log for the three recommendation broadcast
emails (daily Pick of the Day, Starters weekly, large-cap bonus) -- see
utils/suggestion_email.py's send_daily_suggestions_email/
send_weekly_starters_email/send_large_cap_bonus_email, which each already
computed a sent/failed/failures summary per call but never persisted it
anywhere: the cron-triggered daily send only returns that summary as the
GitHub Actions workflow's own HTTP response body (see routes.py's
_dispatch_stocks_job), which nothing in this app can read back later.
This is what makes "who got today's email and who didn't, and why"
actually queryable from a page instead of only visible in a GitHub Actions
log the app itself has no access to.

One row per send ATTEMPT, not one row per (source, suggestion_date,
recipient) kept up to date -- a manual resend (see app.py's
/stocks/suggestions/resend) creates a new row rather than overwriting the
original attempt, so the full history (e.g. "failed at 9am, succeeded on
resend at 11am") is never lost. get_delivery_log below collapses this down
to the LATEST attempt per recipient for the summary view, the same
DISTINCT-ON-then-most-recent pattern get_suggestions already uses for
collapsing multiple stock_suggestions rows down to one per day."""
STOCK_EMAIL_DELIVERIES_TABLE_SQL = [
    '''CREATE TABLE IF NOT EXISTS stock_email_deliveries (
        id BIGSERIAL PRIMARY KEY,
        source TEXT NOT NULL CHECK (source IN ('daily', 'starters', 'large_cap')),
        suggestion_date DATE NOT NULL,
        recipient_id BIGINT REFERENCES stocks_admin_users(id),
        email TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('sent', 'failed')),
        error_detail TEXT,
        sent_at TIMESTAMPTZ DEFAULT NOW()
    )'''
]


def initialize_email_delivery_log_table_if_needed(client):
    for sql in STOCK_EMAIL_DELIVERIES_TABLE_SQL:
        try:
            client.rpc('execute_sql', {'query': sql}).execute()
        except Exception as e:
            print(f'Email delivery log table init warning (may already exist): {e}')


def record_delivery(db, source, suggestion_date, recipient_id, email, status, error_detail=None):
    """Logs one send attempt -- called once per recipient, right after
    utils.stock_alerting.send_zeptomail_stocks_email returns, by each of
    the three broadcast-email functions in suggestion_email.py. Never
    raises past a caller: a logging failure shouldn't take an otherwise-
    successful email send down with it (same "never let bookkeeping break
    the real thing it's tracking" reasoning as
    utils.background_jobs._make_progress_reporter)."""
    try:
        db.execute(
            '''INSERT INTO stock_email_deliveries
                   (source, suggestion_date, recipient_id, email, status, error_detail)
               VALUES (?, ?, ?, ?, ?, ?)''',
            (source, suggestion_date, recipient_id, email, status, error_detail)
        )
        db.commit()
    except Exception as e:
        print(f'Email delivery log write failed for {email} ({source}, {suggestion_date}): {e}')


def list_delivery_dates(db, limit=30):
    """{(source, suggestion_date): {'sent', 'failed'}} counts for the most
    recent `limit` distinct (source, suggestion_date) combinations logged,
    most recent first -- powers the picker on the delivery log page.
    Returns a list of dicts (not the dict above) so callers get a stable
    order: [{'source', 'suggestion_date', 'sent', 'failed'}, ...]."""
    rows = db.execute(
        '''SELECT source, suggestion_date,
                  SUM(CASE WHEN status='sent' THEN 1 ELSE 0 END) AS sent_count,
                  SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed_count,
                  MAX(sent_at) AS last_attempt_at
           FROM stock_email_deliveries
           GROUP BY source, suggestion_date
           ORDER BY suggestion_date DESC, last_attempt_at DESC
           LIMIT ?''',
        (limit,)
    ).fetchall()
    return [
        {
            'source': r['source'], 'suggestion_date': r['suggestion_date'],
            'sent': r.get('sent_count') or 0, 'failed': r.get('failed_count') or 0,
            'last_attempt_at': r.get('last_attempt_at'),
        }
        for r in rows
    ]


def get_delivery_log(db, source, suggestion_date):
    """Every recipient's LATEST send attempt for this (source,
    suggestion_date), newest attempt per recipient winning on a tie (a
    resend after an earlier failure shows the resend's outcome, not the
    original failure) -- name joined in from stocks_admin_users for
    display. Returns [{'recipient_id', 'email', 'name', 'status',
    'error_detail', 'sent_at'}, ...], failed rows first (the ones an admin
    actually needs to act on), then alphabetically by email."""
    rows = db.execute(
        '''SELECT DISTINCT ON (d.recipient_id, d.email) d.recipient_id, d.email, d.status,
                  d.error_detail, d.sent_at, u.name
           FROM stock_email_deliveries d
           LEFT JOIN stocks_admin_users u ON u.id = d.recipient_id
           WHERE d.source = ? AND d.suggestion_date = ?
           ORDER BY d.recipient_id, d.email, d.sent_at DESC''',
        (source, suggestion_date)
    ).fetchall()
    rows = list(rows)
    rows.sort(key=lambda r: (r['status'] != 'failed', (r.get('email') or '').lower()))
    return rows
