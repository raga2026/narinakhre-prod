import os
from datetime import datetime, timedelta, timezone

import requests

from stoqbell.utils.stock_auth import build_unsubscribe_url

# Deliberately reuses the storefront's own "support" Zeptomail Mail Agent's
# TOKEN (SMTP_SUPPORT_EMAIL_PASSWORD, see app.py's send_contact_email)
# rather than a separate Mail Agent/API key -- an earlier version of this
# module used its own credentials, but that var was never actually set up
# on Render, which meant every Stocks email (daily suggestions, viewer
# welcome emails, job-failure alerts) failed outright, regardless of what
# the "from" address was set to. Only the FROM ADDRESS is Stocks-specific
# (STOCKS_SMTP_FROM_EMAIL, defaulting to support-noreply@stoqbell.com) --
# the same "support" Mail Agent now has the stoqbell.com domain verified
# under it too (added 2026-08-21), so its one token can send as either
# support-noreply@narinakhre.com (storefront) or support-noreply@stoqbell.com
# (Stocks) depending on which FROM address a given send uses; no second
# Mail Agent to provision. Only reads env var NAMES app.py already reads
# for the shared token -- doesn't import from app.py, no circular import.
# The .in region endpoint is hardcoded (a fixed public endpoint, not a
# secret) rather than read from ZEPTOMAIL_API_URL, same reasoning as
# before.
STOCKS_ZEPTOMAIL_API_URL = 'https://api.zeptomail.in/v1.1/email'
ALERT_DEDUP_WINDOW_HOURS = 6

IST = timezone(timedelta(hours=5, minutes=30))

