"""All Nari Nakhre Stocks (StoqBell) routes, on their own Blueprint.

Registered onto the main Flask app in app.py via
`app.register_blueprint(stocks_bp)`. Route paths already include the
`/stocks` prefix in each decorator (no Blueprint url_prefix is used), so
every URL is byte-for-byte identical to before this module existed --
this file is purely a relocation, not a behavior change. The Blueprint's
only externally-visible effect is that every view function's endpoint
name gains a `stocks.` namespace (e.g. `stocks_home` -> `stocks.stocks_home`),
which is why every `url_for('stocks.stocks_...')` call site elsewhere (app.py,
the stocks templates) was updated to `url_for('stocks.stocks_...')`
alongside this file being introduced.
"""
import json
import os
import re
import time
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode

from flask import Blueprint, current_app, g, jsonify, redirect, render_template, request, session, url_for, flash

import auth_providers
from db import get_db, get_supabase, SupabaseDB
from razorpay_shared import get_razorpay_client, RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET
from recaptcha_shared import verify_recaptcha, STOCKS_RECAPTCHA_SITE_KEY, STOCKS_RECAPTCHA_SECRET_KEY

from stoqbell.utils.stock_ingestion import initialize_stock_tables_if_needed, sync_daily_data, sync_daily_data_universe
from stoqbell.utils.stock_auth import (
    initialize_stocks_auth_if_needed,
    authenticate_stocks_admin,
    stocks_login_required,
    stocks_role_required,
    stocks_watchlist_access_required,
    list_stocks_admin_users,
    create_child_admin,
    toggle_child_admin_active,
    list_viewers,
    create_viewer_account,
    toggle_viewer_active,
    toggle_viewer_pro,
    set_viewer_plan,
    delete_viewer_account,
    change_own_password,
    migrate_email_recipients_to_viewers,
    has_valid_cron_secret,
    legacy_stocks_redirect,
    find_stocks_account_by_google_sub,
    find_stocks_account_by_username,
    link_google_sub,
    create_pending_google_subscriber,
    safe_stocks_next_url,
    verify_and_apply_unsubscribe,
)
from stoqbell.utils.stocks_subscription import (
    SUBSCRIPTION_TOTAL_COUNT_MONTHS,
    create_pending_subscriber,
    attach_razorpay_subscription,
    activate_subscription,
    activate_trial,
    record_recurring_charge,
    mark_subscription_cancelled,
    mark_subscription_halted,
    find_expiring_subscribers,
    find_expired_trials,
    mark_trial_ended_email_sent,
    find_account_by_razorpay_subscription_id,
    mark_reminder_sent,
    has_stocks_access,
    days_until,
    verify_subscription_payment_signature,
    verify_webhook_signature,
)
from stoqbell.utils.stocks_referrals import (
    REFERRALS_PER_FREE_MONTH,
    apply_referral_credits_on_cancellation,
    available_referral_credits,
    count_qualified_referrals,
    find_referrer_by_code,
    get_or_create_referral_code,
)
from stoqbell.utils.starters_engine import (
    STARTERS_REPEAT_WINDOW_DAYS,
    generate_weekly_starters_pick,
    get_starters_suggestions,
    get_starters_suggestion_by_id,
    initialize_starters_suggestions_table_if_needed,
)
from stoqbell.utils.large_cap_engine import (
    LARGE_CAP_BONUS_REPEAT_WINDOW_DAYS,
    generate_large_cap_bonus_pick,
    get_large_cap_bonus_suggestions,
    get_large_cap_bonus_suggestion_by_id,
    initialize_large_cap_bonus_suggestions_table_if_needed,
)
from stoqbell.utils.saved_filters import (
    initialize_saved_filters_table_if_needed,
    list_saved_stock_filters,
    save_stock_filter,
    delete_saved_stock_filter,
)
from stoqbell.utils.auto_trader import (
    initialize_auto_trade_tables_if_needed,
    get_auto_trade_settings,
    set_auto_trade_settings,
    open_auto_trade_if_enabled,
    open_manual_trade,
    manual_close_trade,
    reconcile_open_trades,
    reconcile_pending_buys,
    reconcile_pending_sells,
    confirm_stop_loss_sell,
    cancel_stop_loss_sell,
    compute_pnl as compute_auto_trade_pnl,
    get_deployed_capital,
    compute_available_funds,
    list_auto_trades,
    DEFAULT_BUDGET_PER_TRADE as DEFAULT_AUTO_TRADE_BUDGET,
    DEFAULT_TOTAL_CAPITAL as DEFAULT_AUTO_TRADE_TOTAL_CAPITAL,
    MODE_DRY_RUN,
    MODE_LIVE,
    STOP_LOSS_ALERT_EMAIL,
)
from stoqbell.utils.kite_client import KiteClient, KiteClientError
from stoqbell.utils.kite_instrument_map import (
    initialize_kite_instrument_map_table_if_needed,
    sync_kite_instrument_map,
)
from stoqbell.utils.background_jobs import (
    initialize_background_jobs_table_if_needed,
    start_background_job,
    get_job_status,
    cancel_job,
)
from stoqbell.utils.kite_session import (
    initialize_kite_session_table_if_needed,
    get_kite_login_url,
    exchange_request_token,
    save_kite_access_token,
    get_kite_access_token,
    get_kite_session_status,
    IST,
)
from stoqbell.utils.kite_postback import (
    initialize_kite_postback_log_table_if_needed,
    verify_postback_checksum,
    log_postback,
)
from stoqbell.utils.fundamentals_ingestion import (
    initialize_fundamentals_table_if_needed,
    sync_fundamentals_rotation,
)
from stoqbell.utils.stock_universe import (
    initialize_stock_universe_table_if_needed,
    refresh_market_cap_filter,
    rebucket_large_cap_eligibility,
)
from stoqbell.utils.stock_shortlist import run_fundamental_shortlist, get_golden_cross_not_qualified, run_large_cap_shortlist
from stoqbell.utils.fundamental_screen import get_metric_note
from stoqbell.utils.watchlist_view import enrich_and_sort_watchlist_rows, redact_recommendation_signals
from stoqbell.utils.price_pattern import (
    compute_day_change,
    compute_52_week_range,
    trend_note,
    build_price_sparkline_svg,
    backtest_rsi_zone_outcomes,
    detect_rounding_pattern,
    compute_projection_targets,
)
from stoqbell.utils.suggestion_chart import build_prediction_chart_image_url
from stoqbell.utils.super_sync import run_super_sync
from stoqbell.utils.stock_indicators import (
    initialize_stock_indicators_table_if_needed,
    run_indicator_calculation,
    run_indicator_calculation_universe,
)
from stoqbell.utils.stock_alerting import (
    initialize_stock_alerting_tables_if_needed,
    alert_job_error,
    record_job_success,
    check_missed_jobs,
    get_last_success_at,
)
from stoqbell.utils.suggestion_engine import (
    initialize_stock_suggestions_table_if_needed,
    generate_daily_suggestions,
    get_suggestions,
    get_suggestion_by_id,
    find_pending_target_hit_suggestions,
    mark_suggestions_target_hit,
    passes_hard_filters,
    HOLDING_PERIOD_DAYS,
    get_recommendation_tracker,
    compute_tracker_row_stats,
    compute_watchlist_nns_scores,
    get_top_stocks,
    get_candidates_for_manual_pick,
    create_manual_suggestions,
    get_special_recommendations_today,
)
from stoqbell.utils.industry_growth import compute_industry_growth
from stoqbell.utils.suggestion_email import (
    initialize_stocks_email_recipients_table_if_needed,
    send_daily_suggestions_email,
    send_viewer_welcome_email,
    send_subscription_welcome_email,
    send_subscription_expiry_reminder_email,
    send_stop_loss_review_email,
    send_admin_new_subscriber_email,
    send_admin_subscription_cancelled_email,
    send_target_hit_email,
    send_target_achieved_email,
    send_trial_ended_email,
    send_trial_started_email,
    send_weekly_starters_email,
    send_large_cap_bonus_email,
    send_rebrand_announcement_to_all_viewers,
)
from stoqbell.utils.trading_calendar import is_trading_day, is_within_trading_hours
from stoqbell.utils.admin_alerts import (
    initialize_admin_alerts_table_if_needed,
    record_and_send_highly_recommended_alerts,
    find_and_notify_intraday_target_hits,
)


stocks_bp = Blueprint(
    'stocks', __name__,
    template_folder='templates',
    static_folder='static',
    static_url_path='/stocks/static',
)

# StoqBell's own domain -- see app.py's redirect_stocks_to_own_domain, which
# imports this same constant so the two stay in sync. Owned here (not
# app.py) since it's specifically Stocks' own branding, not a storefront
# concern.
STOCKS_DOMAIN = 'www.stoqbell.com'

# Used by /stocks/signup's password-based signup form to reject an
# obviously-malformed email before ever creating a row. Defined here
# (not imported from app.py, which has its own separate copies for the
# storefront's own forms) so stoqbell/ stays self-contained.
EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')

razorpay_client = get_razorpay_client()

# Nari Nakhre Stocks self-serve subscription (see stoqbell/utils/stocks_subscription.py,
# /stocks/signup, /stocks/subscribe/verify, /stocks/razorpay/webhook) --
# reuses the SAME Razorpay account/keys as the storefront's one-time-order
# checkout (see razorpay_shared.py), just against the Subscriptions API
# instead of Orders. RAZORPAY_STOCKS_PLAN_ID is a one-time setup value (the
# Rs 299/month Plan object's id, created once via razorpay_client.plan.create
# -- see the setup script, not created automatically on every app start
# since Plan creation has no natural idempotency check and would otherwise
# spam a new duplicate Plan into the account on every deploy).
# RAZORPAY_WEBHOOK_SECRET is a DIFFERENT secret than RAZORPAY_KEY_SECRET --
# it's whatever you set when adding the webhook URL in the Razorpay
# dashboard (Settings -> Webhooks), and must match exactly or every webhook
# signature check fails.
RAZORPAY_STOCKS_PLAN_ID = os.environ.get('RAZORPAY_STOCKS_PLAN_ID', '')
RAZORPAY_WEBHOOK_SECRET = os.environ.get('RAZORPAY_WEBHOOK_SECRET', '')
# Referral system (see stoqbell/utils/stocks_referrals.py) -- a referred
# signup's first cycle bills against THIS plan (Rs 199/month) instead of the
# regular one above; right after that first payment verifies,
# /stocks/subscribe/verify schedules a swap back to RAZORPAY_STOCKS_PLAN_ID
# for cycle 2 onward via subscription.edit(..., schedule_change_at='cycle_end').
# Same one-time-setup nature as RAZORPAY_STOCKS_PLAN_ID above.
RAZORPAY_STOCKS_REFERRAL_PLAN_ID = os.environ.get('RAZORPAY_STOCKS_REFERRAL_PLAN_ID', '')
STOCKS_SUBSCRIPTION_PRICE_DISPLAY = 'Rs 299'
STOCKS_REFERRAL_PRICE_DISPLAY = 'Rs 199'
# Starters tier (see STOCKS_AUTH_ALTER_SQL's stocks_plan column,
# stoqbell/utils/starters_engine.py) -- Rs 99/month, one separately-curated
# golden-tier-only pick a week instead of the daily one. Its own Plan
# object, same one-time-setup nature as the two above -- no discount
# variant exists for this tier (the referral discount stays Standard-only,
# see /stocks/signup).
RAZORPAY_STOCKS_STARTERS_PLAN_ID = os.environ.get('RAZORPAY_STOCKS_STARTERS_PLAN_ID', '')
STOCKS_STARTERS_PRICE_DISPLAY = 'Rs 99'
# Shared secret for every Stocks cron-triggered route (price sync, indicator
# calc, fundamentals rotation scrape, etc.) -- the caller is always a
# GitHub Actions workflow (.github/workflows/stocks-*.yml), not a Render
# Cron Job. Set the same value as the STOCKS_FUNDAMENTALS_CRON_SECRET repo
# secret on GitHub.
STOCKS_FUNDAMENTALS_CRON_SECRET = os.environ.get('STOCKS_FUNDAMENTALS_CRON_SECRET', '')


def init_stocks_tables():
    """Called once from app.py at startup, right after the storefront's own
    initialize_database_if_needed() -- same Supabase project, separate
    tables (see stoqbell/utils/stock_auth.py for Stocks' own admin login,
    not the storefront's)."""
    client = get_supabase()
    initialize_stock_tables_if_needed(client)
    initialize_stocks_auth_if_needed(client)
    initialize_kite_session_table_if_needed(client)
    initialize_kite_postback_log_table_if_needed(client)
    initialize_kite_instrument_map_table_if_needed(client)
    initialize_background_jobs_table_if_needed(client)
    initialize_fundamentals_table_if_needed(client)
    initialize_stock_universe_table_if_needed(client)
    initialize_stock_indicators_table_if_needed(client)
    initialize_stock_alerting_tables_if_needed(client)
    initialize_stock_suggestions_table_if_needed(client)
    initialize_stocks_email_recipients_table_if_needed(client)
    initialize_saved_filters_table_if_needed(client)
    initialize_auto_trade_tables_if_needed(client)
    initialize_starters_suggestions_table_if_needed(client)
    initialize_large_cap_bonus_suggestions_table_if_needed(client)
    initialize_admin_alerts_table_if_needed(client)


_LEGACY_STOCKS_ROUTES = [
    # (old path, new endpoint name, methods, redirect code)
    ('/admin/stocks/sync', 'stocks.admin_stocks_sync', ['POST'], 308),
    ('/admin/stocks/kite/login', 'stocks.stocks_kite_login', ['GET'], 301),
    ('/admin/stocks/kite/callback', 'stocks.stocks_kite_callback', ['GET'], 301),
    ('/admin/stocks/kite/postback', 'stocks.stocks_kite_postback', ['POST'], 308),
    ('/admin/stocks/watchlist', 'stocks.stocks_watchlist', ['GET'], 301),
    ('/admin/stocks/login', 'stocks.stocks_admin_login', ['GET', 'POST'], 308),
    ('/admin/stocks/logout', 'stocks.stocks_admin_logout', ['GET'], 301),
    ('/admin/stocks/dashboard', 'stocks.stocks_admin_dashboard', ['GET'], 301),
    ('/admin/stocks/admins', 'stocks.stocks_admin_manage', ['GET'], 301),
    ('/admin/stocks/admins/create', 'stocks.stocks_admin_create', ['POST'], 308),
    ('/admin/stocks/admins/<int:admin_id>/toggle', 'stocks.stocks_admin_toggle', ['POST'], 308),
]


def register_legacy_stocks_routes(app):
    """Registers the pre-URL-rename /admin/stocks/* redirects directly on
    the main app (not the blueprint) -- these are top-level legacy paths
    outside the Blueprint's own route set, kept only for old
    bookmarks/links. Called from app.py after stocks_bp is registered, so
    url_for() inside legacy_stocks_redirect can already resolve the
    'stocks.*' endpoint names above."""
    for _old_path, _new_endpoint, _methods, _code in _LEGACY_STOCKS_ROUTES:
        app.add_url_rule(
            _old_path,
            endpoint=f'legacy_{_new_endpoint.replace(".", "_")}',
            view_func=legacy_stocks_redirect(_new_endpoint, code=_code),
            methods=_methods,
        )


def _dispatch_stocks_job(db, is_cron, job_name, job_fn):
    """Shared trigger logic for the Stocks dashboard's background-eligible
    admin actions (price sync, fundamentals fetch, shortlist refresh, Kite
    instrument map sync, suggestion email). job_fn(db) -> a JSON-serializable
    summary dict, and may raise.

    A GitHub Actions cron run (is_cron=True) runs job_fn synchronously,
    exactly as before background jobs existed -- the workflow needs the
    real success/failure in this same response. A browser session
    (is_cron=False) instead runs job_fn on a background thread via
    utils/background_jobs.start_background_job and returns immediately, so
    the request that triggered it (and every other page view sharing this
    app's single gunicorn worker -- see render.yaml) doesn't sit blocked
    for however long the job takes. The dashboard polls
    /stocks/jobs/<job_name>/status for the result."""
    if is_cron:
        try:
            summary = job_fn(db)
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500
        return jsonify({'status': 'ok', **summary})

    result = start_background_job(
        db, build_db=lambda: SupabaseDB(get_supabase()), job_name=job_name,
        target_fn=job_fn, triggered_by_id=session.get('stocks_admin_id')
    )
    return jsonify({
        'status': 'started', 'job_name': job_name, 'job_id': result['job_id'],
        'already_running': not result['started'],
    })


@stocks_bp.route('/stocks/sync', methods=['POST'])
def admin_stocks_sync():
    """Manual trigger for Nari Nakhre Stocks Phase 1 ingestion -- pulls the
    latest daily candle (or backfills) for every active stock_watchlist row.
    See utils/stock_ingestion.py. Accepts either an active Stocks login
    session (manual trigger from the browser, e.g. the dashboard's "Sync
    now" button) or a valid X-Cron-Secret header (a scheduled GitHub Actions
    workflow) -- either one is sufficient. Same header name, env var, and
    check has_valid_cron_secret() uses everywhere else in this file, just
    added here as a second accepted path rather than replacing the session
    check @stocks_login_required did.

    Session-triggered runs happen on a background thread, not inline --
    see _dispatch_stocks_job / utils/background_jobs.py."""
    is_cron = has_valid_cron_secret(request.headers, STOCKS_FUNDAMENTALS_CRON_SECRET)
    if not is_cron and not session.get('stocks_admin_id'):
        return redirect(url_for('stocks.stocks_admin_login'))

    db = get_db()
    access_token = get_kite_access_token(db)
    if not access_token:
        return jsonify({
            'status': 'error',
            'message': 'No Kite session yet -- a super_admin must log in via /admin/stocks/kite/login first.'
        }), 400

    def _job(job_db):
        try:
            summary = sync_daily_data(job_db, kite_client=KiteClient(access_token=access_token))
        except Exception as e:
            current_app.logger.error(f'Stock sync failed: {e}')
            alert_job_error(job_db, 'price_sync', str(e))
            raise
        record_job_success(job_db, 'price_sync')
        return summary

    return _dispatch_stocks_job(db, is_cron, 'price_sync', _job)


@stocks_bp.route('/stocks/sync/universe', methods=['POST'])
def stocks_sync_universe():
    """Same as /stocks/sync, but pulls daily candles for the full
    ~1,067-company scrape-eligible stock_universe set instead of just the
    watchlist -- see utils/stock_ingestion.sync_daily_data_universe. This
    is what technical indicators need before
    /stocks/indicators/calculate/universe can find golden-cross companies
    outside the watchlist. Takes several minutes (paced to respect Kite's
    rate limit across ~1,067 calls), so this always runs as a background
    job regardless of trigger -- see _dispatch_stocks_job. Same dual auth
    as /stocks/sync."""
    is_cron = has_valid_cron_secret(request.headers, STOCKS_FUNDAMENTALS_CRON_SECRET)
    if not is_cron and not session.get('stocks_admin_id'):
        return redirect(url_for('stocks.stocks_admin_login'))

    db = get_db()
    access_token = get_kite_access_token(db)
    if not access_token:
        return jsonify({
            'status': 'error',
            'message': 'No Kite session yet -- a super_admin must log in via /admin/stocks/kite/login first.'
        }), 400

    def _job(job_db):
        return sync_daily_data_universe(job_db, kite_client=KiteClient(access_token=access_token))

    return _dispatch_stocks_job(db, is_cron=False, job_name='price_sync_universe', job_fn=_job)


@stocks_bp.route('/stocks/super-sync', methods=['POST'])
@stocks_role_required('super_admin')
def stocks_super_sync():
    """Runs every FAST sync/calculation step in one click, in dependency
    order -- see utils/super_sync.py for exactly what and in what order.
    Deliberately excludes Screener.in fundamentals scraping, which runs on
    its own automatic cron schedule and is never admin-triggerable at all
    -- see /stocks/fundamentals/rotation-sync and the dashboard's
    "Fundamentals data" section. The individual buttons under the
    dashboard's "Advanced" section (Sync now, Sync Kite instrument map,
    Refresh shortlist, Sync prices (universe), Calculate indicators
    (universe), etc.) still exist for running just one step on its own;
    this is a convenience wrapper, not a replacement. super_admin only, and
    always backgrounded -- a full run covers everything including the
    ~1,067-symbol universe sync, so it can still take several minutes."""
    db = get_db()
    access_token = get_kite_access_token(db)
    if not access_token:
        return jsonify({
            'status': 'error',
            'message': 'No Kite session yet -- a super_admin must log in via /admin/stocks/kite/login first.'
        }), 400

    return _dispatch_stocks_job(
        db, is_cron=False, job_name='super_sync', job_fn=lambda job_db: run_super_sync(job_db, access_token)
    )


