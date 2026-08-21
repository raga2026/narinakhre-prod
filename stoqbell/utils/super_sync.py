"""Orchestrates every fast Stocks sync/calculation step in one call -- the
"Super Sync" button on the dashboard. Runs steps in dependency order,
continuing through a failed step rather than stopping the whole run, so
one broken step (e.g. an expired Kite session) doesn't block everything
else that doesn't depend on it. The individual buttons on the dashboard's
"Advanced" section still exist for running just one step on its own --
this is a convenience wrapper around them, not a replacement.

Deliberately does NOT include Screener.in fundamentals scraping
(utils.fundamentals_ingestion.sync_fundamentals_rotation) -- that's a slow,
external, rate-sensitive scrape (one HTTP request per company), unlike
every step here, which is either a local DB computation or a Kite API call.
Bundling it in would make every Super Sync click take as long as the
slowest step, and would let an admin action trigger scraping, which the
scraper is deliberately never supposed to be (see the dashboard's
"Fundamentals data" section and ROTATION_BATCH_SIZE's comment in
fundamentals_ingestion.py -- it runs on its own daily cron instead, sized
so the full universe cycles roughly every 15 days)."""
from stoqbell.utils.kite_client import KiteClient
from stoqbell.utils.kite_instrument_map import sync_kite_instrument_map
from stoqbell.utils.stock_universe import refresh_market_cap_filter
from stoqbell.utils.stock_shortlist import run_fundamental_shortlist
from stoqbell.utils.stock_ingestion import sync_daily_data, sync_daily_data_universe
from stoqbell.utils.stock_indicators import run_indicator_calculation, run_indicator_calculation_universe
from stoqbell.utils.job_progress import report as report_progress

# Order matters:
#   1. Kite instrument map first -- both price syncs below use it to
#      resolve instrument_token, so matching runs before anything that
#      benefits from the match.
#   2. The market cap filter, before...
#   3. ...the shortlist refresh, which reads whatever fundamentals data is
#      already on hand (fundamentals themselves are synced separately, on
#      their own schedule -- see the module docstring).
#   4. Watchlist-scoped price sync + indicators.
#   5. Universe-wide price sync + indicators last -- by far the slowest
#      step (paced across ~1,067 symbols), so it doesn't block anything
#      else in this run from happening first.
SUPER_SYNC_STEPS = [
    'kite_instrument_map_sync',
    'market_cap_filter',
    'shortlist_refresh',
    'price_sync',
    'indicator_calc',
    'price_sync_universe',
    'indicator_calc_universe',
]

# Human-readable form of each SUPER_SYNC_STEPS entry, for the "step N of 7:
# ..." progress label -- see _run below. Keys must stay in sync with
# SUPER_SYNC_STEPS (checked by test_super_sync.py).
STEP_LABELS = {
    'kite_instrument_map_sync': 'Syncing Kite instrument map',
    'market_cap_filter': 'Refreshing market cap filter',
    'shortlist_refresh': 'Refreshing shortlist',
    'price_sync': 'Syncing prices (watchlist)',
    'indicator_calc': 'Calculating indicators (watchlist)',
    'price_sync_universe': 'Syncing prices (universe)',
    'indicator_calc_universe': 'Calculating indicators (universe)',
}


def run_super_sync(db, access_token):
    """access_token: a decrypted Kite access token (see
    utils/kite_session.get_kite_access_token) -- a fresh KiteClient is
    built per price-related step since a full run can take several minutes
    on a background thread (see app.py's /stocks/super-sync).

    Every step runs even if an earlier one raised -- a single summary
    covers the whole run: {'steps': [{'step', 'status': 'ok'|'error',
    'summary'|'error'}, ...], 'ok_count', 'error_count'}.

    Reports a coarse "step N of 7: <name>" progress marker (see
    utils/job_progress.py) before each step starts -- a no-op outside a
    background job. Whichever step is actually running may then report its
    own finer-grained item-level progress on top of that (e.g. "340 of
    1067"), keeping this step's label sticky until the next step begins."""
    steps = []
    total_steps = len(SUPER_SYNC_STEPS)

    def _run(name, fn):
        step_index = len(steps) + 1
        report_progress(step_index - 1, total_steps, label=f'Step {step_index} of {total_steps}: {STEP_LABELS[name]}')
        try:
            summary = fn()
            steps.append({'step': name, 'status': 'ok', 'summary': summary})
        except Exception as e:
            steps.append({'step': name, 'status': 'error', 'error': str(e)})

    _run('kite_instrument_map_sync', lambda: sync_kite_instrument_map(db, KiteClient(access_token=access_token)))
    _run('market_cap_filter', lambda: refresh_market_cap_filter(db))
    _run('shortlist_refresh', lambda: run_fundamental_shortlist(db))
    _run('price_sync', lambda: sync_daily_data(db, kite_client=KiteClient(access_token=access_token)))
    _run('indicator_calc', lambda: run_indicator_calculation(db))
    _run('price_sync_universe', lambda: sync_daily_data_universe(db, kite_client=KiteClient(access_token=access_token)))
    _run('indicator_calc_universe', lambda: run_indicator_calculation_universe(db))

    return {
        'steps': steps,
        'ok_count': sum(1 for s in steps if s['status'] == 'ok'),
        'error_count': sum(1 for s in steps if s['status'] == 'error'),
    }