# Expected-by hour (IST) per cron-triggered route, and a human label for
# the email body. Hardcoded rather than configurable since these are tied
# to this app's actual daily job schedule (see the matching times in
# .github/workflows/stocks-*.yml) and would need updating together with
# those anyway. Derived from the real scheduled times, each with roughly an
# hour of buffer for the job to actually finish running:
#   shortlist_refresh      23:45 UTC = 05:15 IST -> expect done by 06:00 IST
#                          (fast DB-only job, no scraping -- runs first, so
#                          stock_watchlist is current before price_sync and
#                          indicator_calc read it; before market open 09:15 IST.
#                          Also runs a SECOND time at 06:00 UTC = 11:30 IST,
#                          just after fundamentals_rotation below finishes, to
#                          pick up that day's freshly-scraped fundamentals
#                          same-day rather than waiting for tomorrow's early
#                          run -- only the early run above is alerted on here,
#                          since that's the one the market-open pipeline
#                          actually depends on.)
#   price_sync             00:30 UTC = 06:00 IST -> expect done by 07:00 IST
#   indicator_calc         01:15 UTC = 06:45 IST -> expect done by 08:00 IST
#                          (runs after price_sync, needs that day's prices)
#   suggestion_email       02:00 UTC = 07:30 IST -> expect done by 09:00 IST
#                          (runs after indicator_calc, needs today's
#                          cross_status/volume_trend/rsi_14 to score against;
#                          rounded to a whole hour like the others above)
#   fundamentals_rotation  05:30 UTC = 11:00 IST -> expect done by 12:00 IST
#   market_cap_filter      07:00 UTC = 12:30 IST -> expect done by 13:00 IST
#                          (no workflow existed for this route before the
#                          alerting task -- added stocks-market-cap-filter.yml
#                          alongside it so there's actually something to
#                          check against)
#   subscription_reminders 04:00 UTC = 09:30 IST -> expect done by 10:30 IST
#                          (billing-related, not price/market-data-related --
#                          runs every day including weekends/holidays, unlike
#                          suggestion_email above which skips non-trading days;
#                          see utils/trading_calendar.py and
#                          stocks-subscription-reminders.yml)
#   trial_ended_notify     02:10 UTC = 07:40 IST -> expect done by 09:00 IST
#                          (free-trial-related, same "every calendar day"
#                          reasoning as subscription_reminders above; see
#                          utils/stocks_subscription.find_expired_trials and
#                          stocks-trial-ended-notify.yml)
#   auto_trade_reconcile   01:30 UTC = 07:00 IST -> expect done by 08:00 IST
#                          (dry-run only, see utils/auto_trader.py -- runs
#                          every day regardless of whether the simulation
#                          is actually enabled, always a "success" either
#                          way, so this is safe to alert on unconditionally)
#   stock_news_sync        00:00 UTC = 05:30 IST -> expect done by 07:00 IST
#                          (Google News RSS per watchlist company, see
#                          utils/stock_news.py and stocks-news-sync.yml --
#                          no price/fundamentals dependency, runs every
#                          calendar day, same "always a genuine success"
#                          reasoning as auto_trade_reconcile above)
JOB_EXPECTATIONS = {
    'shortlist_refresh': {'expected_by_ist_hour': 6, 'label': '6 AM IST'},
    'price_sync': {'expected_by_ist_hour': 7, 'label': '7 AM IST'},
    'auto_trade_reconcile': {'expected_by_ist_hour': 8, 'label': '8 AM IST'},
    'indicator_calc': {'expected_by_ist_hour': 8, 'label': '8 AM IST'},
    'suggestion_email': {'expected_by_ist_hour': 9, 'label': '9 AM IST'},
    # Runs daily (same cron as suggestion_email) but only does real work on
    # a Monday -- the job body itself checks the weekday and calls
    # record_job_success either way (skip-but-record-success on the other
    # 6 days, same pattern suggestion_email already uses for non-trading
    # days), so this needs no day-of-week awareness here: a genuine
    # failure on a Monday still gets caught, and the other 6 days are
    # never flagged as missed since success is recorded every day
    # regardless (see app.py's /stocks/starters/send-weekly-email).
    'starters_weekly_email': {'expected_by_ist_hour': 9, 'label': '9 AM IST'},
    # Standard-tier (Rs 299/mo) bonus large-cap pick -- runs daily (same
    # cron as suggestion_email) but only does real work on a Tuesday or
    # Friday trading day (see app.py's LARGE_CAP_BONUS_WEEKDAYS and
    # /stocks/large-cap-bonus/send-email), recording success on every
    # other day too -- same reasoning as starters_weekly_email above, just
    # two qualifying weekdays instead of one.
    'large_cap_bonus_email': {'expected_by_ist_hour': 9, 'label': '9 AM IST'},
    # Checks stock_suggestions against the day's synced close and emails
    # every current Standard-plan subscriber about any target hit -- see
    # app.py's /stocks/suggestions/notify-target-hits. Runs every day
    # (there's no "not a trading day" skip written into that job the way
    # suggestion_email has -- if nothing hit target that day it still
    # completes normally with target_hits=0, which is a genuine success,
    # not a skip).
    'suggestion_target_hit_notify': {'expected_by_ist_hour': 9, 'label': '9 AM IST'},
    'suggestion_projection_target_hit_notify': {'expected_by_ist_hour': 9, 'label': '9 AM IST'},
    'trial_ended_notify': {'expected_by_ist_hour': 9, 'label': '9 AM IST'},
    'subscription_reminders': {'expected_by_ist_hour': 11, 'label': '11 AM IST'},
    'fundamentals_rotation': {'expected_by_ist_hour': 12, 'label': '12 PM IST'},
    'market_cap_filter': {'expected_by_ist_hour': 13, 'label': '1 PM IST'},
    # Google News RSS fetch for the watchlist (see utils/stock_news.py) --
    # no price/fundamentals dependency, so it's scheduled independently
    # rather than chained after price_sync/indicator_calc like those are.
    'stock_news_sync': {'expected_by_ist_hour': 7, 'label': '7 AM IST'},
    # Runs daily (see stocks-activation-reminders.yml) but only does real
    # work on a Monday -- same skip-but-record-success pattern as
    # starters_weekly_email above (see
    # app.py's /stocks/notifications/send-activation-reminders).
    'activation_reminders': {'expected_by_ist_hour': 9, 'label': '9 AM IST'},
}