@stocks_bp.route('/stocks/kite/sync-instrument-map', methods=['POST'])
def stocks_kite_sync_instrument_map():
    """Manual trigger for matching Kite's full NSE+BSE instrument list
    against stock_universe by company name and caching the result -- see
    utils/kite_instrument_map.py. Not something that needs to run daily
    (Kite's instrument list and our own universe both change slowly), so
    there's no cron entry for this, just this button. Same dual auth as
    /stocks/sync: a valid X-Cron-Secret header or an active Stocks login
    session, either sufficient.

    Session-triggered runs happen on a background thread -- see
    _dispatch_stocks_job / utils/background_jobs.py."""
    is_cron = has_valid_cron_secret(request.headers, STOCKS_FUNDAMENTALS_CRON_SECRET)
    if not is_cron and not session.get('stocks_admin_id'):
        return redirect(url_for('stocks.stocks_admin_login'))

    db = get_db()
    access_token = get_kite_access_token(db)
    if not access_token:
        return jsonify({
            'status': 'error',
            'message': 'No Kite session yet -- a super_admin must log in via /admin/stocks/kite/login first.'
        }), 400

    def _job(job_db):
        return sync_kite_instrument_map(job_db, KiteClient(access_token=access_token))

    return _dispatch_stocks_job(db, is_cron, 'kite_instrument_map_sync', _job)


@stocks_bp.route('/stocks/universe/refresh-market-cap-filter', methods=['POST'])
def stocks_universe_refresh_market_cap_filter():
    """Re-runs ISIN-based BSE -> NSE market cap propagation, then re-buckets
    every stock_universe row's market_cap_band/is_scrape_eligible. Doesn't
    re-fetch BSE's own market cap -- see utils/stock_universe.py. Same dual
    auth as /stocks/sync: a valid X-Cron-Secret header or an active Stocks
    login session, either sufficient."""
    if not has_valid_cron_secret(request.headers, STOCKS_FUNDAMENTALS_CRON_SECRET) \
            and not session.get('stocks_admin_id'):
        return redirect(url_for('stocks.stocks_admin_login'))

    db = get_db()
    try:
        summary = refresh_market_cap_filter(db)
    except Exception as e:
        current_app.logger.error(f'Market cap filter refresh failed: {e}')
        alert_job_error(db, 'market_cap_filter', str(e))
        return jsonify({'status': 'error', 'message': str(e)}), 500
    record_job_success(db, 'market_cap_filter')
    return jsonify({'status': 'ok', **summary})


@stocks_bp.route('/stocks/watchlist/refresh-shortlist', methods=['POST'])
def stocks_watchlist_refresh_shortlist():
    """Runs the domain expert's fundamental screening criteria (see
    utils/fundamental_screen.py) over every scrape-eligible stock_universe
    company with a recent-enough stock_fundamentals snapshot, and syncs
    stock_watchlist accordingly -- see utils/stock_shortlist.py for exactly
    what "syncs" means (upsert passing companies active, deactivate
    previously-auto-shortlisted ones that no longer pass, never touch
    manually-added rows). Same dual auth as /stocks/sync: a valid
    X-Cron-Secret header or an active Stocks login session, either
    sufficient.

    Session-triggered runs happen on a background thread -- see
    _dispatch_stocks_job / utils/background_jobs.py."""
    is_cron = has_valid_cron_secret(request.headers, STOCKS_FUNDAMENTALS_CRON_SECRET)
    if not is_cron and not session.get('stocks_admin_id'):
        return redirect(url_for('stocks.stocks_admin_login'))

    db = get_db()

    def _job(job_db):
        try:
            summary = run_fundamental_shortlist(job_db)
        except Exception as e:
            current_app.logger.error(f'Fundamental shortlist refresh failed: {e}')
            alert_job_error(job_db, 'shortlist_refresh', str(e))
            raise
        record_job_success(job_db, 'shortlist_refresh')
        return summary

    return _dispatch_stocks_job(db, is_cron, 'shortlist_refresh', _job)


@stocks_bp.route('/stocks/watchlist/refresh-large-cap-shortlist', methods=['POST'])
def stocks_watchlist_refresh_large_cap_shortlist():
    """Large-cap counterpart of /stocks/watchlist/refresh-shortlist above --
    entirely new, parallel route; that route/run_fundamental_shortlist are
    not touched or called by this one. Re-buckets is_large_cap_eligible
    from the current last_market_cap (see
    utils.stock_universe.rebucket_large_cap_eligibility), then runs
    fundamental_screen.score_fundamentals_large_cap over every eligible
    company (above 30000cr, no upper bound) and syncs stock_watchlist --
    see utils.stock_shortlist.run_large_cap_shortlist for exactly what
    "syncs" means (same upsert/deactivate-diff behavior as the mid-cap
    pipeline, just scoped to its own LARGE_CAP_SHORTLIST_SOURCE so the two
    pipelines' watchlist rows never collide). Same dual auth as
    /stocks/watchlist/refresh-shortlist: a valid X-Cron-Secret header or an
    active Stocks login session, either sufficient.

    Session-triggered runs happen on a background thread -- see
    _dispatch_stocks_job / utils/background_jobs.py."""
    is_cron = has_valid_cron_secret(request.headers, STOCKS_FUNDAMENTALS_CRON_SECRET)
    if not is_cron and not session.get('stocks_admin_id'):
        return redirect(url_for('stocks.stocks_admin_login'))

    db = get_db()

    def _job(job_db):
        try:
            eligible_count = rebucket_large_cap_eligibility(job_db)
            summary = run_large_cap_shortlist(job_db)
            summary['large_cap_eligible_count'] = eligible_count
        except Exception as e:
            current_app.logger.error(f'Large-cap shortlist refresh failed: {e}')
            alert_job_error(job_db, 'large_cap_shortlist_refresh', str(e))
            raise
        record_job_success(job_db, 'large_cap_shortlist_refresh')
        return summary

    return _dispatch_stocks_job(db, is_cron, 'large_cap_shortlist_refresh', _job)


@stocks_bp.route('/stocks/suggestions/generate', methods=['POST'])
def stocks_suggestions_generate():
    """Runs the suggestion engine (see utils/suggestion_engine.py) over
    today's watchlist/indicators/fundamentals and inserts up to
    TOP_N_SUGGESTIONS new stock_suggestions rows. Same dual auth as
    /stocks/sync: a valid X-Cron-Secret header or an active Stocks login
    session, either sufficient."""
    if not has_valid_cron_secret(request.headers, STOCKS_FUNDAMENTALS_CRON_SECRET) \
            and not session.get('stocks_admin_id'):
        return redirect(url_for('stocks.stocks_admin_login'))

    db = get_db()
    try:
        summary = generate_daily_suggestions(db)
    except Exception as e:
        current_app.logger.error(f'Suggestion generation failed: {e}')
        alert_job_error(db, 'suggestion_generate', str(e))
        return jsonify({'status': 'error', 'message': str(e)}), 500
    record_job_success(db, 'suggestion_generate')
    return jsonify({'status': 'ok', **summary})


@stocks_bp.route('/stocks/suggestions/send-daily-email', methods=['POST'])
def stocks_suggestions_send_daily_email():
    """Generates today's suggestions (if not already run) and emails every
    active stocks_email_recipients row -- see utils/suggestion_engine.py
    and utils/suggestion_email.py. Same dual auth as /stocks/sync.

    Session-triggered runs happen on a background thread -- see
    _dispatch_stocks_job / utils/background_jobs.py."""
    is_cron = has_valid_cron_secret(request.headers, STOCKS_FUNDAMENTALS_CRON_SECRET)
    if not is_cron and not session.get('stocks_admin_id'):
        return redirect(url_for('stocks.stocks_admin_login'))

    db = get_db()

    def _job(job_db):
        if not is_trading_day():
            # Market's closed today (weekend/NSE holiday) -- nothing new to
            # recommend, so skip generation and the email entirely. Still
            # record success: stocks-check-missed-jobs.yml runs every day
            # with no weekday/holiday exception of its own, and "correctly
            # skipped because the market is shut" is the expected outcome
            # today, not a missed job.
            record_job_success(job_db, 'suggestion_email')
            return {'status': 'skipped', 'reason': 'not a trading day'}
        try:
            generation_summary = generate_daily_suggestions(job_db)
            if generation_summary.get('created'):
                # A no-op unless a super_admin has explicitly turned this
                # on from /stocks/auto-trader. Failures here must never
                # take the actual suggestion email down with them -- see
                # utils/auto_trader.py's module docstring for dry_run vs
                # live behavior.
                try:
                    auto_trade_settings = get_auto_trade_settings(job_db)
                    auto_trade_kite_client = _kite_client_for_auto_trade(job_db, auto_trade_settings)
                except Exception as e:
                    current_app.logger.error(f'Auto-trade Kite session unavailable: {e}')
                    auto_trade_settings, auto_trade_kite_client = None, None
                if auto_trade_settings is not None:
                    for created in generation_summary['created']:
                        try:
                            open_auto_trade_if_enabled(job_db, created, kite_client=auto_trade_kite_client)
                        except Exception as e:
                            current_app.logger.error(f'Auto-trade open failed for {created.get("symbol")}: {e}')
            # Raghav's own uncapped "Highly Recommended" alerts (see
            # utils/admin_alerts.py) -- every golden/silver candidate from
            # today's analysis, not just the one that became the customer
            # Pick of the Day above (that pick can be empty on a day
            # everything qualifying is on cooldown for customers, while
            # Raghav's own list -- which ignores that cooldown entirely --
            # still has entries), so this runs unconditionally, not nested
            # under generation_summary['created']. Never blocks the actual
            # daily customer email below on a failure here.
            try:
                record_and_send_highly_recommended_alerts(job_db)
            except Exception as e:
                current_app.logger.warning(f'Highly Recommended alert emails failed: {e}')
            summary = send_daily_suggestions_email(job_db)
        except Exception as e:
            current_app.logger.error(f'Daily suggestions email failed: {e}')
            alert_job_error(job_db, 'suggestion_email', str(e))
            raise
        record_job_success(job_db, 'suggestion_email')
        return summary

    return _dispatch_stocks_job(db, is_cron, 'suggestion_email', _job)


@stocks_bp.route('/stocks/starters/send-weekly-email', methods=['POST'])
def stocks_starters_send_weekly_email():
    """Starters-tier (Rs 99/mo) equivalent of /stocks/suggestions/send-daily-email
    -- generates this week's separately-curated golden-tier-only pick (see
    utils/starters_engine.py) and emails every active stocks_plan='starters'
    viewer. Same dual cron/session auth as every other Stocks job route.

    Triggered by a DAILY cron (same 02:00 UTC slot as the daily suggestion
    email), not a Monday-only one -- this job body itself checks the
    weekday and is_trading_day() and always calls record_job_success
    either way, exactly mirroring how the daily job already handles
    non-trading-days (see JOB_EXPECTATIONS in utils/stock_alerting.py for
    why this needs no day-of-week awareness there)."""
    is_cron = has_valid_cron_secret(request.headers, STOCKS_FUNDAMENTALS_CRON_SECRET)
    if not is_cron and not session.get('stocks_admin_id'):
        return redirect(url_for('stocks.stocks_admin_login'))

    db = get_db()

    def _job(job_db):
        if date.today().weekday() != 0 or not is_trading_day():
            record_job_success(job_db, 'starters_weekly_email')
            return {'status': 'skipped', 'reason': 'not a Monday trading day'}
        try:
            generate_weekly_starters_pick(job_db)
            summary = send_weekly_starters_email(job_db)
        except Exception as e:
            current_app.logger.error(f'Weekly Starters email failed: {e}')
            alert_job_error(job_db, 'starters_weekly_email', str(e))
            raise
        record_job_success(job_db, 'starters_weekly_email')
        return summary

    return _dispatch_stocks_job(db, is_cron, 'starters_weekly_email', _job)


# Tuesday and Friday -- the two runs a week that produce a bonus large-cap
# pick for Standard-plan subscribers (see stocks_large_cap_bonus_send_email
# below). date.weekday(): Monday=0 ... Sunday=6.
LARGE_CAP_BONUS_WEEKDAYS = (1, 4)


@stocks_bp.route('/stocks/large-cap-bonus/send-email', methods=['POST'])
def stocks_large_cap_bonus_send_email():
    """Standard-tier (Rs 299/mo) bonus large-cap pick, sent TWICE A WEEK
    (Tuesday and Friday) IN ADDITION TO the regular daily Pick of the Day
    -- generates a pick drawn only from the large-cap tier (see
    utils/large_cap_engine.py) and emails every active stocks_plan='standard'
    viewer. Same dual cron/session auth as every other Stocks job route.

    Triggered by a DAILY cron (same 02:00 UTC slot as the daily suggestion
    email), not a Tuesday/Friday-only one -- this job body itself checks
    the weekday (LARGE_CAP_BONUS_WEEKDAYS) and is_trading_day() and always
    calls record_job_success either way, exactly mirroring how
    stocks_starters_send_weekly_email already handles its own Monday-only
    cadence (see JOB_EXPECTATIONS in utils/stock_alerting.py for why this
    needs no day-of-week awareness there)."""
    is_cron = has_valid_cron_secret(request.headers, STOCKS_FUNDAMENTALS_CRON_SECRET)
    if not is_cron and not session.get('stocks_admin_id'):
        return redirect(url_for('stocks.stocks_admin_login'))

    db = get_db()

    def _job(job_db):
        if date.today().weekday() not in LARGE_CAP_BONUS_WEEKDAYS or not is_trading_day():
            record_job_success(job_db, 'large_cap_bonus_email')
            return {'status': 'skipped', 'reason': 'not a Tuesday/Friday trading day'}
        try:
            generate_large_cap_bonus_pick(job_db)
            summary = send_large_cap_bonus_email(job_db)
        except Exception as e:
            current_app.logger.error(f'Large-cap bonus email failed: {e}')
            alert_job_error(job_db, 'large_cap_bonus_email', str(e))
            raise
        record_job_success(job_db, 'large_cap_bonus_email')
        return summary

    return _dispatch_stocks_job(db, is_cron, 'large_cap_bonus_email', _job)


@stocks_bp.route('/stocks/suggestions/notify-target-hits', methods=['POST'])
def stocks_suggestions_notify_target_hits():
    """Checks every still-'pending' stock_suggestions row against the
    latest synced close (see suggestion_engine.find_pending_target_hit_suggestions)
    and, for any that have reached target, emails every stocks_plan='standard'
    (Rs 299/mo) viewer who ever had access -- is_active never gets reset
    back to 0 once a trial or subscription starts (only an admin manually
    suspending an account does that, see toggle_viewer_active), so this
    deliberately still reaches someone whose free trial expired without
    subscribing, or whose paid subscription lapsed -- the pick they saw
    while they had access hit target regardless, and it's exactly the
    moment they're most likely to come back and subscribe. One email per
    recipient bundling every hit found this run, not one email per stock
    -- with the recommended day/price, target price, achieved day, time
    taken, and profit at today's close, prompting them to consider
    booking profit themselves, plus a resubscribe nudge for anyone who
    doesn't currently have access (see send_target_achieved_email). Every
    notified suggestion is then marked status='target_hit'
    (mark_suggestions_target_hit) so it's never re-notified about.

    Deliberately a separate job/route from send-daily-email -- doesn't
    touch that job's own logic at all. Same dual cron/session auth as
    every other Stocks job route."""
    is_cron = has_valid_cron_secret(request.headers, STOCKS_FUNDAMENTALS_CRON_SECRET)
    if not is_cron and not session.get('stocks_admin_id'):
        return redirect(url_for('stocks.stocks_admin_login'))

    db = get_db()

    def _job(job_db):
        try:
            hits = find_pending_target_hit_suggestions(job_db)
            sent = 0
            failed = 0
            if hits:
                recipients = job_db.execute(
                    "SELECT id, username AS email, name, is_pro, subscription_status, "
                    "subscription_current_period_end, trial_ends_at FROM stocks_admin_users "
                    "WHERE role='viewer' AND is_active=1 AND stocks_plan='standard' AND email_unsubscribed_at IS NULL"
                ).fetchall()
                if not recipients:
                    current_app.logger.warning(
                        f'{len(hits)} target hit(s) found but there are no Standard-plan '
                        f'subscribers to notify -- marking as notified anyway since there is no one to retry for.'
                    )
                for r in recipients:
                    currently_subscribed = has_stocks_access(
                        r.get('is_pro'), r.get('subscription_status'), r.get('subscription_current_period_end'),
                        trial_ends_at=r.get('trial_ends_at'),
                    )
                    ok, detail = send_target_achieved_email(r['email'], r.get('name'), hits, currently_subscribed=currently_subscribed)
                    if ok:
                        sent += 1
                    else:
                        failed += 1
                        current_app.logger.warning(f'Target-achieved email failed for {r["email"]}: {detail}')
                # Only consume the 'pending' status (permanently excluding
                # these hits from tomorrow's re-check, see
                # find_pending_target_hit_suggestions) once we know someone
                # actually got told, or there was truly no one to tell --
                # if recipients existed but every single send failed (e.g. a
                # transient ZeptoMail outage), leave them pending so
                # tomorrow's run retries instead of silently losing the
                # notification forever.
                if sent > 0 or not recipients:
                    mark_suggestions_target_hit(job_db, [h['id'] for h in hits])
                else:
                    current_app.logger.error(
                        f'All {len(recipients)} target-achieved emails failed to send; '
                        f'leaving {len(hits)} suggestion(s) pending for retry tomorrow.'
                    )
            summary = {'target_hits': len(hits), 'recipients_sent': sent, 'recipients_failed': failed}
        except Exception as e:
            current_app.logger.error(f'Target-hit notification job failed: {e}')
            alert_job_error(job_db, 'suggestion_target_hit_notify', str(e))
            raise
        record_job_success(job_db, 'suggestion_target_hit_notify')
        return summary

    return _dispatch_stocks_job(db, is_cron, 'suggestion_target_hit_notify', _job)


@stocks_bp.route('/stocks/announcements/send-rebrand', methods=['POST'])
@stocks_role_required('super_admin')
def stocks_announcements_send_rebrand():
    """One-time manual trigger: emails every active viewer that Nari Nakhre
    Stocks has been renamed StoqBell, with its own domain and its own
    sending address (see utils/suggestion_email.py's
    send_rebrand_announcement_to_all_viewers/send_rebrand_announcement_email).
    super_admin-only, no cron path -- unlike the daily/weekly/bonus
    suggestion emails, this isn't meant to ever run again on a schedule.
    Runs on a background thread like the dashboard's other job buttons
    (see _dispatch_stocks_job) since it may be emailing a large recipient
    list; poll /stocks/jobs/rebrand_announcement/status for the result."""
    db = get_db()
    return _dispatch_stocks_job(
        db, is_cron=False, job_name='rebrand_announcement',
        job_fn=send_rebrand_announcement_to_all_viewers,
    )


@stocks_bp.route('/stocks/notifications', methods=['GET'])
@stocks_role_required('super_admin')
def stocks_notifications():
    """super_admin-only: manual pick-and-send page, distinct from the fully
    automatic daily job (generate_daily_suggestions always takes the single
    top-ranked golden-cross candidate) -- lets the admin choose how many
    recommendations to send today, either the top-ranked one(s) or a random
    sample from today's golden-cross-eligible pool, review the specific
    stock(s) surfaced (see /stocks/notifications/preview-picks) before
    committing, and choose which viewers receive them (see
    /stocks/notifications/send). Same recipient list as the resend page."""
    db = get_db()
    recipients = [v for v in list_viewers(db) if v.get('is_active')]
    return render_template('admin/stocks_notifications.html', recipients=recipients)


@stocks_bp.route('/stocks/notifications/preview-picks', methods=['POST'])
@stocks_role_required('super_admin')
def stocks_notifications_preview_picks():
    """AJAX endpoint for the Notifications page: surfaces candidates for the
    admin to review, without creating any stock_suggestions row yet (see
    get_candidates_for_manual_pick) -- committing only happens at
    /stocks/notifications/send, once the admin has picked which of these to
    actually send."""
    mode = request.form.get('mode') or 'top'
    if mode not in ('top', 'random'):
        mode = 'top'
    try:
        count = int(request.form.get('count', 2))
    except ValueError:
        count = 2
    count = max(1, min(count, 5))

    db = get_db()
    candidates = get_candidates_for_manual_pick(db, count=count, mode=mode)
    return jsonify({'status': 'ok', 'candidates': candidates})


