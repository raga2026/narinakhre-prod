"""Orchestrates every Stocks sync/calculation step in one call -- the
"Super Sync" button on the dashboard. Runs steps in dependency order,
continuing through a failed step rather than stopping the whole run, so
one broken step (e.g. an expired Kite session) doesn't block everything
else that doesn't depend on it. The individual buttons on the dashboard
still exist for running just one step on its own -- this is a convenience
wrapper around them, not a replacement."""
from utils.kite_client import KiteClient
from utils.kite_instrument_map import sync_kite_instrument_map
from utils.fundamentals_ingestion import sync_fundamentals, sync_fundamentals_rotation
from utils.stock_universe import refresh_market_cap_filter
from utils.stock_shortlist import run_fundamental_shortlist
from utils.stock_ingestion import sync_daily_data, sync_daily_data_universe
from utils.stock_indicators import run_indicator_calculation, run_indicator_calculation_universe
from utils.job_progress import report as report_progress

# Order matters:
#   1. Kite instrument map first -- both price syncs below use it to
#      resolve instrument_token, so matching runs before anything that
#      benefits from the match.
#   2. Fundamentals (watchlist-scoped, then universe rotation) and the
#      market cap filter, before...
#   3. ...the shortlist refresh, which reads whatever fundamentals data is
#      on hand at that point.
#   4. Watchlist-scoped price sync + indicators.
#   5. Universe-wide price sync + indicators last -- by far the slowest
#      step (paced across ~1,067 symbols), so it doesn't block anything
#      else in this run from happening first.
SUPER_SYNC_STEPS = [
    'kite_instrument_map_sync',
    'fundamentals_sync',
    'fundamentals_rotation',
    'market_cap_filter',
    'shortlist_refresh',
    'price_sync',
    'indicator_calc',
    'price_sync_universe',
    'indicator_calc_universe',
]

# Human-readable form of each SUPER_SYNC_STEPS entry, for the "step N of 9:
# ..." progress label -- see _run below. Keys must stay in sync with
# SUPER_SYNC_STEPS (checked by test_super_sync.py).
STEP_LABELS = {
    'kite_instrument_map_sync': 'Syncing Kite instrument map',
    'fundamentals_sync': 'Fetching fundamentals (watchlist)',
    'fundamentals_rotation': 'Fetching fundamentals (universe rotation)',
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
    built per price-related step since a full run can take many minutes on
    a background thread (see app.py's /stocks/super-sync).

    Every step runs even if an earlier one raised -- a single summary
    covers the whole run: {'steps': [{'step', 'status': 'ok'|'error',
    'summary'|'error'}, ...], 'ok_count', 'error_count'}.

    Reports a coarse "step N of 9: <name>" progress marker (see
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
    _run('fundamentals_sync', lambda: sync_fundamentals(db))
    _run('fundamentals_rotation', lambda: sync_fundamentals_rotation(db))
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