STOCK_ALERTING_TABLES_SQL = [
    '''CREATE TABLE IF NOT EXISTS stock_alerts (
        id BIGSERIAL PRIMARY KEY,
        alert_type TEXT NOT NULL CHECK (alert_type IN ('job_error', 'job_missed')),
        source TEXT NOT NULL,
        detail TEXT,
        sent_at TIMESTAMPTZ DEFAULT NOW(),
        resolved BOOLEAN DEFAULT false
    )''',
    '''CREATE TABLE IF NOT EXISTS stock_job_runs (
        source TEXT PRIMARY KEY,
        last_success_at TIMESTAMPTZ
    )''',
]


def initialize_stock_alerting_tables_if_needed(client):
    for sql in STOCK_ALERTING_TABLES_SQL:
        try:
            client.rpc('execute_sql', {'query': sql}).execute()
        except Exception as e:
            print(f'Stock alerting table init warning (may already exist): {e}')


def send_zeptomail_stocks_email(to_email, to_name, subject, textbody, htmlbody=None,
                                 sender_name='StoqBell Alerts', include_unsubscribe=True):
    """Low-level Zeptomail HTTP API sender shared by every Stocks-module
    email -- job-failure alerts (send_alert_email below) and the daily
    suggestions email (utils/suggestion_email.py) both go through this, so
    there's exactly one place that builds the request. Same request shape,
    same credentials, and same verified sender identity as the storefront's
    own send_contact_email() in app.py -- see the module comment above for
    why this reuses that context instead of a separate one.

    include_unsubscribe=True (the default) appends a signed one-click-
    unsubscribe link (see utils/stock_auth.build_unsubscribe_url) to the
    bottom of every CUSTOMER-facing send -- deliberately opt-OUT rather
    than opt-in, so nobody has to remember to add it to any new
    subscriber-facing email function later. The handful of INTERNAL sends
    (job-failure alerts, Raghav's own auto-trader/admin-notification
    emails -- see each of those callers in utils/suggestion_email.py)
    explicitly pass include_unsubscribe=False, since "unsubscribe" makes
    no sense on an email to the site owner about their own business.

    Returns (success: bool, detail: str) -- never raises, since a failed
    email shouldn't itself crash whatever else was happening (a failing
    job, or a batch of suggestion emails where one recipient's address
    bounces). detail is 'ok' on success, otherwise the actual reason
    (missing config, Zeptomail's own HTTP error, or a network error) --
    this used to be swallowed into a server-side print() only, which meant
    a caller-facing failure message (e.g. the "welcome email failed to
    send" flash on /stocks/users) could say nothing more specific than
    "check the Zeptomail config", even when the real cause was sitting
    right here the whole time."""
    api_key = os.environ.get('SMTP_SUPPORT_EMAIL_PASSWORD', '')
    # Stocks-specific sender -- must be a sender Zeptomail has actually
    # verified under the token above (the stoqbell.com domain was added to
    # the same "support" Mail Agent, see the module comment above), or the
    # send gets rejected regardless of the address looking plausible.
    from_email = os.environ.get('STOCKS_SMTP_FROM_EMAIL', 'support-noreply@stoqbell.com')

    if not api_key or not from_email or not to_email:
        missing = [
            name for name, value in [
                ('SMTP_SUPPORT_EMAIL_PASSWORD', api_key),
                ('STOCKS_SMTP_FROM_EMAIL', from_email),
                ('recipient address', to_email),
            ] if not value
        ]
        detail = f"Missing: {', '.join(missing)}."
        print(f'Stock email skipped: {detail}')
        return False, detail

    if include_unsubscribe:
        unsubscribe_url = build_unsubscribe_url(to_email)
        textbody = f'{textbody}\n\n--\nUnsubscribe from these emails: {unsubscribe_url}\n'
        if htmlbody:
            htmlbody = (
                f'{htmlbody}<p style="color:#94a3b8;font-size:11px;margin-top:20px;'
                f'font-family:Arial,Helvetica,sans-serif;text-align:center;">'
                f'<a href="{unsubscribe_url}" style="color:#94a3b8;">Unsubscribe</a> from these emails</p>'
            )

    payload = {
        'from': {'address': from_email, 'name': sender_name},
        'to': [{'email_address': {'address': to_email, 'name': to_name or to_email}}],
        'subject': subject,
        'textbody': textbody,
    }
    if htmlbody:
        payload['htmlbody'] = htmlbody

    headers = {
        'Authorization': f'Zoho-enczapikey {api_key}',
        'Content-Type': 'application/json',
        'Accept': 'application/json',
    }

    try:
        resp = requests.post(STOCKS_ZEPTOMAIL_API_URL, json=payload, headers=headers, timeout=10)
        if resp.status_code in (200, 201):
            return True, 'ok'
        detail = f'Zeptomail HTTP {resp.status_code}: {resp.text[:300]}'
        print(f'Stock email failed: {detail}')
        return False, detail
    except Exception as e:
        detail = f'{type(e).__name__}: {e}'
        print(f'Stock email failed: {detail}')
        return False, detail