@stocks_bp.route('/stocks/notifications/send', methods=['POST'])
@stocks_role_required('super_admin')
def stocks_notifications_send():
    """super_admin-only: creates today's stock_suggestions row(s) for the
    admin-chosen watchlist_ids (see create_manual_suggestions) and emails
    them to the chosen recipients via the SAME send path the automatic daily
    job uses (send_daily_suggestions_email(target_date=today,
    recipient_ids=...)) -- no separate email-building code. Runs
    synchronously, same reasoning as /stocks/suggestions/resend: a handful
    of stocks and recipients, not a slow sync job worth backgrounding."""
    db = get_db()

    raw_watchlist_ids = request.form.getlist('watchlist_ids')
    if not raw_watchlist_ids:
        flash('Please select at least one stock to send.', 'error')
        return redirect(url_for('stocks.stocks_notifications'))
    try:
        watchlist_ids = [int(v) for v in raw_watchlist_ids]
    except ValueError:
        flash('Invalid stock selection.', 'error')
        return redirect(url_for('stocks.stocks_notifications'))

    raw_recipient_ids = request.form.getlist('recipient_ids')
    if not raw_recipient_ids:
        flash('Please select at least one recipient.', 'error')
        return redirect(url_for('stocks.stocks_notifications'))
    try:
        recipient_ids = [int(v) for v in raw_recipient_ids]
    except ValueError:
        flash('Invalid recipient selection.', 'error')
        return redirect(url_for('stocks.stocks_notifications'))

    try:
        creation_summary = create_manual_suggestions(db, watchlist_ids)
        send_summary = send_daily_suggestions_email(
            db, target_date=date.today(), recipient_ids=recipient_ids
        )
    except Exception as e:
        current_app.logger.error(f'Manual notification send failed: {e}')
        flash(f'Send failed: {e}', 'error')
        return redirect(url_for('stocks.stocks_notifications'))

    created_symbols = ', '.join(f"{c['symbol']} ({c['exchange']})" for c in creation_summary['created'])
    skipped_count = len(creation_summary['skipped'])
    message = (
        f"Sent {created_symbols or 'nothing'} to {send_summary['sent']} of "
        f"{send_summary['recipient_count']} selected recipients"
        + (f", {send_summary['failed']} failed" if send_summary['failed'] else '')
        + (f'. {skipped_count} chosen stock(s) were skipped (already suggested recently, no genuine change).'
           if skipped_count else '.')
    )
    flash(message, 'error' if (send_summary['failed'] or not creation_summary['created']) else 'info')
    return redirect(url_for('stocks.stocks_notifications'))


@stocks_bp.route('/stocks/suggestions/resend', methods=['GET', 'POST'])
@stocks_role_required('super_admin')
def stocks_suggestions_resend():
    """super_admin-only: re-sends a PAST day's recommendation email,
    unchanged from however it looked that day (see
    send_daily_suggestions_email's target_date param) -- for when a send
    needs to go out again (e.g. a design fix, or a recipient who says they
    never got it), without re-running the suggestion engine or affecting
    today's picks at all. Defaults to every active viewer, same as the
    original daily send, but the form lets the admin deselect all and pick
    specific recipients instead (see send_daily_suggestions_email's
    recipient_ids param) -- useful for e.g. resending to just the one
    person who reported a problem, not the whole list again. Runs
    synchronously (unlike the dashboard's other job buttons) -- this is an
    occasional manual action sending a handful of already-computed emails,
    not a slow sync job worth backgrounding."""
    db = get_db()

    if request.method == 'POST':
        target_date = (request.form.get('suggestion_date') or '').strip()
        if not target_date:
            flash('Please choose a date to resend.', 'error')
            return redirect(url_for('stocks.stocks_suggestions_resend'))

        raw_recipient_ids = request.form.getlist('recipient_ids')
        if not raw_recipient_ids:
            flash('Please select at least one recipient.', 'error')
            return redirect(url_for('stocks.stocks_suggestions_resend'))
        try:
            recipient_ids = [int(v) for v in raw_recipient_ids]
        except ValueError:
            flash('Invalid recipient selection.', 'error')
            return redirect(url_for('stocks.stocks_suggestions_resend'))

        try:
            summary = send_daily_suggestions_email(db, target_date=target_date, recipient_ids=recipient_ids)
        except Exception as e:
            current_app.logger.error(f'Manual suggestion resend failed for {target_date}: {e}')
            flash(f'Resend failed: {e}', 'error')
            return redirect(url_for('stocks.stocks_suggestions_resend'))

        stock_word = 'stock' if summary['suggestion_count'] == 1 else 'stocks'
        message = (
            f"Resent {target_date}'s recommendations ({summary['suggestion_count']} {stock_word}) "
            f"to {summary['sent']} of {summary['recipient_count']} selected recipients"
            + (f", {summary['failed']} failed" if summary['failed'] else '') + '.'
        )
        flash(message, 'error' if summary['failed'] else 'info')
        return redirect(url_for('stocks.stocks_suggestions_resend'))

    dates = db.execute(
        'SELECT DISTINCT suggestion_date FROM stock_suggestions ORDER BY suggestion_date DESC LIMIT 90'
    ).fetchall()
    recipients = [v for v in list_viewers(db) if v.get('is_active')]
    return render_template(
        'admin/stocks_suggestions_resend.html',
        dates=[d['suggestion_date'] for d in dates],
        recipients=recipients,
    )


@stocks_bp.route('/stocks/jobs/<job_name>/status', methods=['GET'])
@stocks_login_required
def stocks_job_status(job_name):
    """Polled by the dashboard's JS after a background job starts (see
    _dispatch_stocks_job above and utils/background_jobs.py) -- returns the
    latest run's status for job_name: 'running', 'done' (with its result),
    'error' (with the message), or 'never_run'. Any logged-in role can
    poll -- it's read-only, and job_name is one of a handful of fixed
    strings the dashboard itself controls, not user input that reaches a
    query some other way."""
    db = get_db()
    return jsonify(get_job_status(db, job_name))


@stocks_bp.route('/stocks/jobs/<job_name>/cancel', methods=['POST'])
@stocks_role_required('super_admin')
def stocks_job_cancel(job_name):
    """Manual escape hatch for a job stuck showing 'running' -- see
    cancel_job's docstring for exactly what this does and doesn't do (it
    can't interrupt a genuinely still-executing thread, only clear the
    dedup lock and update what the dashboard shows; in practice the
    process that started it has almost always already died by the time
    anyone needs this button, e.g. after a Render restart mid-run).
    super_admin only, same as Super Sync itself."""
    db = get_db()
    cancelled = cancel_job(db, job_name)
    if cancelled:
        flash(f'"{job_name}" was cancelled.', 'info')
    else:
        flash(f'"{job_name}" was not running.', 'info')
    return redirect(url_for('stocks.stocks_admin_dashboard'))


@stocks_bp.route('/stocks/users', methods=['GET', 'POST'])
@stocks_role_required('super_admin')
def stocks_users_manage():
    """Replaces the old /stocks/recipients page -- viewers are now real
    stocks_admin_users accounts (role='viewer'), not just an email on a
    list, so they can log in and see their own suggestions (see
    /stocks/my/suggestions). No self-serve signup, no plans, no payment --
    purely a super_admin-maintained list, same as before.

    A newly-created viewer gets a real, random, usable password (see
    create_viewer_account in utils/stock_auth.py) and is emailed it
    immediately, along with the login link and a short explanation of the
    suggestions system -- there's still no separate password-reset/change
    flow anywhere in this codebase, so this welcome email is the only way
    they ever learn their credentials."""
    db = get_db()
    if request.method == 'POST':
        email = request.form.get('email')
        name = request.form.get('name')
        start_trial = request.form.get('start_trial') == 'on'
        row, password, error = create_viewer_account(
            db, email, name, session.get('stocks_admin_id'), start_trial=start_trial,
        )
        if error:
            flash(error, 'error')
        else:
            email = email.strip()
            sent, detail = send_viewer_welcome_email(email, name, password, db=db, admin_id=row['id'])
            if sent:
                flash(f'Added {email} as a viewer and emailed their login details.')
            else:
                flash(f'Added {email} as a viewer, but the welcome email failed to send: '
                      f'{detail} -- share their login manually for now.', 'error')
        return redirect(url_for('stocks.stocks_users_manage'))

    viewers = []
    for v in list_viewers(db):
        v = dict(v)
        trial_ends_at = v.get('trial_ends_at')
        if isinstance(trial_ends_at, str):
            trial_ends_at = datetime.fromisoformat(trial_ends_at.replace('Z', '+00:00'))
        v['trial_ends_at_label'] = trial_ends_at.strftime('%d %b %Y') if trial_ends_at else None
        viewers.append(v)
    return render_template('admin/stocks_users.html', viewers=viewers)


@stocks_bp.route('/stocks/users/<int:viewer_id>/toggle', methods=['POST'])
@stocks_role_required('super_admin')
def stocks_users_toggle(viewer_id):
    db = get_db()
    if not toggle_viewer_active(db, viewer_id):
        flash('Could not update that user.', 'error')
    else:
        flash('User status updated.')
    return redirect(url_for('stocks.stocks_users_manage'))


@stocks_bp.route('/stocks/users/<int:viewer_id>/delete', methods=['POST'])
@stocks_role_required('super_admin')
def stocks_users_delete(viewer_id):
    """Permanently removes a viewer account -- unlike the toggle above,
    not reversible. See delete_viewer_account in utils/stock_auth.py for
    the same role-safety guarantee toggle_viewer_active already has (can
    never delete a super_admin/child_admin row, whatever id is passed)."""
    db = get_db()
    if not delete_viewer_account(db, viewer_id):
        flash('Could not delete that user.', 'error')
    else:
        flash('Viewer account deleted.')
    return redirect(url_for('stocks.stocks_users_manage'))


@stocks_bp.route('/stocks/users/migrate-recipients', methods=['POST'])
@stocks_role_required('super_admin')
def stocks_users_migrate_recipients():
    """One-time (safely re-runnable) migration button: creates a viewer
    account for every stocks_email_recipients row that doesn't already have
    one, and emails each of them their new login credentials -- same
    welcome email a manually-created viewer gets (see stocks_users_manage
    above). See migrate_email_recipients_to_viewers in utils/stock_auth.py."""
    db = get_db()
    summary = migrate_email_recipients_to_viewers(db, session.get('stocks_admin_id'))
    emailed = 0
    email_failures = []
    for account in summary['created_accounts']:
        sent, detail = send_viewer_welcome_email(account['email'], account['name'], account['password'])
        if sent:
            emailed += 1
        else:
            email_failures.append(f"{account['email']} ({detail})")
    if summary['migrated']:
        message = (f"Migrated {len(summary['migrated'])} recipient(s) to viewer accounts "
                    f"and emailed login details to {emailed} of them.")
        if email_failures:
            message += f" Failed to email: {'; '.join(email_failures)} -- share their login manually."
            flash(message, 'error')
        else:
            flash(message)
    else:
        flash('No new recipients to migrate.')
    return redirect(url_for('stocks.stocks_users_manage'))


@stocks_bp.route('/stocks/indicators/calculate', methods=['POST'])
def stocks_indicators_calculate():
    """Computes MA21/50/200, RSI-14, cross status, and volume trend for
    every active stock_watchlist row -- see utils/stock_indicators.py and
    utils/indicator_engine.py. Requires stock_daily_data to already have
    that day's prices, hence the daily GitHub Actions workflow for this is
    scheduled after the price-sync one. Same dual auth as /stocks/sync: a
    valid X-Cron-Secret header or an active Stocks login session, either
    sufficient."""
    if not has_valid_cron_secret(request.headers, STOCKS_FUNDAMENTALS_CRON_SECRET) \
            and not session.get('stocks_admin_id'):
        return redirect(url_for('stocks.stocks_admin_login'))

    db = get_db()
    try:
        summary = run_indicator_calculation(db)
    except Exception as e:
        current_app.logger.error(f'Indicator calculation failed: {e}')
        alert_job_error(db, 'indicator_calc', str(e))
        return jsonify({'status': 'error', 'message': str(e)}), 500
    record_job_success(db, 'indicator_calc')
    return jsonify({'status': 'ok', **summary})


@stocks_bp.route('/stocks/indicators/calculate/universe', methods=['POST'])
def stocks_indicators_calculate_universe():
    """Same as /stocks/indicators/calculate, but over the full scrape-
    eligible stock_universe set -- see
    utils/stock_indicators.run_indicator_calculation_universe. Requires
    /stocks/sync/universe to have already populated stock_daily_data for
    these companies. Always backgrounded regardless of trigger, same
    reasoning as /stocks/sync/universe -- see _dispatch_stocks_job."""
    is_cron = has_valid_cron_secret(request.headers, STOCKS_FUNDAMENTALS_CRON_SECRET)
    if not is_cron and not session.get('stocks_admin_id'):
        return redirect(url_for('stocks.stocks_admin_login'))

    db = get_db()
    return _dispatch_stocks_job(
        db, is_cron=False, job_name='indicator_calc_universe', job_fn=run_indicator_calculation_universe
    )


@stocks_bp.route('/stocks/notifications/check-intraday-hits', methods=['POST'])
def stocks_notifications_check_intraday_hits():
    """Every-5-minutes, market-hours-only intraday target-hit check (see
    utils/admin_alerts.find_and_notify_intraday_target_hits) -- cron-only,
    same convention as /stocks/alerts/check-missed-jobs, called by its own
    GitHub Actions workflow (stocks-intraday-target-hit-check.yml), never
    from the dashboard. Skips (without error) outside actual trading
    hours/days -- the cron schedule itself is deliberately a little
    generous around NSE/BSE's 09:15-15:30 IST session, this is the precise
    gate. A Kite session issue (expired daily access token -- see
    KiteClient) is reported as an error but never raises past this route,
    since a stuck cron step here would otherwise fail every run for the
    rest of the day until someone notices."""
    if not has_valid_cron_secret(request.headers, STOCKS_FUNDAMENTALS_CRON_SECRET):
        return jsonify({'status': 'error', 'message': 'Unauthorized'}), 403

    if not is_trading_day() or not is_within_trading_hours():
        return jsonify({'status': 'skipped', 'reason': 'outside trading hours'})

    db = get_db()
    try:
        summary = find_and_notify_intraday_target_hits(db)
    except Exception as e:
        current_app.logger.error(f'Intraday target-hit check failed: {e}')
        return jsonify({'status': 'error', 'message': str(e)}), 500
    return jsonify({'status': 'ok', **summary})


@stocks_bp.route('/stocks/alerts/check-missed-jobs', methods=['POST'])
def stocks_alerts_check_missed_jobs():
    """Checks stock_job_runs for each cron-triggered route and emails an
    alert for anything overdue -- see utils/stock_alerting.py. Cron-only,
    unlike the other Stocks routes: no browser session path, since this is
    only ever meant to be called by the scheduled GitHub Actions workflow,
    not clicked from the dashboard."""
    if not has_valid_cron_secret(request.headers, STOCKS_FUNDAMENTALS_CRON_SECRET):
        return jsonify({'status': 'error', 'message': 'Unauthorized'}), 403

    db = get_db()
    try:
        summary = check_missed_jobs(db)
    except Exception as e:
        current_app.logger.error(f'Missed-job check failed: {e}')
        return jsonify({'status': 'error', 'message': str(e)}), 500
    return jsonify({'status': 'ok', **summary})


@stocks_bp.route('/stocks/kite/login', methods=['GET'])
@stocks_role_required('super_admin')
def stocks_kite_login():
    """Sends the super_admin to Zerodha's login page. Kite redirects back to
    stocks_kite_callback below with a request_token once they log in there."""
    try:
        login_url = get_kite_login_url()
    except RuntimeError as e:
        flash(str(e), 'error')
        return redirect(url_for('stocks.stocks_admin_dashboard'))
    return redirect(login_url)


@stocks_bp.route('/stocks/kite/callback', methods=['GET'])
def stocks_kite_callback():
    """Registered as the Redirect URL on the Kite Connect app. Deliberately
    public/unauthenticated -- Zerodha's redirect is a fresh top-level GET
    from kite.zerodha.com, and this is the standard shape for an OAuth-style
    callback (the request_token itself, single-use and only exchangeable
    with our api_secret, is what proves the login happened -- not our own
    session cookie, which may or may not have survived the round trip).
    Exchanges the request_token for an access_token and stores it -- that's
    the token every subsequent Kite API call uses until it expires tomorrow
    and a super_admin repeats this flow."""
    request_token = request.args.get('request_token')
    status = request.args.get('status')
    if status != 'success' or not request_token:
        flash('Kite login was not completed successfully.', 'error')
        return redirect(url_for('stocks.stocks_admin_dashboard'))

    try:
        access_token = exchange_request_token(request_token)
    except Exception as e:
        current_app.logger.error(f'Kite session exchange failed: {e}')
        flash('Could not complete Kite login. Please try again.', 'error')
        return redirect(url_for('stocks.stocks_admin_dashboard'))

    db = get_db()
    expires_at = save_kite_access_token(db, access_token, session.get('stocks_admin_id'))
    expires_at_ist = expires_at.astimezone(IST).strftime('%d %b %Y, %I:%M %p IST')
    flash(f'Kite access token refreshed. Expires {expires_at_ist}.')
    return redirect(url_for('stocks.stocks_admin_dashboard'))


@stocks_bp.route('/stocks/kite/postback', methods=['POST'])
def stocks_kite_postback():
    """Registered as the Postback URL on the Kite Connect app. Public --
    Zerodha posts here directly, no browser session involved. Only verifies
    the checksum and logs the payload for now; nothing here updates order or
    suggestion state yet (that depends on execute_suggestion(), a later
    phase)."""
    payload = request.get_json(silent=True)
    if payload is None:
        payload = request.form.to_dict()

    if not verify_postback_checksum(payload):
        current_app.logger.warning('Kite postback rejected: invalid or missing checksum')
        return jsonify({'status': 'error', 'message': 'Invalid checksum'}), 400

    db = get_db()
    try:
        log_postback(db, payload)
    except Exception as e:
        current_app.logger.error(f'Kite postback log failed: {e}')
        return jsonify({'status': 'error'}), 500
    return jsonify({'status': 'ok'})


@stocks_bp.route('/stocks/fundamentals/rotation-sync', methods=['POST'])
def stocks_fundamentals_rotation_sync():
    """The only Screener.in scraping this app does -- daily rotation over
    the full scrape-eligible stock_universe set (see
    utils/fundamentals_ingestion.sync_fundamentals_rotation), refreshing
    the ROTATION_BATCH_SIZE stalest companies each run so the full
    ~1,067-company eligible set cycles roughly every 15 days.

    Deliberately cron-secret only, unlike most other /stocks/ routes --
    there is intentionally no dashboard button and no admin-session path
    here. Screener.in scraping is slow (one HTTP request per company) and
    rate-sensitive; an admin being able to trigger it on demand risks
    someone re-running it far more often than the 15-day cadence this was
    sized for, which is exactly what this route is meant to prevent. See
    the Stocks dashboard's "Fundamentals data" section, which shows a
    last-synced time but no button."""
    if not has_valid_cron_secret(request.headers, STOCKS_FUNDAMENTALS_CRON_SECRET):
        return jsonify({'status': 'error', 'message': 'Unauthorized'}), 403

    db = get_db()
    try:
        summary = sync_fundamentals_rotation(db)
    except Exception as e:
        current_app.logger.error(f'Fundamentals rotation sync failed: {e}')
        alert_job_error(db, 'fundamentals_rotation', str(e))
        return jsonify({'status': 'error', 'message': str(e)}), 500
    record_job_success(db, 'fundamentals_rotation')
    return jsonify({'status': 'ok', **summary})


@stocks_bp.route('/stocks/watchlist', methods=['GET'])
@stocks_watchlist_access_required
def stocks_watchlist():
    """Lists every stock_watchlist row with its latest stock_fundamentals,
    stock_indicators, and stock_daily_data (price) snapshots joined in,
    including cross_status (golden cross / death cross / no clear trend)
    and whether it currently passes the same hard filters the suggestion
    engine uses to decide what to recommend (see
    suggestion_engine.passes_hard_filters) -- shown as "Recommended to buy".
    Doesn't touch any of those tables -- read-only here. Any logged-in
    Stocks account -- super_admin, child_admin, or a plain viewer of
    either plan -- has access (see stocks_watchlist_access_required).

    Staff (super_admin/child_admin) get the full operational table incl.
    PE/PEG/OPM/RSI/StoqBell Score AND the Recommended column. A viewer
    gets a simplified table instead (see the template): name, price, and
    cross-over status only -- the Recommended column (and its row
    highlight) is deliberately left out for a viewer, so browsing the
    watchlist doesn't leak which companies StoqBell is currently
    recommending; that stays behind the daily Pick of the Day a paying
    plan actually delivers.

    ?filter=golden narrows the list to golden_cross rows only, for either
    audience -- the "view only golden cross companies" option.
    ?filter=golden_not_qualified switches entirely to
    get_golden_cross_not_qualified()'s list instead -- golden-cross
    companies from the full scrape-eligible universe (not just the
    watchlist) that are excluded fundamentally, with the specific reasons
    why. Staff only (super_admin/child_admin) -- gated by its own inline
    role check below, not stocks_watchlist_access_required, since a viewer
    should never see this diagnostic view regardless of watchlist access.

    Only is_active=1 rows are shown -- stock_watchlist never deletes a row,
    only deactivates it (see run_fundamental_shortlist), so without this
    filter every company that ever fell out of the screen, plus every
    pre-dedup duplicate NSE/BSE listing from before ISIN-based dedup
    existed (see utils/stock_shortlist.py's _pick_canonical_listing), would
    still show up here forever."""
    db = get_db()
    cross_filter = request.args.get('filter')

    if cross_filter == 'golden_not_qualified':
        if session.get('stocks_admin_role') not in ('super_admin', 'child_admin'):
            flash('You do not have access to that view.', 'error')
            return redirect(url_for('stocks.stocks_watchlist'))
        golden_not_qualified = get_golden_cross_not_qualified(db)
        return render_template(
            'admin/stocks_watchlist.html', rows=[], cross_filter=cross_filter, tier_filter='all',
            golden_not_qualified=golden_not_qualified
        )

    rows = db.execute(
        '''SELECT w.id, w.symbol, w.exchange, w.name, w.is_active, w.fundamental_tier,
                  u.id AS universe_id, u.industry,
                  f.pe_ratio, f.peg_ratio, f.eps, f.opm_pct, f.roce_pct, f.roa_pct,
                  f.quarterly_profit_growth_pct, f.quarterly_revenue_growth_pct, f.price_to_book,
                  f.promoter_holding_pct, f.fii_holding_pct, f.snapshot_date,
                  i.rsi_14, i.cross_status, i.volume_trend, i.calc_date,
                  d.close AS latest_price, d.trade_date AS price_date
           FROM stock_watchlist w
           LEFT JOIN stock_universe u ON u.symbol = w.symbol AND u.exchange = w.exchange
           LEFT JOIN stock_fundamentals f ON f.watchlist_id = w.id
               AND f.snapshot_date = (
                   SELECT MAX(f2.snapshot_date) FROM stock_fundamentals f2 WHERE f2.watchlist_id = w.id
               )
           LEFT JOIN stock_indicators i ON i.watchlist_id = w.id
               AND i.calc_date = (
                   SELECT MAX(i2.calc_date) FROM stock_indicators i2 WHERE i2.watchlist_id = w.id
               )
           LEFT JOIN stock_daily_data d ON d.watchlist_id = w.id
               AND d.trade_date = (
                   SELECT MAX(d2.trade_date) FROM stock_daily_data d2 WHERE d2.watchlist_id = w.id
               )
           WHERE w.is_active = 1
           ORDER BY w.symbol'''
    ).fetchall()

    # NNS Score for every row, not just ones that also clear
    # passes_hard_filters -- see compute_watchlist_nns_scores's docstring.
    # Staff-only: computing this for every row (industry benchmarks +
    # previous-snapshot batching) is extra DB work a viewer's simplified
    # table has no use for anyway (see the template -- viewers still only
    # see is_recommended, not the score itself).
    if session.get('stocks_admin_role') in ('super_admin', 'child_admin'):
        rows = compute_watchlist_nns_scores(db, rows)

    rows = enrich_and_sort_watchlist_rows(rows, cross_filter=cross_filter)

    # Separate from cross_filter -- this narrows by fundamental_tier
    # (golden/silver/bronze, see utils.fundamental_screen.classify_fundamental_tier),
    # not by the golden-cross technical signal. Applied after enrich/sort
    # (which already computed is_recommended/pe_note/etc. for every row)
    # rather than in SQL, since is_active=1 already includes all three
    # tiers -- this is purely a display-side narrowing.
    tier_filter = request.args.get('tier')
    if tier_filter in ('golden', 'silver', 'bronze'):
        rows = [r for r in rows if r.get('fundamental_tier') == tier_filter]

    return render_template(
        'admin/stocks_watchlist.html', rows=rows, cross_filter=cross_filter or 'all',
        tier_filter=tier_filter or 'all',
    )


@stocks_bp.route('/stocks/company/<int:watchlist_id>', methods=['GET'])
@stocks_watchlist_access_required
def stocks_company_detail(watchlist_id):
    """Full fundamentals + technicals + recent price/suggestion history for
    one watchlist company -- the drill-down every row on /stocks/watchlist
    links to. Same access as the watchlist itself. Read-only.

    Also computes (see utils/price_pattern.py, no DB access there):
      - day change % vs the previous close, and the 52-week high/low
      - a per-parameter trend (Increasing/Decreasing/Unchanged) for every
        fundamental and technical figure that has a prior snapshot to
        compare against -- fundamentals compare against the previous
        quarterly snapshot (that's the real reporting cadence; there's no
        daily shareholding data to compare against instead), technicals
        against the previous day's calc
      - an inline price sparkline (decorative -- the real trend info is in
        the adjacent text, for screen readers)
      - a transparent historical backtest of what happened, in this
        stock's own price history, after past days with a similar RSI
        zone -- explicitly not a forecast, see backtest_rsi_zone_outcomes's
        docstring."""
    db = get_db()
    company = db.execute(
        '''SELECT w.id, w.symbol, w.exchange, w.name, w.is_active, w.fundamental_tier,
                  f.pe_ratio, f.peg_ratio, f.eps, f.market_cap, f.roe, f.debt_to_equity,
                  f.earnings_growth_pct, f.sector_avg_pe, f.price_to_book, f.opm_pct,
                  f.roce_pct, f.roa_pct, f.current_ratio, f.tol_by_tnw,
                  f.promoter_holding_pct, f.fii_holding_pct, f.public_holding_pct,
                  f.quarterly_profit_growth_pct, f.quarterly_revenue_growth_pct,
                  f.free_cash_flow, f.snapshot_date AS fundamentals_date,
                  i.ma_5, i.ma_21, i.ma_50, i.ma_200, i.rsi_14, i.volume_avg_20d,
                  i.cross_status, i.volume_trend, i.calc_date AS indicators_date,
                  d.close AS latest_price, d.trade_date AS price_date
           FROM stock_watchlist w
           LEFT JOIN stock_fundamentals f ON f.watchlist_id = w.id
               AND f.snapshot_date = (
                   SELECT MAX(f2.snapshot_date) FROM stock_fundamentals f2 WHERE f2.watchlist_id = w.id
               )
           LEFT JOIN stock_indicators i ON i.watchlist_id = w.id
               AND i.calc_date = (
                   SELECT MAX(i2.calc_date) FROM stock_indicators i2 WHERE i2.watchlist_id = w.id
               )
           LEFT JOIN stock_daily_data d ON d.watchlist_id = w.id
               AND d.trade_date = (
                   SELECT MAX(d2.trade_date) FROM stock_daily_data d2 WHERE d2.watchlist_id = w.id
               )
           WHERE w.id = ?''',
        (watchlist_id,)
    ).fetchone()
    if not company:
        flash('No such company in the watchlist.', 'error')
        return redirect(url_for('stocks.stocks_watchlist'))

    previous_fundamentals = db.execute(
        '''SELECT pe_ratio, peg_ratio, eps, opm_pct, roce_pct, roa_pct, price_to_book,
                  promoter_holding_pct, fii_holding_pct, quarterly_profit_growth_pct,
                  quarterly_revenue_growth_pct, snapshot_date
           FROM stock_fundamentals
           WHERE watchlist_id=? AND snapshot_date < ?
           ORDER BY snapshot_date DESC LIMIT 1''',
        (watchlist_id, company.get('fundamentals_date') or date.today().isoformat())
    ).fetchone() or {}

    previous_indicators = db.execute(
        '''SELECT rsi_14, ma_5, ma_21, ma_50, ma_200, calc_date
           FROM stock_indicators
           WHERE watchlist_id=? AND calc_date < ?
           ORDER BY calc_date DESC LIMIT 1''',
        (watchlist_id, company.get('indicators_date') or date.today().isoformat())
    ).fetchone() or {}

    company = {
        **company,
        'pe_note': get_metric_note('pe_ratio', company.get('pe_ratio')),
        'opm_note': get_metric_note('opm_pct', company.get('opm_pct')),
        'is_recommended': passes_hard_filters(company),
        'trends': {
            field: trend_note(company.get(field), previous_fundamentals.get(field))
            for field in ('pe_ratio', 'peg_ratio', 'eps', 'opm_pct', 'roce_pct', 'roa_pct',
                          'price_to_book', 'promoter_holding_pct', 'fii_holding_pct',
                          'quarterly_profit_growth_pct', 'quarterly_revenue_growth_pct')
        },
        'fundamentals_trend_as_of': previous_fundamentals.get('snapshot_date'),
        'rsi_trend': trend_note(company.get('rsi_14'), previous_indicators.get('rsi_14')),
        'indicators_trend_as_of': previous_indicators.get('calc_date'),
    }

    # 250 trading days covers the 200-day MA plus margin, and comfortably
    # spans a 52-week window even accounting for market holidays.
    price_history = db.execute(
        '''SELECT trade_date, close, high, low, volume FROM stock_daily_data
           WHERE watchlist_id=? ORDER BY trade_date DESC LIMIT 250''',
        (watchlist_id,)
    ).fetchall()

    closes_desc = [p['close'] for p in price_history if p.get('close') is not None]
    day_change = compute_day_change(closes_desc)
    week52_high, week52_low = compute_52_week_range(
        [p.get('high') for p in price_history], [p.get('low') for p in price_history]
    )
    company['day_change'] = day_change
    company['week52_high'] = week52_high
    company['week52_low'] = week52_low

    closes_oldest_first = list(reversed(closes_desc))
    sparkline_svg = build_price_sparkline_svg(closes_oldest_first)
    sparkline_summary = None
    if len(closes_oldest_first) >= 2:
        period_change_pct = round(
            (closes_oldest_first[-1] - closes_oldest_first[0]) / closes_oldest_first[0] * 100, 2
        ) if closes_oldest_first[0] else None
        sparkline_summary = {
            'days': len(closes_oldest_first),
            'start_price': closes_oldest_first[0],
            'end_price': closes_oldest_first[-1],
            'change_pct': period_change_pct,
        }

    backtest = backtest_rsi_zone_outcomes(closes_oldest_first, company.get('rsi_14'))
    rounding_pattern = detect_rounding_pattern(closes_oldest_first)

    recent_prices = price_history[:15]

    suggestion_history = db.execute(
        '''SELECT suggestion_date, buy_price, target_sell_price, stop_loss_price,
                  holding_period_days, pattern_name, pattern_note, score AS nns_score, nns_tier,
                  pe_at_suggestion, opm_at_suggestion, fundamental_tier, status, rationale
           FROM stock_suggestions WHERE watchlist_id=?
           ORDER BY suggestion_date DESC LIMIT 20''',
        (watchlist_id,)
    ).fetchall()

    return render_template(
        'admin/stocks_company_detail.html',
        company=company, recent_prices=recent_prices, suggestion_history=suggestion_history,
        sparkline_svg=sparkline_svg, sparkline_summary=sparkline_summary, backtest=backtest,
        rounding_pattern=rounding_pattern,
    )


def _can_view_watchlist_signals():
    """True for staff (super_admin/child_admin) and any viewer whose
    can_view_watchlist flag was granted at account creation -- same
    permission stocks_watchlist_access_required (utils/stock_auth.py)
    gates entry to /stocks/watchlist with, reused here to gate DATA
    (golden-cross status, the recommended flag) rather than PAGE ACCESS:
    /stocks/universe and /stocks/universe/<id> are open to every logged-in
    Stocks user, but only show the buy-signal layer to this group -- see
    utils.watchlist_view.redact_recommendation_signals."""
    role = session.get('stocks_admin_role')
    return role in ('super_admin', 'child_admin') or (role == 'viewer' and session.get('stocks_can_view_watchlist'))


UNIVERSE_LIST_PAGE_SIZE = 50