def send_alert_email(subject, body):
    """Sends a job-failure/missed alert to the fixed STOCKS_ALERT_TO_EMAIL
    address -- see send_zeptomail_stocks_email for the actual HTTP call.
    Kept as its own function (rather than every call site reading
    STOCKS_ALERT_TO_EMAIL itself) since alert_job_error/alert_job_missed
    below both call this with just a subject/body, same as before this was
    refactored to share the low-level sender."""
    to_email = os.environ.get('STOCKS_ALERT_TO_EMAIL', '')
    if not to_email:
        print('Stock alert email skipped: STOCKS_ALERT_TO_EMAIL must be set.')
        return False
    ok, detail = send_zeptomail_stocks_email(to_email, to_email, subject, body, include_unsubscribe=False)
    if not ok:
        print(f'Stock alert email failed: {detail}')
    return ok


def _parse_timestamp(value):
    """stock_job_runs.last_success_at comes back from the Supabase RPC
    bridge as an ISO8601 string, not a native datetime -- normalizes either
    into a timezone-aware datetime for comparison."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _has_recent_unresolved_alert(db, alert_type, source):
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=ALERT_DEDUP_WINDOW_HOURS)).isoformat()
    existing = db.execute(
        '''SELECT id FROM stock_alerts
           WHERE alert_type=? AND source=? AND resolved=false AND sent_at >= ?
           LIMIT 1''',
        (alert_type, source, cutoff)
    ).fetchone()
    return existing is not None


def alert_job_error(db, source, error_detail):
    """Logs a job_error alert and emails immediately, UNLESS an unresolved
    job_error alert for the same source was already logged within the last
    ALERT_DEDUP_WINDOW_HOURS -- avoids spamming on retries/repeated
    failures of the same underlying problem. Never raises -- called from
    the except block of an already-failing route; a second failure here
    must not replace or mask the original error response."""
    try:
        if _has_recent_unresolved_alert(db, 'job_error', source):
            print(f'Stock alert suppressed (recent unresolved job_error already logged for {source}): {error_detail}')
            return

        now_str = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
        subject = f'[StoqBell] {source} failed'
        body = f'Job: {source}\nTime: {now_str}\nError: {error_detail}\n'
        send_alert_email(subject, body)

        db.execute(
            "INSERT INTO stock_alerts (alert_type, source, detail) VALUES ('job_error', ?, ?)",
            (source, error_detail)
        )
        db.commit()
    except Exception as e:
        print(f'alert_job_error itself failed for {source}: {type(e).__name__}: {e}')


def alert_job_missed(db, source, expected_by):
    """Same dedup logic as alert_job_error, for a job that didn't complete
    successfully by its expected time today (see check_missed_jobs).
    Never raises, same reasoning as alert_job_error."""
    try:
        if _has_recent_unresolved_alert(db, 'job_missed', source):
            return

        now_str = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
        detail = f'{source} did not complete successfully by {expected_by} today (checked {now_str}).'
        subject = f'[StoqBell] {source} did not run'
        body = f'Job: {source}\nExpected by: {expected_by}\nChecked at: {now_str}\nStatus: did not run / did not complete successfully today\n'
        send_alert_email(subject, body)

        db.execute(
            "INSERT INTO stock_alerts (alert_type, source, detail) VALUES ('job_missed', ?, ?)",
            (source, detail)
        )
        db.commit()
    except Exception as e:
        print(f'alert_job_missed itself failed for {source}: {type(e).__name__}: {e}')


def get_last_success_at(db, source):
    """Returns stock_job_runs.last_success_at for source (a timezone-aware
    datetime), or None if it's never recorded a successful run. Read-only
    counterpart to record_job_success -- used by the Stocks dashboard to
    show "Last synced: ..." for jobs that run on a cron schedule only (e.g.
    fundamentals_rotation), which have no admin-triggered button to poll
    for a live result."""
    row = db.execute(
        'SELECT last_success_at FROM stock_job_runs WHERE source=?',
        (source,)
    ).fetchone()
    return _parse_timestamp(row['last_success_at']) if row and row.get('last_success_at') else None


def record_job_success(db, source):
    """Upserts last_success_at=NOW() for source into stock_job_runs --
    called on the success path of every cron-triggered route (in addition
    to, not instead of, their existing response), read by
    check_missed_jobs() to tell whether that job actually ran today. Never
    raises -- a failure to record success must not turn a genuinely
    successful job into an error response."""
    try:
        db.execute(
            '''INSERT INTO stock_job_runs (source, last_success_at) VALUES (?, NOW())
               ON CONFLICT (source) DO UPDATE SET last_success_at = NOW()''',
            (source,)
        )
        db.commit()
    except Exception as e:
        print(f'record_job_success failed for {source}: {type(e).__name__}: {e}')


def job_ran_today(db, source, now=None):
    """True if stock_job_runs.source has a recorded successful run since
    today's IST midnight -- the same "ran_today" check check_missed_jobs
    applies per JOB_EXPECTATIONS entry, pulled out standalone so a single
    job can be checked outside the once-a-day missed-jobs sweep. Used by
    the recommendation tracker page to show whether today's automatic Pick
    of the Day email has gone out yet, and as a guard so the automatic cron
    (/stocks/suggestions/send-daily-email) and the tracker page's manual
    "send today's recommendation" button (/stocks/recommendations/tracker/
    send-today) never both send the same day's email."""
    now = now or datetime.now(timezone.utc)
    now_ist = now.astimezone(IST)
    today_start_ist = now_ist.replace(hour=0, minute=0, second=0, microsecond=0)
    row = db.execute(
        'SELECT last_success_at FROM stock_job_runs WHERE source=?',
        (source,)
    ).fetchone()
    last_success = _parse_timestamp(row['last_success_at']) if row and row.get('last_success_at') else None
    return last_success is not None and last_success.astimezone(IST) >= today_start_ist


def check_missed_jobs(db, now=None):
    """For each job in JOB_EXPECTATIONS, checks whether it has a recorded
    successful run (stock_job_runs.last_success_at) since today's IST
    midnight. Only flags a job once its expected-by hour (IST) has actually
    passed -- calling this earlier in the day would flag jobs that simply
    haven't had their turn yet. Calls alert_job_missed() for anything that's
    overdue and hasn't run. Meant to run once, late in the day -- see
    /stocks/alerts/check-missed-jobs and its GitHub Actions workflow.

    now defaults to the real current time; tests pass a fixed datetime
    instead so cases can be deterministic without monkeypatching the
    datetime module itself (which breaks _parse_timestamp's own
    isinstance(value, datetime) check, since that would replace datetime
    with a Mock rather than a real type)."""
    now = now or datetime.now(timezone.utc)
    now_ist = now.astimezone(IST)

    checked = []
    missed = []

    for source, expectation in JOB_EXPECTATIONS.items():
        if now_ist.hour < expectation['expected_by_ist_hour']:
            continue

        checked.append(source)
        if not job_ran_today(db, source, now=now):
            missed.append(source)
            alert_job_missed(db, source, expected_by=expectation['label'])

    return {'checked': checked, 'missed': missed}