@stocks_bp.route('/stocks/universe', methods=['GET'])
@stocks_login_required
def stocks_universe_list():
    """Every scrape-eligible company (~1,067), not just the ~80-company
    watchlist -- alphabetical by company name, with an optional ?q= search
    (matches company name or symbol, case-insensitive) and simple ?page=
    pagination (UNIVERSE_LIST_PAGE_SIZE per page). Open to every logged-in
    Stocks user regardless of role or the can_view_watchlist flag -- unlike
    /stocks/watchlist, which viewers need that flag for. Golden-cross
    status is still gated, though: see _can_view_watchlist_signals /
    redact_recommendation_signals -- everyone gets the underlying numbers
    (price, PE, OPM, RSI), only staff and flagged viewers get the
    buy-signal layer on top.

    where_sql's NOT EXISTS clause collapses each NSE/BSE ISIN pair down to
    one row (see the comment right above it) -- otherwise the same company
    shows up twice here, once under each exchange's own name for it (e.g.
    'X Ltd' vs 'X Limited')."""
    db = get_db()
    query = (request.args.get('q') or '').strip()
    industry_filter = (request.args.get('industry') or '').strip()
    cross_filter = (request.args.get('cross') or '').strip()
    page = max(1, request.args.get('page', 1, type=int))
    offset = (page - 1) * UNIVERSE_LIST_PAGE_SIZE
    can_view_signals = _can_view_watchlist_signals()
    # A cross_status filter would otherwise let someone without
    # can_view_signals infer the redacted signal indirectly (e.g. "filter to
    # golden_cross" narrows the list to exactly the companies that column
    # would have shown, even with the column itself stripped from the
    # rendered rows) -- so the filter itself is only honoured, not just
    # hidden in the template, when they're actually allowed to see it.
    if cross_filter and not can_view_signals:
        cross_filter = ''

    # Every numeric fundamental/technical filter -- open to every logged-in
    # role (not just staff), same as the rest of this page. Kept as a flat
    # list of (query param, SQL fragment, cast) tuples rather than one
    # per-field if/append block each, since they're all the same shape
    # (a single optional bound, applied only when present and parseable).
    numeric_filters = [
        ('pe_min', 'f.pe_ratio >= ?'), ('pe_max', 'f.pe_ratio <= ?'),
        ('peg_max', 'f.peg_ratio <= ?'),
        ('opm_min', 'f.opm_pct >= ?'),
        ('roce_min', 'f.roce_pct >= ?'),
        ('roa_min', 'f.roa_pct >= ?'),
        ('rsi_min', 'i.rsi_14 >= ?'), ('rsi_max', 'i.rsi_14 <= ?'),
    ]
    numeric_values = {}
    for arg_name, _fragment in numeric_filters:
        raw = (request.args.get(arg_name) or '').strip()
        if not raw:
            continue
        try:
            numeric_values[arg_name] = float(raw)
        except ValueError:
            pass  # silently ignored, same as any other malformed query param

    # Same company, two stock_universe rows -- NSE and BSE each get their
    # own row (see utils/stock_universe.propagate_bse_market_cap_to_nse's
    # docstring: "never merges or deletes either row", kept apart on
    # purpose so both sides of the ISIN pair can independently supply
    # market cap data). Our NSE and BSE source lists also don't agree on
    # 'Ltd' vs 'Limited' etc. for the same company (see
    # stock_shortlist._pick_canonical_listing's identical note), so
    # without this the exact same company shows up twice here under two
    # different names. Rather than touching the underlying rows (which
    # the watchlist shortlist step still needs both of, to later pick
    # whichever listing actually resolves a Kite instrument token), this
    # collapses each ISIN pair down to one row for THIS browse list only
    # -- the NSE listing when one exists, else whichever row has the
    # lower id. A row with no ISIN on record can't be identified as part
    # of a pair at all, so it's always shown regardless.
    where_sql = (
        'WHERE u.is_scrape_eligible = true '
        'AND NOT EXISTS ('
        '  SELECT 1 FROM stock_universe u2 '
        '  WHERE u2.isin = u.isin AND u2.isin IS NOT NULL AND u2.isin != \'\' '
        '    AND u2.id != u.id '
        '    AND ((u2.exchange = \'NSE\' AND u.exchange != \'NSE\') OR (u2.exchange = u.exchange AND u2.id < u.id))'
        ')'
    )
    params = []
    if query:
        where_sql += ' AND (u.company_name ILIKE ? OR u.symbol ILIKE ?)'
        like_query = f'%{query}%'
        params += [like_query, like_query]
    if industry_filter:
        where_sql += ' AND u.industry = ?'
        params.append(industry_filter)

    industries = db.execute(
        'SELECT DISTINCT industry FROM stock_universe '
        'WHERE is_scrape_eligible = true AND industry IS NOT NULL ORDER BY industry'
    ).fetchall()
    industries = [r['industry'] for r in industries]

    # cross_filter and every numeric filter narrow on the LEFT JOINed
    # fundamentals/indicators tables, so they have to be applied after
    # those joins exist -- built as a separate fragment rather than folded
    # into where_sql (which the COUNT(*) query below also uses, joined the
    # same way for exactly this reason).
    having_sql = ''
    if cross_filter:
        having_sql += ' AND i.cross_status = ?'
        params.append(cross_filter)
    for arg_name, fragment in numeric_filters:
        if arg_name in numeric_values:
            having_sql += f' AND {fragment}'
            params.append(numeric_values[arg_name])

    total = db.execute(
        f'''SELECT COUNT(*) AS c FROM stock_universe u
            LEFT JOIN stock_fundamentals f ON f.universe_id = u.id
                AND f.snapshot_date = (SELECT MAX(f2.snapshot_date) FROM stock_fundamentals f2 WHERE f2.universe_id = u.id)
            LEFT JOIN stock_indicators i ON i.universe_id = u.id
                AND i.calc_date = (SELECT MAX(i2.calc_date) FROM stock_indicators i2 WHERE i2.universe_id = u.id)
            {where_sql}{having_sql}''',
        tuple(params)
    ).fetchone()['c']

    rows = db.execute(
        f'''SELECT u.id AS universe_id, u.symbol, u.exchange, u.company_name, u.industry,
                   f.pe_ratio, f.peg_ratio, f.opm_pct, f.roce_pct, f.roa_pct,
                   i.rsi_14, i.cross_status,
                   d.close AS latest_price
            FROM stock_universe u
            LEFT JOIN stock_fundamentals f ON f.universe_id = u.id
                AND f.snapshot_date = (SELECT MAX(f2.snapshot_date) FROM stock_fundamentals f2 WHERE f2.universe_id = u.id)
            LEFT JOIN stock_indicators i ON i.universe_id = u.id
                AND i.calc_date = (SELECT MAX(i2.calc_date) FROM stock_indicators i2 WHERE i2.universe_id = u.id)
            LEFT JOIN stock_daily_data d ON d.universe_id = u.id
                AND d.trade_date = (SELECT MAX(d2.trade_date) FROM stock_daily_data d2 WHERE d2.universe_id = u.id)
            {where_sql}{having_sql}
            ORDER BY u.company_name ASC
            LIMIT ? OFFSET ?''',
        tuple(params) + (UNIVERSE_LIST_PAGE_SIZE, offset)
    ).fetchall()
    rows = redact_recommendation_signals(rows, can_view_signals)

    total_pages = max(1, -(-total // UNIVERSE_LIST_PAGE_SIZE))  # ceil division

    saved_filters = list_saved_stock_filters(db, session.get('stocks_admin_id'))
    # Everything the user could plausibly want to save/re-apply later --
    # deliberately excludes page (a saved filter should always reopen on
    # page 1, not wherever pagination happened to be when it was saved).
    filters_for_saving = {'q': query, 'industry': industry_filter, 'cross': cross_filter, **numeric_values}
    filters_query_string = urlencode({k: v for k, v in filters_for_saving.items() if v not in (None, '')})

    return render_template(
        'admin/stocks_universe_list.html',
        rows=rows, query=query, page=page, total=total, total_pages=total_pages,
        can_view_signals=can_view_signals, industries=industries,
        industry_filter=industry_filter, cross_filter=cross_filter,
        numeric_values=numeric_values, saved_filters=saved_filters,
        filters_query_string=filters_query_string,
    )


@stocks_bp.route('/stocks/universe/filters/save', methods=['POST'])
@stocks_login_required
def stocks_universe_filters_save():
    """Saves the current /stocks/universe filter combination (passed back
    as a hidden field, not re-derived from request.args -- see the
    template's save form) under a name, for the logged-in account. Open to
    any role, same as the universe page itself -- see utils/saved_filters.py."""
    db = get_db()
    name = request.form.get('name')
    query_string = request.form.get('query_string')
    _row, error = save_stock_filter(db, session.get('stocks_admin_id'), name, query_string)
    if error:
        flash(error, 'error')
    else:
        flash(f'Saved filter "{name}".', 'info')
    redirect_target = f'{url_for("stocks.stocks_universe_list")}?{query_string}' if query_string else url_for('stocks.stocks_universe_list')
    return redirect(redirect_target)


@stocks_bp.route('/stocks/universe/filters/<int:filter_id>/delete', methods=['POST'])
@stocks_login_required
def stocks_universe_filters_delete(filter_id):
    db = get_db()
    delete_saved_stock_filter(db, session.get('stocks_admin_id'), filter_id)
    return redirect(url_for('stocks.stocks_universe_list'))


def _suggestion_history_for_company(db, watchlist_id):
    """Every recommendation ever made for this company, across all three
    suggestion engines (daily Pick of the Day, Starters weekly pick,
    Standard-tier bonus large-cap pick) -- shown on the company/universe
    detail page as "Recommended on [date] -- view analysis for this pick"
    (see stocks_universe_detail below), so a reader sees a company's full
    recommendation history in one place without three separate sections
    duplicating the same table shape per engine. watchlist_id is None for
    a universe-only company that's never been shortlisted -- returns []
    immediately rather than querying three tables for nothing.

    Returns [{'source', 'suggestion_id', 'date'}, ...], most recent
    first -- source matches _ANALYSIS_SOURCE_LABELS' keys, used both for
    that label and to build the /stocks/analysis/<source>/<id> link."""
    if not watchlist_id:
        return []
    history = []
    daily = db.execute(
        'SELECT id AS suggestion_id, suggestion_date AS date FROM stock_suggestions '
        'WHERE watchlist_id=? ORDER BY suggestion_date DESC',
        (watchlist_id,)
    ).fetchall()
    history += [{'source': 'daily', 'suggestion_id': r['suggestion_id'], 'date': r['date']} for r in daily]
    starters = db.execute(
        'SELECT id AS suggestion_id, week_start_date AS date FROM stock_starters_suggestions '
        'WHERE watchlist_id=? ORDER BY week_start_date DESC',
        (watchlist_id,)
    ).fetchall()
    history += [{'source': 'starters', 'suggestion_id': r['suggestion_id'], 'date': r['date']} for r in starters]
    large_cap = db.execute(
        'SELECT id AS suggestion_id, suggestion_date AS date FROM stock_large_cap_bonus_suggestions '
        'WHERE watchlist_id=? ORDER BY suggestion_date DESC',
        (watchlist_id,)
    ).fetchall()
    history += [{'source': 'large_cap', 'suggestion_id': r['suggestion_id'], 'date': r['date']} for r in large_cap]
    history.sort(key=lambda r: r['date'], reverse=True)
    return history


@stocks_bp.route('/stocks/universe/<int:universe_id>', methods=['GET'])
@stocks_login_required
def stocks_universe_detail(universe_id):
    """Same read-only detail view as /stocks/company/<watchlist_id> (day
    change, 52-week range, price sparkline, RSI backtest, rounding
    pattern, fundamentals, technicals, recent prices), but for ANY
    scrape-eligible universe company, not just the ~80 on the watchlist --
    see stocks_universe_list for who can reach this and why. No
    "Suggestion history" section here: suggestions are only ever generated
    for watchlist companies (see suggestion_engine.py), so a universe-only
    company never has any. Golden-cross status and the recommended flag
    are only included for _can_view_watchlist_signals()."""
    db = get_db()
    can_view_signals = _can_view_watchlist_signals()

    company = db.execute(
        '''SELECT u.id AS universe_id, u.symbol, u.exchange, u.company_name AS name, u.industry,
                  f.pe_ratio, f.peg_ratio, f.eps, f.market_cap, f.roe, f.debt_to_equity,
                  f.earnings_growth_pct, f.sector_avg_pe, f.price_to_book, f.opm_pct,
                  f.roce_pct, f.roa_pct, f.current_ratio, f.tol_by_tnw,
                  f.promoter_holding_pct, f.fii_holding_pct, f.public_holding_pct,
                  f.quarterly_profit_growth_pct, f.quarterly_revenue_growth_pct,
                  f.free_cash_flow, f.snapshot_date AS fundamentals_date,
                  i.ma_5, i.ma_21, i.ma_50, i.ma_200, i.rsi_14, i.volume_avg_20d,
                  i.cross_status, i.volume_trend, i.calc_date AS indicators_date,
                  d.close AS latest_price, d.trade_date AS price_date
           FROM stock_universe u
           LEFT JOIN stock_fundamentals f ON f.universe_id = u.id
               AND f.snapshot_date = (
                   SELECT MAX(f2.snapshot_date) FROM stock_fundamentals f2 WHERE f2.universe_id = u.id
               )
           LEFT JOIN stock_indicators i ON i.universe_id = u.id
               AND i.calc_date = (
                   SELECT MAX(i2.calc_date) FROM stock_indicators i2 WHERE i2.universe_id = u.id
               )
           LEFT JOIN stock_daily_data d ON d.universe_id = u.id
               AND d.trade_date = (
                   SELECT MAX(d2.trade_date) FROM stock_daily_data d2 WHERE d2.universe_id = u.id
               )
           WHERE u.id = ?''',
        (universe_id,)
    ).fetchone()
    if not company:
        flash('No such company.', 'error')
        return redirect(url_for('stocks.stocks_universe_list'))

    previous_fundamentals = db.execute(
        '''SELECT pe_ratio, peg_ratio, eps, opm_pct, roce_pct, roa_pct, price_to_book,
                  promoter_holding_pct, fii_holding_pct, quarterly_profit_growth_pct,
                  quarterly_revenue_growth_pct, snapshot_date
           FROM stock_fundamentals
           WHERE universe_id=? AND snapshot_date < ?
           ORDER BY snapshot_date DESC LIMIT 1''',
        (universe_id, company.get('fundamentals_date') or date.today().isoformat())
    ).fetchone() or {}

    previous_indicators = db.execute(
        '''SELECT rsi_14, ma_5, ma_21, ma_50, ma_200, calc_date
           FROM stock_indicators
           WHERE universe_id=? AND calc_date < ?
           ORDER BY calc_date DESC LIMIT 1''',
        (universe_id, company.get('indicators_date') or date.today().isoformat())
    ).fetchone() or {}

    company = {
        **company,
        'pe_note': get_metric_note('pe_ratio', company.get('pe_ratio')),
        'opm_note': get_metric_note('opm_pct', company.get('opm_pct')),
        'is_recommended': passes_hard_filters(company),
        'trends': {
            field: trend_note(company.get(field), previous_fundamentals.get(field))
            for field in ('pe_ratio', 'peg_ratio', 'eps', 'opm_pct', 'roce_pct', 'roa_pct',
                          'price_to_book', 'promoter_holding_pct', 'fii_holding_pct',
                          'quarterly_profit_growth_pct', 'quarterly_revenue_growth_pct')
        },
        'fundamentals_trend_as_of': previous_fundamentals.get('snapshot_date'),
        'rsi_trend': trend_note(company.get('rsi_14'), previous_indicators.get('rsi_14')),
        'indicators_trend_as_of': previous_indicators.get('calc_date'),
    }

    price_history = db.execute(
        '''SELECT trade_date, close, high, low, volume FROM stock_daily_data
           WHERE universe_id=? ORDER BY trade_date DESC LIMIT 250''',
        (universe_id,)
    ).fetchall()

    closes_desc = [p['close'] for p in price_history if p.get('close') is not None]
    day_change = compute_day_change(closes_desc)
    week52_high, week52_low = compute_52_week_range(
        [p.get('high') for p in price_history], [p.get('low') for p in price_history]
    )
    company['day_change'] = day_change
    company['week52_high'] = week52_high
    company['week52_low'] = week52_low

    closes_oldest_first = list(reversed(closes_desc))
    sparkline_svg = build_price_sparkline_svg(closes_oldest_first)
    sparkline_summary = None
    if len(closes_oldest_first) >= 2:
        period_change_pct = round(
            (closes_oldest_first[-1] - closes_oldest_first[0]) / closes_oldest_first[0] * 100, 2
        ) if closes_oldest_first[0] else None
        sparkline_summary = {
            'days': len(closes_oldest_first),
            'start_price': closes_oldest_first[0],
            'end_price': closes_oldest_first[-1],
            'change_pct': period_change_pct,
        }

    backtest = backtest_rsi_zone_outcomes(closes_oldest_first, company.get('rsi_14'))
    rounding_pattern = detect_rounding_pattern(closes_oldest_first)
    recent_prices = price_history[:15]

    # A universe-only company (never shortlisted to stock_watchlist) has no
    # watchlist_id and therefore no suggestion history at all -- see
    # _suggestion_history_for_company's own None-guard.
    watchlist_row = db.execute(
        'SELECT id FROM stock_watchlist WHERE symbol=? AND exchange=?',
        (company['symbol'], company['exchange'])
    ).fetchone()
    suggestion_history = _suggestion_history_for_company(db, watchlist_row['id'] if watchlist_row else None)

    if not can_view_signals:
        company = redact_recommendation_signals([company], can_view_signals=False)[0]

    return render_template(
        'admin/stocks_universe_detail.html',
        company=company, recent_prices=recent_prices,
        sparkline_svg=sparkline_svg, sparkline_summary=sparkline_summary, backtest=backtest,
        rounding_pattern=rounding_pattern, can_view_signals=can_view_signals,
        suggestion_history=suggestion_history, analysis_source_labels=_ANALYSIS_SOURCE_LABELS,
    )


def _annotate_suggestions_with_projection(suggestions):
    """Adds a 'projection' key (see price_pattern.compute_projection_targets)
    to each suggestion row -- this stock's own mid-period/long-term price
    projection, grounded in whichever chart pattern actually drove its
    target_sell_price when there is one. Mirrors what the daily email
    already computes per suggestion (see suggestion_email.py's
    _render_stock_card_html/_render_stock_card_text) so the viewer pages
    (/stocks/my/suggestions, /stocks/my/history) show the same figures,
    not just the email. Returns new dicts -- does not mutate the input rows."""
    annotated = []
    for row in suggestions:
        row = dict(row)
        row['projection'] = compute_projection_targets(
            row.get('buy_price'), row.get('target_sell_price'), row.get('pattern_name')
        )
        annotated.append(row)
    return annotated


@stocks_bp.route('/stocks/home', methods=['GET'])
@stocks_login_required
def stocks_home():
    """The post-login landing page for viewer accounts (see
    stocks_admin_login's redirect below) -- a minimalistic summary
    combining how many suggestions this subscriber has personally received
    recently, a link out to browse every currently quality-screened stock
    (see stocks_quality_stocks below -- kept as a link rather than an
    inline widget here, to keep this page light), and an industry-wise
    growth snapshot built from our own tracked universe (see
    utils/industry_growth.py -- NOT real Nifty/Bank Nifty/Sensex index
    values; there is no live market-index data source anywhere in this
    codebase, so the template labels this explicitly as our own
    tracked-universe average, not an official index).

    Staff (super_admin/child_admin) can reach this too (no role gate,
    consistent with stocks_my_suggestions/stocks_my_history below) but
    their own post-login redirect still goes straight to
    stocks_admin_dashboard -- landing here is only ever a deliberate visit
    for them, so the personal-suggestions section is skipped entirely
    (staff accounts don't have suggestions genuinely "sent to them")."""
    db = get_db()
    is_viewer = session.get('stocks_admin_role') == 'viewer'

    suggestion_summary = None
    if is_viewer:
        is_starters = session.get('stocks_plan') == 'starters'
        if is_starters:
            start_date = (date.today() - timedelta(days=63)).isoformat()
            recent_suggestions = [dict(r, source='starters') for r in get_starters_suggestions(db, start_date=start_date)]
        else:
            start_date = (date.today() - timedelta(days=HOLDING_PERIOD_DAYS)).isoformat()
            recent_suggestions = [dict(r, source='daily') for r in get_suggestions(db, start_date=start_date)]
        recent_bonus = []
        if session.get('stocks_plan') == 'standard':
            bonus_start_date = (date.today() - timedelta(days=30)).isoformat()
            recent_bonus = [dict(r, source='large_cap') for r in get_large_cap_bonus_suggestions(db, start_date=bonus_start_date)]
        combined = recent_suggestions + recent_bonus
        combined.sort(key=lambda r: r['suggestion_date'], reverse=True)
        suggestion_summary = {
            'count': len(combined),
            'latest': combined[:3],
            'is_starters': is_starters,
        }

    industry_growth = compute_industry_growth(db, top_n=5)

    return render_template(
        'admin/stocks_home.html',
        suggestion_summary=suggestion_summary,
        industry_growth=industry_growth,
    )


@stocks_bp.route('/stocks/quality-stocks', methods=['GET'])
@stocks_login_required
def stocks_quality_stocks():
    """Full, uncapped list of every stock currently clearing our
    golden/silver NNS Score bar (see get_top_stocks) -- linked from the
    Stocks home page as "Browse all quality stocks" (replaces what was
    previously a capped "Top Stocks" preview widget directly on the home
    page). Same viewer-safe policy as get_top_stocks itself: tier badge
    only, never the raw score."""
    db = get_db()
    quality_stocks = get_top_stocks(db, limit=None)
    return render_template('admin/stocks_quality_stocks.html', quality_stocks=quality_stocks)


def _pct_increase(buy_price, price):
    """'+12.3' style figure for how much higher a target/projection price
    is than the buy price -- used by stocks_suggestion_analysis below to
    show the same %-increase context the daily/Starters/bonus emails
    already show beside each target value (see
    utils.suggestion_email._pct_increase, which this mirrors -- kept as
    its own copy rather than importing that module's underscore-prefixed,
    email-specific helper). Returns None when buy_price isn't a usable
    positive number."""
    if not buy_price or buy_price <= 0 or price is None:
        return None
    return round((price - buy_price) / buy_price * 100, 1)


# Which suggestion table/engine each /stocks/analysis/<source>/<id> URL
# reads from -- see stocks_suggestion_analysis below. Also doubles as the
# human-readable label shown on that page and in the suggestion-history
# table on the company/universe detail page.
_ANALYSIS_SOURCE_LABELS = {
    'daily': 'Pick of the Day',
    'starters': 'Starters Weekly Pick',
    'large_cap': 'Bonus Large-Cap Pick',
}


@stocks_bp.route('/stocks/analysis/<source>/<int:suggestion_id>', methods=['GET'])
@stocks_login_required
def stocks_suggestion_analysis(source, suggestion_id):
    """Full explanation for one specific recommendation -- company, date,
    buy/target price, tier, rationale, chart-pattern name/note (or plain
    holding-period timing), fundamentals snapshot (RSI/PE/PEG/OPM at
    suggestion time, whichever are on record, any tier -- not just
    silver), the projection chart image, and mid/long-term projection with
    % increase from the buy price -- the same figures and the same chart
    image the emails already show (see
    utils.suggestion_email._render_stock_card_html/build_prediction_chart_image_url).
    Every suggestion from any of the three engines requires golden-cross
    at generation time (see suggestion_engine.is_suggestion_eligible) --
    this page states that directly rather than leaving it implicit, since
    the email itself never spells it out either. Linked from the Stocks
    home page's Your Suggestions table ("Analysis") and from the
    company/universe detail page's own suggestion-history table ("View
    analysis for this pick").

    source picks which of the three suggestion tables/engines this id
    belongs to -- 'daily' (stock_suggestions, the regular Pick of the
    Day), 'starters' (stock_starters_suggestions, the Rs 99/mo weekly
    pick), or 'large_cap' (stock_large_cap_bonus_suggestions, the
    Standard-tier bonus pick) -- since a bare integer id alone can't
    disambiguate between three separate tables each with their own
    independent id sequence."""
    if source not in _ANALYSIS_SOURCE_LABELS:
        flash('Unknown recommendation type.', 'error')
        return redirect(url_for('stocks.stocks_home'))

    db = get_db()
    if source == 'daily':
        suggestion = get_suggestion_by_id(db, suggestion_id)
    elif source == 'starters':
        suggestion = get_starters_suggestion_by_id(db, suggestion_id)
    else:
        suggestion = get_large_cap_bonus_suggestion_by_id(db, suggestion_id)

    if not suggestion:
        flash('No such recommendation.', 'error')
        return redirect(url_for('stocks.stocks_home'))

    projection = compute_projection_targets(
        suggestion.get('buy_price'), suggestion.get('target_sell_price'), suggestion.get('pattern_name')
    )
    target_pct = _pct_increase(suggestion.get('buy_price'), suggestion.get('target_sell_price'))
    mid_pct = _pct_increase(suggestion.get('buy_price'), projection.get('mid_period', {}).get('price')) if projection else None
    long_pct = _pct_increase(suggestion.get('buy_price'), projection.get('long_term', {}).get('price')) if projection else None
    # Same chart the daily/weekly/bonus email itself embedded (see
    # utils/suggestion_email.py's _render_stock_card_html) -- content-
    # addressed by pixel content (see build_prediction_chart_image_url's
    # own docstring), so this reuses the exact file already uploaded for
    # this suggestion's numbers rather than uploading a fresh copy.
    chart_url = build_prediction_chart_image_url(
        suggestion.get('buy_price'), projection, suggestion.get('stop_loss_price')
    )

    return render_template(
        'admin/stocks_suggestion_analysis.html',
        s=suggestion, source=source, source_label=_ANALYSIS_SOURCE_LABELS[source],
        projection=projection, target_pct=target_pct, mid_pct=mid_pct, long_pct=long_pct,
        chart_url=chart_url,
    )


def _stocks_viewer_account_summary(db, admin_id):
    """Subscription status/renewal + referral code/link/stats for a
    logged-in Stocks account -- shared by stocks_my_suggestions and
    stocks_profile so this lookup isn't duplicated across both. Returns a
    flat dict whose keys match the template variable names both pages
    already use (subscription_status, subscription_period_end_label,
    trial_ends_at_label, can_cancel_subscription, referral_code,
    referral_link, qualified_referrals, available_referral_credits,
    referrals_per_free_month) -- callers pass it straight through to
    render_template via **."""
    subscription_row = db.execute(
        'SELECT subscription_status, subscription_current_period_end, trial_ends_at FROM stocks_admin_users WHERE id=?',
        (admin_id,)
    ).fetchone()
    subscription_period_end_label = None
    if subscription_row and subscription_row.get('subscription_status') in ('active', 'cancelled', 'halted') \
            and subscription_row.get('subscription_current_period_end'):
        end_value = subscription_row['subscription_current_period_end']
        end_dt = datetime.fromisoformat(str(end_value).replace('Z', '+00:00')) if isinstance(end_value, str) else end_value
        subscription_period_end_label = end_dt.strftime('%d %b %Y')

    trial_ends_at_label = None
    if subscription_row and subscription_row.get('subscription_status') == 'trialing' \
            and subscription_row.get('trial_ends_at'):
        trial_value = subscription_row['trial_ends_at']
        trial_dt = datetime.fromisoformat(str(trial_value).replace('Z', '+00:00')) if isinstance(trial_value, str) else trial_value
        trial_ends_at_label = trial_dt.strftime('%d %b %Y')

    # Cancel button only for a real, currently-active PAID subscription --
    # a free trial has no Razorpay mandate at all to cancel (see
    # stocks_subscription_cancel's own docstring for the matching
    # server-side check, this is just what decides whether to show the
    # button in the first place).
    can_cancel_subscription = bool(subscription_row and subscription_row.get('subscription_status') == 'active')

    # Referral status (see utils/stocks_referrals.py) -- lazily generates a
    # code on first view for an account that's never had one (existing
    # accounts predating this feature weren't backfilled).
    referral_code = get_or_create_referral_code(db, admin_id)
    referral_link = f'https://{STOCKS_DOMAIN}/stocks/signup?ref={referral_code}'
    qualified_referrals = count_qualified_referrals(db, admin_id)
    available_credits = available_referral_credits(db, admin_id)

    return {
        'subscription_status': subscription_row.get('subscription_status') if subscription_row else None,
        'subscription_period_end_label': subscription_period_end_label,
        'trial_ends_at_label': trial_ends_at_label,
        'can_cancel_subscription': can_cancel_subscription,
        'referral_code': referral_code, 'referral_link': referral_link,
        'qualified_referrals': qualified_referrals, 'available_referral_credits': available_credits,
        'referrals_per_free_month': REFERRALS_PER_FREE_MONTH,
    }


@stocks_bp.route('/stocks/profile', methods=['GET'])
@stocks_login_required
def stocks_profile():
    """Account/profile page -- username, role/plan, subscription status
    and renewal date, and referral code/link/stats, all in one place --
    linked from the Stocks nav bar (see stocks_home.html). Reuses the
    exact same subscription/referral lookup stocks_my_suggestions already
    does (see _stocks_viewer_account_summary) rather than duplicating it."""
    db = get_db()
    admin_id = session.get('stocks_admin_id')
    account_summary = _stocks_viewer_account_summary(db, admin_id)
    return render_template(
        'admin/stocks_profile.html',
        username=session.get('stocks_admin_username'),
        role=session.get('stocks_admin_role'),
        plan=session.get('stocks_plan'),
        **account_summary,
    )


@stocks_bp.route('/stocks/subscription/upgrade-now', methods=['GET'])
@stocks_login_required
def stocks_subscription_upgrade_now():
    """'Subscribe now' from /stocks/profile during an active trial --
    lets someone pay early instead of waiting for their trial to run out.
    Can't just point this at /stocks/signup: that account already has
    working trial access, so create_pending_subscriber's 'existing' branch
    would see has_stocks_access already True and bounce them to "please
    log in" instead of checkout. Reuses /stocks/plans' 'resubscribe' mode
    instead (see stocks_plans_continue) -- same "choose a plan, go straight
    to real Razorpay checkout, no second trial" path an expired trial uses,
    just reached voluntarily instead of via a lapsed-access redirect."""
    db = get_db()
    row = db.execute('SELECT name FROM stocks_admin_users WHERE id=?', (session['stocks_admin_id'],)).fetchone()
    session['stocks_plans_context'] = {
        'mode': 'resubscribe', 'admin_id': session['stocks_admin_id'],
        'email': session.get('stocks_admin_username'), 'name': row.get('name') if row else None,
    }
    session.modified = True
    return redirect(url_for('stocks.stocks_plans'))


@stocks_bp.route('/stocks/subscription/cancel', methods=['POST'])
@stocks_login_required
def stocks_subscription_cancel():
    """Self-serve cancellation from /stocks/profile's 'Cancel subscription'
    button. Cancels at the end of the current billing cycle
    (cancel_at_cycle_end=1) rather than immediately -- access keeps
    working through whatever period was already paid for, exactly what
    subscription_is_current already assumes for a 'cancelled' row (see its
    own docstring) and exactly what the profile page displays
    ("Cancelled -- access ends <date>"). Updates the local row immediately
    via mark_subscription_cancelled rather than waiting on Razorpay's own
    'subscription.cancelled' webhook (which only fires once the cycle
    actually ends for a cycle-end cancellation) -- the visitor who just
    clicked Cancel should see it reflected on the very next page load, not
    days later.

    Only ever acts on a real, currently 'active' PAID subscription -- a
    free trial has no Razorpay mandate to cancel at all (see
    _stocks_viewer_account_summary's can_cancel_subscription, which is
    what decides whether the button even shows), and a row that's already
    cancelled/halted/pending has nothing further to cancel."""
    admin_id = session.get('stocks_admin_id')
    db = get_db()
    row = db.execute(
        'SELECT subscription_status, razorpay_subscription_id FROM stocks_admin_users WHERE id=?',
        (admin_id,)
    ).fetchone()
    if not row or row.get('subscription_status') != 'active' or not row.get('razorpay_subscription_id'):
        flash('No active subscription to cancel.', 'error')
        return redirect(url_for('stocks.stocks_profile'))

    try:
        razorpay_client.subscription.cancel(row['razorpay_subscription_id'], data={'cancel_at_cycle_end': 1})
    except Exception as e:
        current_app.logger.error(f'Razorpay subscription cancel failed for admin_id={admin_id}: {e}')
        flash('Could not cancel right now -- please try again shortly.', 'error')
        return redirect(url_for('stocks.stocks_profile'))

    mark_subscription_cancelled(db, row['razorpay_subscription_id'])
    flash('Your subscription has been cancelled -- access continues until your current billing period ends.', 'info')
    return redirect(url_for('stocks.stocks_profile'))


@stocks_bp.route('/stocks/my/suggestions', methods=['GET'])
@stocks_login_required
def stocks_my_suggestions():
    """Read-only, any logged-in role (this is viewer's own landing page,
    but staff can see it too -- no edit/execute controls here regardless of
    role). Shows suggestions from the last HOLDING_PERIOD_DAYS days, using
    the same get_suggestions() query the daily email and /stocks/my/history
    both use.

    A stocks_plan='starters' account (see STOCKS_AUTH_ALTER_SQL) sees its
    own weekly-curated pick history instead (get_starters_suggestions,
    utils/starters_engine.py) -- a daily Pick of the Day list would be the
    wrong content entirely for what they're actually paying for. Staff
    (super_admin/child_admin) always default to 'standard' and never have
    this changed, so they keep seeing the daily view regardless."""
    db = get_db()
    admin_id = session.get('stocks_admin_id')
    is_starters = session.get('stocks_plan') == 'starters'
    if is_starters:
        # ~9 weeks -- enough recent weekly picks to be worth showing on the
        # landing page without becoming the full all-time list (that's
        # stocks_my_history's job below).
        start_date = (date.today() - timedelta(days=63)).isoformat()
        suggestions = _annotate_suggestions_with_projection(get_starters_suggestions(db, start_date=start_date))
    else:
        start_date = (date.today() - timedelta(days=HOLDING_PERIOD_DAYS)).isoformat()
        suggestions = _annotate_suggestions_with_projection(get_suggestions(db, start_date=start_date))

    account_summary = _stocks_viewer_account_summary(db, admin_id)

    return render_template(
        'admin/stocks_my_suggestions.html', suggestions=suggestions, is_starters=is_starters,
        **account_summary,
    )


@stocks_bp.route('/stocks/my/history', methods=['GET'])
@stocks_login_required
def stocks_my_history():
    """Read-only, any logged-in role. All-time suggestion history --
    unlike the name might suggest, there's no real outcome/ROI tracking
    yet (nothing ever moves a suggestion's status away from 'pending', see
    execute_suggestion in an earlier deferred phase), so every row's
    status genuinely reads 'Pending' here rather than a fabricated
    result.

    Branches to the Starters weekly-pick history the same way
    stocks_my_suggestions does -- see that route's docstring."""
    db = get_db()
    is_starters = session.get('stocks_plan') == 'starters'
    if is_starters:
        suggestions = _annotate_suggestions_with_projection(get_starters_suggestions(db))
    else:
        suggestions = _annotate_suggestions_with_projection(get_suggestions(db))
    return render_template('admin/stocks_my_history.html', suggestions=suggestions, is_starters=is_starters)


@stocks_bp.route('/stocks/recommendations/tracker', methods=['GET'])
@stocks_role_required('super_admin', 'child_admin')
def stocks_recommendations_tracker():
    """Staff-only: every Pick of the Day ever sent, all-time, with its
    current price, profit/loss since the suggestion, days elapsed, and
    whether it's hit its target or stop-loss yet -- see
    utils.suggestion_engine.get_recommendation_tracker/compute_tracker_row_stats.
    Each row links to /stocks/company/<watchlist_id>, which already has both
    the full reasoning for every suggestion on that company (its suggestion
    history section) and the company's full current stock details -- one
    page covers both halves of what this links out to, so there's no
    separate per-suggestion detail page to build."""
    db = get_db()
    rows = get_recommendation_tracker(db)
    tracker_rows = []
    for row in rows:
        row = dict(row)
        row.update(compute_tracker_row_stats(
            row.get('buy_price'), row.get('target_sell_price'), row.get('stop_loss_price'),
            row.get('latest_price'), row['suggestion_date'], target_hit_date=row.get('target_hit_date'),
        ))
        row['projection'] = compute_projection_targets(
            row.get('buy_price'), row.get('target_sell_price'), row.get('pattern_name')
        )
        tracker_rows.append(row)
    return render_template('admin/stocks_recommendation_tracker.html', tracker_rows=tracker_rows)


@stocks_bp.route('/stocks/special-recommendations', methods=['GET'])
@stocks_role_required('super_admin')
def stocks_special_recommendations():
    """super_admin-only (narrower than every other staff-facing Stocks
    page in this codebase, which all also admit child_admin, per
    instruction) -- every currently golden-cross-eligible,
    quality-clearing candidate ranked score-descending, live, from the
    FULL scrape-eligible stock_universe (~1,067 companies), not just the
    ~80-company watchlist stocks_watchlist covers. Same buy/target/tier/
    score/pattern detail a real suggestion would show, computed on the
    fly -- read-only, never inserts a stock_suggestions row, never
    affects any cooldown, never emailed to anyone. See
    utils.suggestion_engine.get_special_recommendations_today."""
    db = get_db()
    picks = get_special_recommendations_today(db)
    return render_template('admin/stocks_special_recommendations.html', picks=picks)


def _kite_client_for_auto_trade(db, settings):
    """Returns a KiteClient when settings['mode'] == 'live' (real orders
    need a real session), None for dry_run (never touches Kite at all).
    Raises the same clear error stocks_super_sync etc. already surface
    when no Kite session exists yet, rather than a bare AttributeError
    later when a None client gets called."""
    if settings['mode'] != MODE_LIVE:
        return None
    access_token = get_kite_access_token(db)
    if not access_token:
        raise KiteClientError('No Kite session yet -- a super_admin must log in via /admin/stocks/kite/login first.')
    return KiteClient(access_token=access_token)


# How many recent, still-actionable recommendations the auto-trader
# dashboard's quick-buy list shows at once (see stocks_auto_trader below) --
# a cap, not a business rule; just keeps the list from growing unbounded.
BUYABLE_RECOMMENDATIONS_LIMIT = 20


@stocks_bp.route('/stocks/auto-trader', methods=['GET'])
@stocks_role_required('super_admin')
def stocks_auto_trader():
    """Auto-trading dashboard -- super_admin only (see utils/auto_trader.py's
    module docstring for exactly what dry_run vs live mode each do, and the
    stop-loss handling that's identical in both: never auto-sold, always a
    manual Proceed/Cancel). Shows every trade ever opened, newest first,
    with running P&L, plus a quick-buy list of recent recommendations below
    (see BUYABLE_RECOMMENDATIONS_LIMIT) so a manual buy doesn't require a
    trip to the separate Recommendation Tracker page."""
    db = get_db()
    settings = get_auto_trade_settings(db)
    trades = list_auto_trades(db)
    open_count = sum(1 for t in trades if t['status'] in ('open', 'pending_buy'))
    pending_count = sum(1 for t in trades if t['status'] in ('stop_loss_pending', 'pending_sell'))
    closed_trades = [t for t in trades if t['status'] in ('target_hit', 'stopped_out')]
    total_pnl = sum(t['pnl_amount'] for t in closed_trades if t.get('pnl_amount') is not None)
    win_count = sum(1 for t in closed_trades if t['status'] == 'target_hit')
    deployed_capital = get_deployed_capital(db, settings['mode'])
    available_funds = compute_available_funds(settings['total_capital'], deployed_capital)

    # Quick-buy list: recent recommendations that are still "live" (haven't
    # already hit target or what would've been their stop-loss level, per
    # the latest synced price) and don't already have a trade against them
    # -- capped to the most recent BUYABLE_RECOMMENDATIONS_LIMIT so this
    # doesn't turn into the full all-time tracker. Manual buys placed from
    # here (or from the tracker page) never carry a stop-loss -- see
    # open_manual_trade.
    already_bought_suggestion_ids = {t['suggestion_id'] for t in trades if t.get('suggestion_id')}
    buyable_recommendations = []
    for row in get_recommendation_tracker(db):
        if row['id'] in already_bought_suggestion_ids:
            continue
        stats = compute_tracker_row_stats(
            row.get('buy_price'), row.get('target_sell_price'), row.get('stop_loss_price'),
            row.get('latest_price'), row['suggestion_date'],
        )
        if stats['outcome'] not in ('open', 'unknown'):
            continue
        buyable_recommendations.append({**row, **stats})
        if len(buyable_recommendations) >= BUYABLE_RECOMMENDATIONS_LIMIT:
            break

    return render_template(
        'admin/stocks_auto_trader.html', settings=settings, trades=trades,
        open_count=open_count, pending_count=pending_count, closed_count=len(closed_trades),
        total_pnl=total_pnl, win_count=win_count, stop_loss_alert_email=STOP_LOSS_ALERT_EMAIL,
        deployed_capital=deployed_capital, available_funds=available_funds,
        buyable_recommendations=buyable_recommendations,
    )


@stocks_bp.route('/stocks/auto-trader/settings', methods=['POST'])
@stocks_role_required('super_admin')
def stocks_auto_trader_settings():
    db = get_db()
    enabled = request.form.get('enabled') == 'on'
    mode = MODE_LIVE if (request.form.get('mode') == 'live' and request.form.get('confirm_live') == 'on') else MODE_DRY_RUN
    try:
        budget = float(request.form.get('budget_per_trade') or DEFAULT_AUTO_TRADE_BUDGET)
    except ValueError:
        budget = DEFAULT_AUTO_TRADE_BUDGET
    try:
        total_capital = float(request.form.get('total_capital') or DEFAULT_AUTO_TRADE_TOTAL_CAPITAL)
    except ValueError:
        total_capital = DEFAULT_AUTO_TRADE_TOTAL_CAPITAL
    set_auto_trade_settings(db, enabled, budget, total_capital, mode=mode)
    mode_label = 'LIVE -- real orders' if mode == MODE_LIVE else 'dry-run (simulated)'
    flash(
        f'Auto-trading {"enabled" if enabled else "disabled"}, mode: {mode_label}, '
        f'budget Rs {budget:,.0f} per pick, Rs {total_capital:,.0f} total capital.',
        'info'
    )
    return redirect(url_for('stocks.stocks_auto_trader'))


@stocks_bp.route('/stocks/auto-trader/reconcile', methods=['POST'])
def stocks_auto_trader_reconcile():
    """Daily cron-triggered: auto-closes any open trade that's hit its
    target, and emails STOP_LOSS_ALERT_EMAIL for any that have hit their
    stop-loss instead (never auto-sold, in either mode -- see
    utils.auto_trader's module docstring). Also picks up any live order
    that hadn't filled the moment it was placed (reconcile_pending_buys/
    reconcile_pending_sells). Same dual auth as every other Stocks cron
    route; should run after price_sync each day."""
    is_cron = has_valid_cron_secret(request.headers, STOCKS_FUNDAMENTALS_CRON_SECRET)
    if not is_cron and not session.get('stocks_admin_id'):
        return redirect(url_for('stocks.stocks_admin_login'))

    db = get_db()

    def _job(job_db):
        try:
            settings = get_auto_trade_settings(job_db)
            kite_client = _kite_client_for_auto_trade(job_db, settings)

            pending_buys_summary = {'checked': 0, 'filled': 0, 'failed': 0}
            pending_sells_summary = {'checked': 0, 'closed': 0}
            if kite_client is not None:
                pending_buys_summary = reconcile_pending_buys(job_db, kite_client)
                pending_sells_summary = reconcile_pending_sells(job_db, kite_client)

            summary = reconcile_open_trades(job_db, kite_client=kite_client)
            for trade in summary.get('stop_loss_pending', []):
                pnl_amount, pnl_pct = compute_auto_trade_pnl(
                    trade['buy_price'], trade['stop_loss_triggered_price'], trade['quantity']
                )
                try:
                    send_stop_loss_review_email(STOP_LOSS_ALERT_EMAIL, trade, pnl_amount, pnl_pct)
                except Exception as e:
                    current_app.logger.warning(f'Stop-loss review email failed for {trade.get("symbol")}: {e}')
            for trade in summary.get('target_hit', []):
                try:
                    send_target_hit_email(STOP_LOSS_ALERT_EMAIL, trade, trade['pnl_amount'], trade['pnl_pct'])
                except Exception as e:
                    current_app.logger.warning(f'Target-hit email failed for {trade.get("symbol")}: {e}')
        except Exception as e:
            current_app.logger.error(f'Auto-trade reconciliation failed: {e}')
            alert_job_error(job_db, 'auto_trade_reconcile', str(e))
            raise
        record_job_success(job_db, 'auto_trade_reconcile')
        return {
            'checked': summary['checked'], 'target_hit': len(summary['target_hit']),
            'stop_loss_pending': len(summary['stop_loss_pending']),
            'pending_buys': pending_buys_summary, 'pending_sells': pending_sells_summary,
        }

    return _dispatch_stocks_job(db, is_cron, 'auto_trade_reconcile', _job)


@stocks_bp.route('/stocks/auto-trader/<int:trade_id>/proceed', methods=['POST'])
@stocks_role_required('super_admin')
def stocks_auto_trader_proceed(trade_id):
    """Manually confirms a pending stop-loss -- dry_run books the loss at
    the price that triggered it; live places a real sell order right now
    (see confirm_stop_loss_sell)."""
    db = get_db()
    settings = get_auto_trade_settings(db)
    try:
        kite_client = _kite_client_for_auto_trade(db, settings)
        ok = confirm_stop_loss_sell(db, trade_id, kite_client=kite_client)
        flash('Stop-loss confirmed -- position closed.' if ok else 'That trade is not awaiting a stop-loss decision.', 'info')
    except KiteClientError as e:
        flash(f'Could not place the sell order: {e}', 'error')
    return redirect(url_for('stocks.stocks_auto_trader'))


@stocks_bp.route('/stocks/auto-trader/<int:trade_id>/cancel', methods=['POST'])
@stocks_role_required('super_admin')
def stocks_auto_trader_cancel(trade_id):
    """Manually cancels a pending stop-loss -- keeps holding, puts the
    trade back to 'open' (see cancel_stop_loss_sell). Same in both modes
    -- no order was ever placed to cancel."""
    ok = cancel_stop_loss_sell(get_db(), trade_id)
    flash('Kept the position open.' if ok else 'That trade is not awaiting a stop-loss decision.', 'info')
    return redirect(url_for('stocks.stocks_auto_trader'))


@stocks_bp.route('/stocks/auto-trader/<int:trade_id>/sell-now', methods=['POST'])
@stocks_role_required('super_admin')
def stocks_auto_trader_sell_now(trade_id):
    """Discretionary exit on an 'open' position, independent of whether
    target/stop-loss has actually been reached (see manual_close_trade) --
    the "Sell now" button on the auto-trader dashboard next to any open
    row."""
    db = get_db()
    settings = get_auto_trade_settings(db)
    try:
        kite_client = _kite_client_for_auto_trade(db, settings)
        ok = manual_close_trade(db, trade_id, kite_client=kite_client)
        flash('Sold -- position closed.' if ok else 'That trade is not currently open.', 'info')
    except KiteClientError as e:
        flash(f'Could not place the sell order: {e}', 'error')
    return redirect(url_for('stocks.stocks_auto_trader'))


@stocks_bp.route('/stocks/suggestions/<int:suggestion_id>/buy', methods=['GET', 'POST'])
@stocks_role_required('super_admin')
def stocks_suggestion_buy(suggestion_id):
    """Discretionary buy on a specific already-sent recommendation -- the
    "Buy" link on the recommendation tracker. GET shows a confirmation
    page (company/buy/target/stop-loss read-only, inherited straight from
    the suggestion; amount editable, defaulting to the configured
    budget_per_trade). POST places the trade at whatever amount was
    submitted, using the auto-trader's current global mode (dry_run/live)
    -- see open_manual_trade, which isn't gated by settings['enabled'] the
    way the automatic path is."""
    db = get_db()
    suggestion = get_suggestion_by_id(db, suggestion_id)
    if not suggestion:
        flash('That recommendation no longer exists.', 'error')
        return redirect(url_for('stocks.stocks_recommendations_tracker'))

    settings = get_auto_trade_settings(db)

    if request.method == 'POST':
        try:
            budget_amount = float(request.form.get('budget_amount') or settings['budget_per_trade'])
        except ValueError:
            budget_amount = settings['budget_per_trade']
        try:
            kite_client = _kite_client_for_auto_trade(db, settings)
            quantity = open_manual_trade(db, suggestion, budget_amount, settings['mode'], kite_client=kite_client)
            if quantity:
                flash(f'Bought {quantity} shares of {suggestion["symbol"]} for Rs {budget_amount:,.0f}.', 'info')
            else:
                flash(
                    f'Could not buy {suggestion["symbol"]} -- a trade may already exist for this '
                    f'recommendation, the amount may be too small, insufficient capital available, or '
                    f'(live mode) the symbol has no Kite match.', 'error'
                )
        except KiteClientError as e:
            flash(f'Could not place the buy order: {e}', 'error')
        return redirect(url_for('stocks.stocks_auto_trader'))

    return render_template(
        'admin/stocks_suggestion_buy.html', suggestion=suggestion, settings=settings,
        default_budget=settings['budget_per_trade'],
    )


@stocks_bp.route('/stocks', methods=['GET'])
def stocks_landing():
    """Public marketing/info page for Nari Nakhre Stocks -- no login
    required, unlike every other /stocks/* route. Purely informational
    (what the product is, how the NNS Score works, pricing); "Get Started"
    leads to /stocks/signup (self-serve Razorpay subscription), "Login"
    leads to /stocks/login for existing accounts."""
    return render_template('admin/stocks_landing.html')


def _render_stocks_checkout(admin_id, email, name, plan='standard', referral_plan=False):
    """Creates a fresh Razorpay subscription for admin_id and renders the
    Checkout page for it -- shared by /stocks/signup (password path) and
    /stocks/auth/google/callback (Google path), and also re-run any time a
    lapsed (halted/cancelled-and-expired) paid account needs to renew.
    Always creates a NEW Razorpay subscription rather than trying to reuse
    a previous never-completed one -- simpler and safer than working out
    whether an old subscription_id is still in a usable state, at the cost
    of leaving an occasional harmless unauthorized subscription sitting in
    the Razorpay dashboard for an abandoned signup attempt (those never
    charge anything, since Razorpay only bills after Checkout actually
    authorizes one).

    plan ('standard' or 'starters', see STOCKS_AUTH_ALTER_SQL's stocks_plan
    column) picks which of the two base Plan objects this checks out
    against. referral_plan=True (see utils/stocks_referrals.py) overrides
    that and checks out against RAZORPAY_STOCKS_REFERRAL_PLAN_ID (Rs 199)
    instead -- only ever set True for a brand-new STANDARD signup that
    supplied a valid referral code (there's no discounted Starters plan;
    see the call sites' plan=='standard' guard), never for a renewal. The
    account's own referred_by_id (already stamped at signup time) is what
    /stocks/subscribe/verify later checks to know whether to schedule the
    swap back to the regular Standard plan after this first cycle -- this
    function itself doesn't need to remember which plan it used beyond the
    one API call.

    Returns a Flask response (either the checkout page, or a redirect back
    to signup with a flashed error if Razorpay itself isn't reachable/
    configured)."""
    if referral_plan:
        plan_id, price_display = RAZORPAY_STOCKS_REFERRAL_PLAN_ID, STOCKS_REFERRAL_PRICE_DISPLAY
        missing_var = 'RAZORPAY_STOCKS_REFERRAL_PLAN_ID'
    elif plan == 'starters':
        plan_id, price_display = RAZORPAY_STOCKS_STARTERS_PLAN_ID, STOCKS_STARTERS_PRICE_DISPLAY
        missing_var = 'RAZORPAY_STOCKS_STARTERS_PLAN_ID'
    else:
        plan_id, price_display = RAZORPAY_STOCKS_PLAN_ID, STOCKS_SUBSCRIPTION_PRICE_DISPLAY
        missing_var = 'RAZORPAY_STOCKS_PLAN_ID'
    if not plan_id:
        current_app.logger.error(f'{missing_var} is not set -- cannot start a Stocks subscription checkout.')
        flash('Sign-ups are temporarily unavailable. Please try again shortly.', 'error')
        return redirect(url_for('stocks.stocks_signup'))

    try:
        subscription = razorpay_client.subscription.create({
            'plan_id': plan_id,
            'total_count': SUBSCRIPTION_TOTAL_COUNT_MONTHS,
            'customer_notify': 1,
            'notes': {'stocks_admin_id': str(admin_id)},
        })
    except Exception as e:
        current_app.logger.error(f'Razorpay subscription.create failed for admin_id={admin_id}: {e}')
        flash('Could not start checkout right now. Please try again shortly.', 'error')
        return redirect(url_for('stocks.stocks_signup'))

    db = get_db()
    attach_razorpay_subscription(db, admin_id, None, subscription['id'])
    session['stocks_pending_signup_id'] = admin_id
    session.modified = True

    return render_template(
        'admin/stocks_checkout.html',
        razorpay_key_id=RAZORPAY_KEY_ID,
        subscription_id=subscription['id'],
        prefill_name=name or '',
        prefill_email=email,
        price_display=price_display,
    )


def _finish_stocks_signup(row, email, name, plan):
    """Routes a brand-new signup to either an immediate trial login
    (Standard, trial just granted by create_pending_subscriber/
    create_pending_google_subscriber) or Razorpay checkout (Starters, or
    any row that wasn't freshly created as 'trialing' -- e.g. a resubmitted
    signup for an account that already used its trial) -- shared by
    /stocks/signup, the Google callback's new-subscriber branch, and
    /stocks/plans/continue's 'new_google' mode, all three of which need
    this exact same fork."""
    if row.get('subscription_status') == 'trialing':
        session['stocks_admin_id'] = row['id']
        session['stocks_admin_username'] = row['username']
        session['stocks_admin_role'] = 'viewer'
        session['stocks_can_view_watchlist'] = bool(row.get('can_view_watchlist'))
        session['stocks_must_change_password'] = bool(row.get('must_change_password'))
        session['stocks_plan'] = plan
        session.modified = True
        trial_ends_at = row.get('trial_ends_at')
        if isinstance(trial_ends_at, str):
            trial_ends_at = datetime.fromisoformat(trial_ends_at.replace('Z', '+00:00'))
        trial_end_display = trial_ends_at.strftime('%d %b %Y') if trial_ends_at else 'in 7 days'
        flash(f'Your 7-day free trial has started -- full access through {trial_end_display}, no card required.', 'info')
        return redirect(url_for('stocks.stocks_my_suggestions'))
    return _render_stocks_checkout(
        row['id'], email, name, plan=plan,
        referral_plan=bool(row.get('referred_by_id')) and plan == 'standard',
    )


@stocks_bp.route('/stocks/signup', methods=['GET', 'POST'])
def stocks_signup():
    """Self-serve paid signup -- collects name/email/password, then hands
    off to _render_stocks_checkout for the actual Razorpay Checkout step.
    The account row itself is created up front (subscription_status=
    'pending', is_active=0) so /stocks/subscribe/verify has something to
    activate once payment succeeds; see utils/stocks_subscription.py.

    Same bot-defense stack as the storefront's /contact form (honeypot +
    timing trap + IP rate limit + reCAPTCHA) -- a spam submission here
    would otherwise create a real (if never-authorized) Razorpay
    subscription object, not just a wasted DB row.

    ?ref=<code> (see utils/stocks_referrals.py) prefills the visible
    referral-code field -- also editable by hand, since a code shared
    verbally rather than via the link has nowhere else to go. A valid,
    non-self code checks the new account out on the discounted referral
    plan (see _render_stocks_checkout's referral_plan param) and stamps
    referred_by_id once at creation time -- but only for a Standard
    signup; the referral discount doesn't exist for Starters (see the
    plan selector below), a Starters signup with a code still stamps
    referred_by_id (so the referrer earns credit) but pays full price.

    ?plan=starters (or the form's own plan radio) selects the Rs 99/mo
    Starters tier instead of the default Standard Rs 299/mo -- see
    STOCKS_AUTH_ALTER_SQL's stocks_plan column and _render_stocks_checkout's
    plan param. Stamped once at creation time, same as referred_by_id;
    there's no self-serve way to change it after (a super_admin can, from
    /stocks/users -- see set_viewer_plan)."""
    if request.method == 'GET':
        return render_template(
            'admin/stocks_signup.html', recaptcha_site_key=STOCKS_RECAPTCHA_SITE_KEY, form_rendered_at=time.time(),
            referral_code_prefill=(request.args.get('ref') or '').strip(),
            plan_prefill='starters' if request.args.get('plan') == 'starters' else 'standard',
        )

    referral_code_prefill = (request.form.get('referral_code') or '').strip()
    plan = 'starters' if request.form.get('plan') == 'starters' else 'standard'

    if (request.form.get('system_verification_token') or '').strip():
        current_app.logger.warning(f'Bot caught on stocks signup (honeypot): {request.form.get("email")}')
        return redirect(url_for('stocks.stocks_signup'))
    if contact_form_is_bot(request.form.get('form_rendered_at')):
        current_app.logger.warning(f'Bot caught on stocks signup (timing): {request.form.get("email")}')
        # A genuine user CAN trip this (browser autofill filling every
        # field near-instantly, then submitting fast) -- unlike the
        # honeypot check above (which a real person can't trigger at all),
        # this one needs a visible way back in rather than a silent bounce.
        flash('Please try submitting the form again.', 'error')
        return redirect(url_for('stocks.stocks_signup'))
    client_ip = request.remote_addr or 'unknown'
    if contact_ip_is_rate_limited(client_ip):
        flash('Please wait a moment before trying again.', 'error')
        return render_template('admin/stocks_signup.html', recaptcha_site_key=STOCKS_RECAPTCHA_SITE_KEY, form_rendered_at=time.time(), referral_code_prefill=referral_code_prefill, plan_prefill=plan), 429
    if not verify_recaptcha(request.form.get('recaptcha_token'), remote_ip=client_ip, expected_action='stocks_signup', secret_key=STOCKS_RECAPTCHA_SECRET_KEY):
        current_app.logger.warning(f'Bot caught on stocks signup (recaptcha): {request.form.get("email")}')
        flash('Please try again.', 'error')
        return render_template('admin/stocks_signup.html', recaptcha_site_key=STOCKS_RECAPTCHA_SITE_KEY, form_rendered_at=time.time(), referral_code_prefill=referral_code_prefill, plan_prefill=plan), 401

    name = (request.form.get('name') or '').strip()
    email = (request.form.get('email') or '').strip().lower()
    password = request.form.get('password') or ''
    confirm_password = request.form.get('confirm_password') or ''

    if not EMAIL_RE.match(email):
        flash('Please enter a valid email address.', 'error')
        return render_template('admin/stocks_signup.html', recaptcha_site_key=STOCKS_RECAPTCHA_SITE_KEY, form_rendered_at=time.time(), referral_code_prefill=referral_code_prefill, plan_prefill=plan), 400
    if password != confirm_password:
        flash('Passwords do not match.', 'error')
        return render_template('admin/stocks_signup.html', recaptcha_site_key=STOCKS_RECAPTCHA_SITE_KEY, form_rendered_at=time.time(), referral_code_prefill=referral_code_prefill, plan_prefill=plan), 400

    db = get_db()
    referrer = find_referrer_by_code(db, referral_code_prefill)
    if referrer and referrer['username'] == email:
        referrer = None  # self-referral -- silently ignored, not an error worth blocking signup over

    row, error = create_pending_subscriber(
        db, email, name, password, referred_by_id=referrer['id'] if referrer else None, stocks_plan=plan,
    )
    if error and error != 'existing':
        flash(error, 'error')
        return render_template('admin/stocks_signup.html', recaptcha_site_key=STOCKS_RECAPTCHA_SITE_KEY, form_rendered_at=time.time(), referral_code_prefill=referral_code_prefill, plan_prefill=plan), 400

    if error == 'existing':
        if has_stocks_access(
            row.get('is_pro'), row.get('subscription_status'), row.get('subscription_current_period_end'),
            trial_ends_at=row.get('trial_ends_at'),
        ):
            flash('You already have an account -- please log in.', 'info')
            return redirect(url_for('stocks.stocks_admin_login'))
        # Pending (never completed checkout), an expired trial, or lapsed
        # (halted/expired cancelled) -- send them back through checkout
        # rather than refusing the signup outright. An existing row's
        # referred_by_id AND stocks_plan (if any) were already stamped the
        # first time it was created -- this resubmission doesn't
        # retroactively change either one, and deliberately does NOT grant
        # a second trial (_finish_stocks_signup only starts a trial login
        # for a row this same call just freshly created as 'trialing' --
        # see create_pending_subscriber's docstring).

    # Based on the ACCOUNT's own stored referred_by_id/stocks_plan, not
    # just whatever was resubmitted in this exact request -- covers the
    # retry path too (an abandoned-checkout account coming back through
    # signup again still gets the plan and discount it originally
    # qualified for, as long as it's still on its first, never-completed
    # payment attempt).
    account_plan = row.get('stocks_plan', 'standard')
    return _finish_stocks_signup(row, email, name, account_plan)


@stocks_bp.route('/stocks/subscribe/verify', methods=['POST'])
def stocks_subscribe_verify():
    """Called by stocks_checkout.html's Razorpay Checkout success handler
    (fetch, not a form submit) right after the FIRST payment on a new
    subscription succeeds. This is only ever the fast path for that first
    payment -- every later renewal happens directly between Razorpay and
    the customer's bank/UPI app with no browser involved at all, so it's
    /stocks/razorpay/webhook (not this route) that's the source of truth
    for renewals. session['stocks_pending_signup_id'] (set by
    _render_stocks_checkout) is what ties this callback back to the right
    account -- the request body's razorpay_subscription_id is only trusted
    once it's confirmed to match what that account itself has on file."""
    admin_id = session.get('stocks_pending_signup_id')
    if not admin_id:
        return jsonify({'status': 'error', 'message': 'Signup session expired, please sign up again.'}), 400

    payload = request.get_json(silent=True) or {}
    razorpay_payment_id = (payload.get('razorpay_payment_id') or '').strip()
    razorpay_subscription_id = (payload.get('razorpay_subscription_id') or '').strip()
    razorpay_signature = (payload.get('razorpay_signature') or '').strip()
    if not razorpay_payment_id or not razorpay_subscription_id or not razorpay_signature:
        return jsonify({'status': 'error', 'message': 'Incomplete payment confirmation.'}), 400

    if not verify_subscription_payment_signature(razorpay_payment_id, razorpay_subscription_id, razorpay_signature, RAZORPAY_KEY_SECRET):
        current_app.logger.warning(f'Stocks subscription payment signature verification failed for admin_id={admin_id}')
        return jsonify({'status': 'error', 'message': 'Payment verification failed.'}), 400

    db = get_db()
    row = db.execute(
        'SELECT id, username, name, can_view_watchlist, must_change_password, razorpay_subscription_id, '
        'referred_by_id, stocks_plan '
        'FROM stocks_admin_users WHERE id=?',
        (admin_id,)
    ).fetchone()
    if not row or row.get('razorpay_subscription_id') != razorpay_subscription_id:
        current_app.logger.warning(f'Stocks subscription id mismatch for admin_id={admin_id}')
        return jsonify({'status': 'error', 'message': 'Payment verification failed.'}), 400

    try:
        subscription = razorpay_client.subscription.fetch(razorpay_subscription_id)
        current_end_ts = subscription.get('current_end')
    except Exception as e:
        current_app.logger.warning(f'Razorpay subscription.fetch failed after verified payment (admin_id={admin_id}): {e}')
        current_end_ts = None
    if current_end_ts:
        current_period_end = datetime.fromtimestamp(current_end_ts, tz=timezone.utc)
    else:
        # Never block activation just because the follow-up fetch hiccuped
        # -- the payment signature itself is already verified. The webhook
        # will correct this to the real date on the very next charge.
        current_period_end = datetime.now(timezone.utc) + timedelta(days=30)

    activate_subscription(db, admin_id, current_period_end)
    session.pop('stocks_pending_signup_id', None)

    if row.get('referred_by_id') and row.get('stocks_plan', 'standard') == 'standard' and RAZORPAY_STOCKS_PLAN_ID:
        # This account's first cycle just billed at the discounted
        # referral rate (Rs 199 -- see _render_stocks_checkout's
        # referral_plan param) -- schedule the swap back to the regular
        # Rs 299 plan for cycle 2 onward. schedule_change_at='cycle_end'
        # (not 'now') is what keeps cycle 1's already-completed charge at
        # Rs 199 untouched while queuing the new plan for the next billing
        # date -- Razorpay handles that automatically from here with no
        # further action needed. Never blocks activation if this fails
        # (logged for manual follow-up) -- the customer's own access
        # doesn't depend on it succeeding immediately.
        # The stocks_plan=='standard' guard matters now that Starters
        # exists (see STOCKS_AUTH_ALTER_SQL) -- a referred Starters signup
        # still stamps referred_by_id (so the referrer earns credit) but
        # checks out directly against RAZORPAY_STOCKS_STARTERS_PLAN_ID at
        # full price, never the discounted referral plan, so it must never
        # be swapped onto the Standard plan here.
        try:
            razorpay_client.subscription.edit(
                razorpay_subscription_id, {'plan_id': RAZORPAY_STOCKS_PLAN_ID, 'schedule_change_at': 'cycle_end'}
            )
        except Exception as e:
            current_app.logger.error(f'Failed to schedule referral-to-regular plan swap for admin_id={admin_id}: {e}')

    session['stocks_admin_id'] = row['id']
    session['stocks_admin_username'] = row['username']
    session['stocks_admin_role'] = 'viewer'
    session['stocks_can_view_watchlist'] = bool(row.get('can_view_watchlist'))
    session['stocks_must_change_password'] = bool(row.get('must_change_password'))
    session['stocks_plan'] = row.get('stocks_plan', 'standard')
    session.modified = True

    try:
        period_end_label = current_period_end.strftime('%d %b %Y')
        subscriber_plan = row.get('stocks_plan', 'standard')
        if subscriber_plan == 'starters':
            # The most recent pick(s) still within the rotation window --
            # not "today's", since Starters only generates on Mondays; a
            # signup on any other day of the week would otherwise always
            # see an empty welcome email even with a perfectly current pick.
            window_start = (date.today() - timedelta(days=STARTERS_REPEAT_WINDOW_DAYS)).isoformat()
            welcome_suggestions = get_starters_suggestions(db, start_date=window_start)
        else:
            today_iso = date.today().isoformat()
            welcome_suggestions = get_suggestions(db, start_date=today_iso, end_date=today_iso)
        send_subscription_welcome_email(
            row['username'], row.get('name'), period_end_label, suggestions=welcome_suggestions,
            db=db, admin_id=admin_id, plan=subscriber_plan,
        )
    except Exception as e:
        current_app.logger.warning(f'Subscription welcome email failed for {row["username"]}: {e}')

    try:
        send_admin_new_subscriber_email(row['username'], row.get('name'))
    except Exception as e:
        current_app.logger.warning(f'Admin new-subscriber notification failed for {row["username"]}: {e}')

    redirect_url = url_for('stocks.stocks_change_password') if session['stocks_must_change_password'] else url_for('stocks.stocks_my_suggestions')
    return jsonify({'status': 'ok', 'redirect': redirect_url})


@stocks_bp.route('/stocks/auth/google/login', methods=['GET'])
def stocks_google_login():
    """Mirrors the storefront's /auth/google/login (see google_login above)
    against stocks_admin_users instead of the storefront's users table --
    same GoogleAuthProvider, same Authlib handshake, just a different
    redirect_uri/callback and a different table on the other end.

    ?ref=<code> (see utils/stocks_referrals.py), ?next=<path> (see
    stocks_login_required/stocks_admin_login), and ?plan= (see
    STOCKS_AUTH_ALTER_SQL's stocks_plan column and the signup page's plan
    selector) are all stashed in the session rather than passed through the
    OAuth round-trip itself (Google's redirect back to
    /stocks/auth/google/callback carries no form fields or query params of
    ours) -- the callback reads them back from here."""
    referral_code = (request.args.get('ref') or '').strip()
    if referral_code:
        session['stocks_pending_referral_code'] = referral_code
    next_url = safe_stocks_next_url(request.args.get('next', ''))
    if next_url:
        session['stocks_pending_next_url'] = next_url
    plan_param = request.args.get('plan')
    if plan_param in ('standard', 'starters'):
        session['stocks_pending_plan'] = plan_param
    session.modified = True
    provider = auth_providers.get_auth_provider('google')
    redirect_uri = url_for('stocks.stocks_google_callback', _external=True)
    return redirect(provider.get_auth_url(redirect_uri))


@stocks_bp.route('/stocks/auth/google/callback', methods=['GET'])
def stocks_google_callback():
    """Handles both sign-in (an existing account, matched by google_sub or
    by email) and sign-up (no matching account at all) in one callback --
    Google doesn't distinguish the two ahead of time, so neither does this
    route. A brand-new or lapsed account still has to go through
    _render_stocks_checkout exactly like the password signup path; Google
    only replaces the password step, not the payment step (see is_pro for
    the one case where payment is skipped entirely)."""
    provider = auth_providers.get_auth_provider('google')
    try:
        token = provider.exchange_code()
        userinfo = provider.get_user_info(token)
    except Exception as e:
        current_app.logger.warning(f'Stocks Google OAuth callback failed: {e}')
        flash('Sign-in was cancelled or failed. Please try again.', 'error')
        return redirect(url_for('stocks.stocks_landing'))

    google_sub = userinfo.get('sub')
    name = userinfo.get('name') or ''
    email = (userinfo.get('email') or '').strip().lower()
    if not google_sub or not email:
        current_app.logger.warning('Stocks Google callback missing sub/email claim; aborting.')
        flash('Sign-in was cancelled or failed. Please try again.', 'error')
        return redirect(url_for('stocks.stocks_landing'))

    referral_code = session.pop('stocks_pending_referral_code', None)
    next_url = session.pop('stocks_pending_next_url', None)
    pending_plan = session.pop('stocks_pending_plan', None)
    session.modified = True

    db = get_db()
    row = find_stocks_account_by_google_sub(db, google_sub)
    if not row:
        existing = find_stocks_account_by_username(db, email)
        if existing:
            link_google_sub(db, existing['id'], google_sub)
            row = existing
        elif pending_plan is None:
            # Reached here without ever going through /stocks/signup's plan
            # radio buttons or a landing-page pricing card's explicit
            # ?plan= link -- most commonly, hitting "Sign in with Google" on
            # the LOGIN page directly. Rather than silently defaulting to
            # Standard, stash what Google gave us and send them to a plan
            # picker first; stocks_plans_continue re-enters this same
            # create-account-then-trial/checkout path once they've chosen.
            session['stocks_plans_context'] = {
                'mode': 'new_google', 'google_sub': google_sub, 'email': email, 'name': name,
                'referral_code': referral_code,
            }
            session.modified = True
            return redirect(url_for('stocks.stocks_plans'))
        else:
            referrer = find_referrer_by_code(db, referral_code)
            if referrer and referrer['username'] == email:
                referrer = None  # self-referral -- silently ignored
            row = create_pending_google_subscriber(
                db, email, name, google_sub, referred_by_id=referrer['id'] if referrer else None,
                stocks_plan=pending_plan,
            )
            return _finish_stocks_signup(row, email, name, pending_plan)

    if row.get('is_active') and has_stocks_access(
        row.get('is_pro'), row.get('subscription_status'), row.get('subscription_current_period_end'),
        trial_ends_at=row.get('trial_ends_at'),
    ):
        session['stocks_admin_id'] = row['id']
        session['stocks_admin_username'] = row['username']
        session['stocks_admin_role'] = 'viewer'
        session['stocks_can_view_watchlist'] = bool(row.get('can_view_watchlist'))
        session['stocks_must_change_password'] = bool(row.get('must_change_password'))
        session['stocks_plan'] = row.get('stocks_plan', 'standard')
        session.modified = True
        if session['stocks_must_change_password']:
            return redirect(url_for('stocks.stocks_change_password'))
        if next_url:
            return redirect(next_url)
        return redirect(url_for('stocks.stocks_my_suggestions'))

    if row.get('subscription_status') == 'trialing':
        # An existing account whose free trial has run out, signing in via
        # Google -- same "go choose a plan for real" treatment as a
        # password login getting reason='trial_expired' below, not a
        # straight-to-checkout bounce (see stocks_admin_login's docstring
        # for why this is scoped to 'trialing' specifically and not every
        # kind of lapsed account).
        session['stocks_plans_context'] = {'mode': 'resubscribe', 'admin_id': row['id'], 'email': email, 'name': name}
        session.modified = True
        flash('Your 7-day free trial has ended -- choose a plan to keep your access.', 'info')
        return redirect(url_for('stocks.stocks_plans'))

    # Based on the ACCOUNT's own stored referred_by_id/stocks_plan (see the
    # matching comment in /stocks/signup) so a retried checkout still gets
    # the same plan and discount it originally qualified for.
    return _render_stocks_checkout(
        row['id'], email, name, plan=row.get('stocks_plan', 'standard'),
        referral_plan=bool(row.get('referred_by_id')) and row.get('stocks_plan', 'standard') == 'standard',
    )


@stocks_bp.route('/stocks/plans', methods=['GET'])
def stocks_plans():
    """Pricing-card page -- reached either as a brand-new Google sign-in
    that arrived with no plan chosen yet, or as an EXISTING account whose
    free trial just ran out needing to actually subscribe now (see
    stocks_plans_context's 'mode' field, set by stocks_google_callback and
    stocks_admin_login). Reads nothing itself -- stocks_plans_continue does
    the actual account creation/checkout -- this just renders the two plan
    cards; if the context is gone (direct hit, expired session, back-button
    after finishing), there is nothing to continue, so send them to the
    normal signup page instead."""
    context = session.get('stocks_plans_context')
    if not context:
        return redirect(url_for('stocks.stocks_signup'))
    return render_template('admin/stocks_plans.html', plans_mode=context.get('mode', 'new_google'))


@stocks_bp.route('/stocks/plans/continue', methods=['GET'])
def stocks_plans_continue():
    """Finishes whichever /stocks/plans visit this is, once a plan card has
    been picked -- see stocks_plans_context's 'mode':
      - 'new_google': mirrors the create_pending_google_subscriber +
        referral + trial/checkout sequence in stocks_google_callback's own
        new-subscriber branch (via _finish_stocks_signup), just resumed
        from the stashed session state instead of a fresh OAuth round-trip.
      - 'resubscribe': an EXISTING account whose free trial already ran
        out, choosing a plan to actually pay for now -- always goes to
        real Razorpay checkout, never grants a second trial regardless of
        which plan is picked. Lets them switch Standard<->Starters from
        what they originally trialed, since nothing's been paid for yet."""
    plan = request.args.get('plan')
    if plan not in ('standard', 'starters'):
        flash('Please choose a plan to continue.', 'error')
        return redirect(url_for('stocks.stocks_plans'))

    context = session.pop('stocks_plans_context', None)
    session.modified = True
    if not context:
        return redirect(url_for('stocks.stocks_signup'))

    db = get_db()
    if context.get('mode') == 'resubscribe':
        admin_id = context['admin_id']
        set_viewer_plan(db, admin_id, plan)
        return _render_stocks_checkout(admin_id, context['email'], context['name'], plan=plan, referral_plan=False)

    referrer = find_referrer_by_code(db, context.get('referral_code'))
    if referrer and referrer['username'] == context['email']:
        referrer = None  # self-referral -- silently ignored
    row = create_pending_google_subscriber(
        db, context['email'], context['name'], context['google_sub'],
        referred_by_id=referrer['id'] if referrer else None, stocks_plan=plan,
    )
    return _finish_stocks_signup(row, context['email'], context['name'], plan)


@stocks_bp.route('/stocks/razorpay/webhook', methods=['POST'])
def stocks_razorpay_webhook():
    """Razorpay calls this directly (no browser, no session) for every
    subscription lifecycle event -- most importantly 'subscription.charged'
    on every renewal, which is the ONLY way this app finds out a recurring
    payment succeeded (see _render_stocks_checkout/stocks_subscribe_verify's
    docstrings for why: Checkout only comes back to the browser for a
    subscription's first payment, never later ones). Must read the raw
    request body for signature verification -- request.get_json() would
    re-parse/re-serialize it, and Razorpay's signature covers the exact
    bytes they sent, not a semantically-equivalent re-encoding of them.

    Always returns 200 once the signature itself checks out, even if
    handling a recognized event then fails internally -- Razorpay retries
    on any non-2xx response, and a handler bug retrying forever helps no
    one; the failure is logged for manual follow-up instead."""
    raw_body = request.get_data()
    signature = request.headers.get('X-Razorpay-Signature', '')
    if not RAZORPAY_WEBHOOK_SECRET or not verify_webhook_signature(raw_body, signature, RAZORPAY_WEBHOOK_SECRET):
        current_app.logger.warning('Razorpay webhook signature verification failed or RAZORPAY_WEBHOOK_SECRET unset')
        return jsonify({'status': 'invalid signature'}), 400

    payload = request.get_json(silent=True) or {}
    event = payload.get('event', '')
    db = get_db()

    try:
        entity = payload.get('payload', {}).get('subscription', {}).get('entity', {})
        subscription_id = entity.get('id')
        if event == 'subscription.charged' and subscription_id and entity.get('current_end'):
            current_period_end = datetime.fromtimestamp(entity['current_end'], tz=timezone.utc)
            record_recurring_charge(db, subscription_id, current_period_end)
        elif event in ('subscription.cancelled', 'subscription.completed') and subscription_id:
            account = find_account_by_razorpay_subscription_id(db, subscription_id)
            mark_subscription_cancelled(db, subscription_id)
            if account:
                try:
                    # Banked referral credits (see utils/stocks_referrals.py's
                    # module docstring for why this happens at cancellation
                    # rather than as a live billing interruption) -- extends
                    # subscription_current_period_end by 30 days per
                    # unredeemed credit, so access actually lasts the extra
                    # free month(s) earned before really ending.
                    apply_referral_credits_on_cancellation(db, account['id'])
                except Exception as e:
                    current_app.logger.error(f'Failed to apply referral credits for admin_id={account["id"]}: {e}')
                try:
                    send_admin_subscription_cancelled_email(account['username'], account.get('name'))
                except Exception as e:
                    current_app.logger.warning(f'Admin cancellation notification failed for {account["username"]}: {e}')
        elif event == 'subscription.halted' and subscription_id:
            mark_subscription_halted(db, subscription_id)
    except Exception as e:
        current_app.logger.error(f'Razorpay webhook handling failed for event={event}: {e}')

    return jsonify({'status': 'ok'}), 200


@stocks_bp.route('/stocks/subscriptions/send-expiry-reminders', methods=['POST'])
def stocks_subscriptions_send_expiry_reminders():
    """Daily cron-triggered: emails anyone whose paid access is about to
    lapse within REMINDER_WINDOW_DAYS -- either an upcoming auto-renewal
    charge (subscription_status='active') or access actually ending
    (subscription_status='cancelled', no renewal coming). Same dual auth
    as every other Stocks cron route. Unlike the Pick of the Day email,
    this is NOT gated on is_trading_day() -- billing runs on calendar days,
    not trading days."""
    is_cron = has_valid_cron_secret(request.headers, STOCKS_FUNDAMENTALS_CRON_SECRET)
    if not is_cron and not session.get('stocks_admin_id'):
        return redirect(url_for('stocks.stocks_admin_login'))

    db = get_db()

    def _job(job_db):
        try:
            rows = find_expiring_subscribers(job_db)
            sent = 0
            for row in rows:
                period_end = row['subscription_current_period_end']
                if isinstance(period_end, str):
                    period_end_dt = datetime.fromisoformat(period_end.replace('Z', '+00:00'))
                else:
                    period_end_dt = period_end
                period_end_label = period_end_dt.strftime('%d %b %Y')
                ok, _detail = send_subscription_expiry_reminder_email(
                    row['username'], row.get('name'), period_end_label,
                    is_renewing=(row['subscription_status'] == 'active'),
                )
                if ok:
                    mark_reminder_sent(job_db, row['id'], period_end)
                    sent += 1
            summary = {'checked': len(rows), 'sent': sent}
        except Exception as e:
            current_app.logger.error(f'Subscription expiry reminders failed: {e}')
            alert_job_error(job_db, 'subscription_reminders', str(e))
            raise
        record_job_success(job_db, 'subscription_reminders')
        return summary

    return _dispatch_stocks_job(db, is_cron, 'subscription_reminders', _job)


@stocks_bp.route('/stocks/subscription/notify-trial-ended', methods=['POST'])
def stocks_subscription_notify_trial_ended():
    """Daily cron-triggered: emails anyone whose 7-day Standard-plan free
    trial has run out (see find_expired_trials/activate_trial), pointing
    them at /stocks/login rather than /stocks/plans directly -- a bare
    emailed link can't carry the stocks_plans_context session state
    /stocks/plans needs, but logging in with their already-known
    credentials naturally lands them there via stocks_admin_login's own
    reason='trial_expired' redirect. Same dual auth as every other Stocks
    cron route, and NOT gated on is_trading_day() -- trials run on calendar
    days like billing does, not trading days.

    Only marks a row's email as sent once it actually sends -- same "don't
    silently burn the one-time notification on a failed send" rule as
    /stocks/suggestions/notify-target-hits, so a transient failure retries
    tomorrow instead of that subscriber never hearing their trial ended."""
    is_cron = has_valid_cron_secret(request.headers, STOCKS_FUNDAMENTALS_CRON_SECRET)
    if not is_cron and not session.get('stocks_admin_id'):
        return redirect(url_for('stocks.stocks_admin_login'))

    db = get_db()

    def _job(job_db):
        try:
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
                    current_app.logger.warning(f'Trial-ended email failed for {row["username"]}: {detail}')
            summary = {'expired_trials': len(rows), 'sent': sent, 'failed': failed}
        except Exception as e:
            current_app.logger.error(f'Trial-ended notification job failed: {e}')
            alert_job_error(job_db, 'trial_ended_notify', str(e))
            raise
        record_job_success(job_db, 'trial_ended_notify')
        return summary

    return _dispatch_stocks_job(db, is_cron, 'trial_ended_notify', _job)


@stocks_bp.route('/stocks/users/<int:viewer_id>/toggle-pro', methods=['POST'])
@stocks_role_required('super_admin', 'child_admin')
def stocks_users_toggle_pro(viewer_id):
    """Comps (or un-comps) a viewer's full access independent of any
    Razorpay subscription -- see has_stocks_access/toggle_viewer_pro. Same
    role gate as the rest of /stocks/users."""
    toggle_viewer_pro(get_db(), viewer_id)
    return redirect(url_for('stocks.stocks_users_manage'))


@stocks_bp.route('/stocks/users/<int:viewer_id>/set-plan', methods=['POST'])
@stocks_role_required('super_admin', 'child_admin')
def stocks_users_set_plan(viewer_id):
    """Admin-only Standard <-> Starters plan switch (see
    STOCKS_AUTH_ALTER_SQL's stocks_plan column, set_viewer_plan) -- there's
    no self-serve upgrade/downgrade flow, so this is the only way an
    existing subscriber's plan ever changes after signup. Same role gate
    as the rest of /stocks/users. Doesn't touch Razorpay -- see
    set_viewer_plan's docstring."""
    plan = request.form.get('plan')
    ok = set_viewer_plan(get_db(), viewer_id, plan)
    flash(f'Plan updated to {plan}.' if ok else 'Could not update plan.', 'info' if ok else 'error')
    return redirect(url_for('stocks.stocks_users_manage'))


@stocks_bp.route('/stocks/login', methods=['GET', 'POST'])
def stocks_admin_login():
    """Separate login for Nari Nakhre Stocks -- shared by super_admin and
    child admins (session['stocks_admin_role'] tells them apart). Nothing to
    do with the storefront's /admin/login or session['is_admin'].

    ?next=<path> (set by stocks_login_required when it redirects a logged-
    out visit here -- e.g. a "View full analysis" email link to
    /stocks/universe/<id>) takes over the post-login redirect so that
    visit lands on the page actually requested, not always the generic
    role default. Validated via safe_stocks_next_url (same-site /stocks/...
    paths only) to rule out an open-redirect via a crafted next= value."""
    next_url = safe_stocks_next_url(request.values.get('next', ''))
    if request.method == 'GET':
        return render_template('admin/stocks_login.html', recaptcha_site_key=STOCKS_RECAPTCHA_SITE_KEY, next_url=next_url or '')

    if not verify_recaptcha(request.form.get('recaptcha_token'), remote_ip=request.remote_addr, expected_action='stocks_admin_login', secret_key=STOCKS_RECAPTCHA_SECRET_KEY):
        current_app.logger.warning('Bot caught on stocks admin login (recaptcha)')
        flash('Please try again.', 'error')
        return render_template('admin/stocks_login.html', recaptcha_site_key=STOCKS_RECAPTCHA_SITE_KEY, next_url=next_url or ''), 401

    username = (request.form.get('username') or '').strip()
    password = request.form.get('password') or ''

    db = get_db()
    admin_row, reason = authenticate_stocks_admin(db, username, password)
    if not admin_row:
        if reason == 'trial_expired':
            # Credentials were correct -- this is specifically a Standard
            # trial that's run out (see authenticate_stocks_admin's
            # docstring for why every other lapsed-access case still falls
            # through to the generic message below). Look the row back up
            # for its id/name -- authenticate_stocks_admin only returns
            # those on success.
            expired_row = find_stocks_account_by_username(db, username)
            session['stocks_plans_context'] = {
                'mode': 'resubscribe', 'admin_id': expired_row['id'],
                'email': expired_row['username'], 'name': expired_row.get('name'),
            }
            session.modified = True
            flash('Your 7-day free trial has ended -- choose a plan to keep your access.', 'info')
            return redirect(url_for('stocks.stocks_plans'))
        flash('Invalid username or password.', 'error')
        return render_template('admin/stocks_login.html', recaptcha_site_key=STOCKS_RECAPTCHA_SITE_KEY, next_url=next_url or ''), 401

    session['stocks_admin_id'] = admin_row['id']
    session['stocks_admin_username'] = admin_row['username']
    session['stocks_admin_role'] = admin_row['role']
    # Cached here rather than looked up fresh per request, same as role
    # above -- see stocks_watchlist_access_required. Irrelevant for
    # super_admin/child_admin, who always have watchlist access regardless.
    session['stocks_can_view_watchlist'] = bool(admin_row.get('can_view_watchlist'))
    session['stocks_must_change_password'] = bool(admin_row.get('must_change_password'))
    session['stocks_plan'] = admin_row.get('stocks_plan', 'standard')
    session.modified = True
    # A forced password change wins over every other redirect below -- see
    # stock_auth.py's access decorators, which enforce this on every
    # subsequent request too, not just this one. next_url is skipped in
    # that case too -- there's no page to usefully land on before the
    # password's actually changed.
    if session['stocks_must_change_password']:
        return redirect(url_for('stocks.stocks_change_password'))
    if next_url:
        return redirect(next_url)
    # viewer accounts land on their own minimalistic home/summary page, not
    # the staff dashboard (there was no per-role redirect at all before
    # this -- super_admin/child_admin both just went to
    # stocks_admin_dashboard, which they still do, unchanged).
    if admin_row['role'] == 'viewer':
        return redirect(url_for('stocks.stocks_home'))
    return redirect(url_for('stocks.stocks_admin_dashboard'))


@stocks_bp.route('/stocks/logout', methods=['GET'])
@stocks_login_required
def stocks_admin_logout():
    session.pop('stocks_admin_id', None)
    session.pop('stocks_admin_username', None)
    session.pop('stocks_admin_role', None)
    session.pop('stocks_can_view_watchlist', None)
    session.pop('stocks_must_change_password', None)
    session.modified = True
    return redirect(url_for('stocks.stocks_admin_login'))


@stocks_bp.route('/stocks/unsubscribe', methods=['GET'])
def stocks_unsubscribe():
    """One-click unsubscribe -- the link appended to every customer-facing
    Stocks email by default (see utils/stock_alerting.send_zeptomail_stocks_email
    and utils/stock_auth.build_unsubscribe_url/verify_and_apply_unsubscribe).
    Deliberately no login required -- a recipient clicking this from their
    inbox shouldn't need to remember Stocks credentials just to stop
    receiving mail, and GET-not-POST matches the one-click convention every
    major mail client's own list-unsubscribe support already assumes.
    Purely an email-delivery opt-out -- doesn't touch is_active,
    subscription_status, or website login/access at all."""
    email = request.args.get('email', '')
    token = request.args.get('token', '')
    applied = verify_and_apply_unsubscribe(get_db(), email, token)
    return render_template('admin/stocks_unsubscribe.html', applied=applied)


@stocks_bp.route('/stocks/change-password', methods=['GET', 'POST'])
@stocks_login_required
def stocks_change_password():
    """Any logged-in Stocks account can change their own password here --
    required first for a viewer whose must_change_password flag is still
    set (every access decorator in utils/stock_auth.py redirects here
    until it's cleared -- see _must_change_password_redirect), but usable
    afterward by anyone, any role, who just wants a different password.
    No 'current password' re-entry -- the active session already proves
    that."""
    forced = session.get('stocks_must_change_password', False)
    if request.method == 'POST':
        new_password = request.form.get('new_password') or ''
        confirm_password = request.form.get('confirm_password') or ''
        if new_password != confirm_password:
            flash('Passwords do not match.', 'error')
        else:
            db = get_db()
            ok, error, trial_started = change_own_password(db, session['stocks_admin_id'], new_password)
            if not ok:
                flash(error, 'error')
            else:
                session['stocks_must_change_password'] = False
                session.modified = True
                if trial_started:
                    row = db.execute(
                        'SELECT name, trial_ends_at FROM stocks_admin_users WHERE id=?',
                        (session['stocks_admin_id'],)
                    ).fetchone()
                    trial_ends_at = row.get('trial_ends_at') if row else None
                    if isinstance(trial_ends_at, str):
                        trial_ends_at = datetime.fromisoformat(trial_ends_at.replace('Z', '+00:00'))
                    trial_end_label = trial_ends_at.strftime('%d %b %Y') if trial_ends_at else 'in 7 days'
                    flash(f'Password updated -- your 7-day free trial has started! Full access through {trial_end_label}.')
                    try:
                        send_trial_started_email(session['stocks_admin_username'], row.get('name') if row else None, trial_end_label)
                    except Exception as e:
                        current_app.logger.warning(f'Trial-started email failed for {session["stocks_admin_username"]}: {e}')
                else:
                    flash('Password updated.')
                if session.get('stocks_admin_role') == 'viewer':
                    return redirect(url_for('stocks.stocks_my_suggestions'))
                return redirect(url_for('stocks.stocks_admin_dashboard'))
    return render_template('admin/stocks_change_password.html', forced=forced)


@stocks_bp.route('/stocks/dashboard', methods=['GET'])
@stocks_login_required
def stocks_admin_dashboard():
    db = get_db()
    kite_status = get_kite_session_status(db)

    # Read-only -- fundamentals_rotation has no admin-triggerable button
    # (see /stocks/fundamentals/rotation-sync), so this is purely
    # informational: when the automatic, cron-only 15-day-cadence scrape
    # last actually completed.
    fundamentals_last_synced = get_last_success_at(db, 'fundamentals_rotation')
    fundamentals_last_synced_ist = (
        fundamentals_last_synced.astimezone(IST).strftime('%d %b %Y, %I:%M %p IST')
        if fundamentals_last_synced else None
    )

    return render_template(
        'admin/stocks_dashboard.html',
        username=session.get('stocks_admin_username'),
        role=session.get('stocks_admin_role'),
        kite_status=kite_status,
        fundamentals_last_synced_ist=fundamentals_last_synced_ist,
    )


@stocks_bp.route('/stocks/admins', methods=['GET'])
@stocks_role_required('super_admin')
def stocks_admin_manage():
    db = get_db()
    admins = list_stocks_admin_users(db)
    return render_template('admin/stocks_admins.html', admins=admins)


@stocks_bp.route('/stocks/admins/create', methods=['POST'])
@stocks_role_required('super_admin')
def stocks_admin_create():
    db = get_db()
    username = request.form.get('username')
    password = request.form.get('password')
    _row, error = create_child_admin(db, username, password, session.get('stocks_admin_id'))
    if error:
        flash(error, 'error')
    else:
        flash(f'Child admin "{username.strip()}" created.')
    return redirect(url_for('stocks.stocks_admin_manage'))


@stocks_bp.route('/stocks/admins/<int:admin_id>/toggle', methods=['POST'])
@stocks_role_required('super_admin')
def stocks_admin_toggle(admin_id):
    db = get_db()
    if not toggle_child_admin_active(db, admin_id):
        flash('Could not update that account.', 'error')
    else:
        flash('Account status updated.')
    return redirect(url_for('stocks.stocks_admin_manage'))

