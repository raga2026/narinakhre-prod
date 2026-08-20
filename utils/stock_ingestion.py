import time
from datetime import date, timedelta

from utils.kite_client import KiteClient
from utils.kite_instrument_map import get_cached_instrument_token
from utils.job_progress import report as report_progress

# How far back (calendar days) to pull when a symbol doesn't have enough
# history yet. 380 calendar days comfortably yields 250+ trading days once
# weekends/holidays are excluded -- Kite only returns actual trading-day
# candles in the requested range regardless of how wide it is, and it's
# still exactly one API call either way, so there's no cost to asking wide.
# Was 30 days (Phase 1's original "ingestion only, nothing downstream needs
# deep history yet" reasoning) -- since raised, because MA_200 and the
# 52-week range both need far more than 30 days of history to ever compute
# at all, and a 30-day window meant they silently stayed None indefinitely.
BACKFILL_DAYS = 380

# Below this many existing rows, a symbol is treated as needing the full
# BACKFILL_DAYS window again, regardless of what its last recorded
# trade_date is -- otherwise a symbol that was already synced under the
# old 30-day BACKFILL_DAYS would stay stuck at ~30 days of history forever
# (from_date = last_date + 1 day only ever adds one day at a time going
# forward). Upserting an overlapping date range is harmless (ON CONFLICT
# DO UPDATE), so re-requesting days already on file costs nothing but the
# one Kite call.
MIN_HISTORY_DAYS_BEFORE_INCREMENTAL_SYNC = 200

# Nari Nakhre Stocks -- Phase 1. Kept in its own function/file, separate from
# the e-commerce schema in app.py's initialize_database_if_needed() -- same
# Supabase project and same admin login as the rest of the site (shared to
# avoid a second Render service/domain), but otherwise a self-contained
# feature with its own two tables.
STOCK_TABLES_SQL = [
    '''CREATE TABLE IF NOT EXISTS stock_watchlist (
        id BIGSERIAL PRIMARY KEY,
        symbol TEXT NOT NULL,
        exchange TEXT NOT NULL DEFAULT 'NSE' CHECK (exchange IN ('NSE', 'BSE')),
        name TEXT,
        is_active INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(symbol, exchange)
    )''',
    '''CREATE TABLE IF NOT EXISTS stock_daily_data (
        id BIGSERIAL PRIMARY KEY,
        watchlist_id BIGINT NOT NULL REFERENCES stock_watchlist(id),
        trade_date DATE NOT NULL,
        open NUMERIC,
        high NUMERIC,
        low NUMERIC,
        close NUMERIC,
        volume BIGINT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(watchlist_id, trade_date)
    )''',
]


# Added after stock_watchlist already had data in it -- kept as a separate
# additive migration, same pattern as everywhere else in this codebase.
# DEFAULT 'manual' means every pre-existing row (added before this column
# existed, i.e. by hand) is treated as manually curated, so
# run_fundamental_shortlist()'s "never touch a row with a different source"
# rule protects them automatically without a separate backfill step.
STOCK_WATCHLIST_ALTER_SQL = [
    "ALTER TABLE stock_watchlist ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'manual'",
    # 'golden' = passed every fundamental screening criterion outright;
    # 'silver' = passed everything except PE range and/or OPM, which are a
    # softer second-level filter (see fundamental_screen.classify_fundamental_tier).
    # NULL for rows predating this column and for manually-added rows, which
    # were never screened at all.
    "ALTER TABLE stock_watchlist ADD COLUMN IF NOT EXISTS fundamental_tier TEXT "
    "CHECK (fundamental_tier IS NULL OR fundamental_tier IN ('golden', 'silver'))",
    # Which screening pipeline a watchlist row came from -- the original
    # 5000-30000cr mid-cap pipeline (run_fundamental_shortlist) or the new,
    # wholly parallel above-30000cr large-cap one (run_large_cap_shortlist,
    # utils/stock_shortlist.py). DEFAULT 'mid_cap' retroactively backfills
    # every existing row (all of them came from the mid-cap pipeline, the
    # only one that existed before this column) with no separate UPDATE
    # needed. run_fundamental_shortlist() itself is untouched -- its INSERT
    # doesn't mention this column at all, so its rows simply keep getting
    # this same DEFAULT; only run_large_cap_shortlist()'s INSERT sets
    # 'large_cap' explicitly.
    "ALTER TABLE stock_watchlist ADD COLUMN IF NOT EXISTS market_cap_tier TEXT DEFAULT 'mid_cap' "
    "CHECK (market_cap_tier IN ('mid_cap', 'large_cap'))",
]

# Same watchlist_id/universe_id duality as stock_fundamentals (see
# utils/fundamentals_ingestion.py's STOCK_FUNDAMENTALS_ALTER_SQL comment for
# the full reasoning) -- sync_daily_data_universe below covers the full
# ~1,067-company scrape-eligible stock_universe set so technical indicators
# (golden cross etc.) can be computed for companies that were never
# fundamentally shortlisted, not just the ~80 that made stock_watchlist.
STOCK_DAILY_DATA_ALTER_SQL = [
    'ALTER TABLE stock_daily_data ALTER COLUMN watchlist_id DROP NOT NULL',
    'ALTER TABLE stock_daily_data ADD COLUMN IF NOT EXISTS universe_id BIGINT REFERENCES stock_universe(id)',
    '''CREATE UNIQUE INDEX IF NOT EXISTS stock_daily_data_universe_trade_date_uniq
       ON stock_daily_data (universe_id, trade_date) WHERE universe_id IS NOT NULL''',
]

# How many Kite historical-data calls per second sync_daily_data_universe
# allows itself -- Kite's documented rate limit is roughly 3/sec for this
# endpoint; the existing watchlist-scoped sync_daily_data never needed
# this (only ~80 symbols, comfortably under any burst allowance), but a
# ~1,067-symbol run without pacing would get throttled partway through.
UNIVERSE_SYNC_DELAY_SECONDS = 0.35


def initialize_stock_tables_if_needed(client):
    """Create stock_watchlist / stock_daily_data if they don't exist yet.
    Call once at app startup, same as app.py's initialize_database_if_needed()
    -- idempotent, existing data is never touched."""
    for sql in STOCK_TABLES_SQL + STOCK_WATCHLIST_ALTER_SQL + STOCK_DAILY_DATA_ALTER_SQL:
        try:
            client.rpc('execute_sql', {'query': sql}).execute()
        except Exception as e:
            print(f'Stock table init warning (may already exist): {e}')


def _parse_last_date(last_date):
    if last_date is None:
        return None
    if isinstance(last_date, str):
        return date.fromisoformat(last_date[:10])
    return last_date


def _compute_from_date(last_date, row_count, today):
    """Decides how far back to ask Kite for. Below
    MIN_HISTORY_DAYS_BEFORE_INCREMENTAL_SYNC existing rows, always asks
    for the full BACKFILL_DAYS window regardless of last_date -- otherwise
    a symbol that already has some (but not enough) history would only
    ever gain one day per sync going forward, never catching up to a
    200-day moving average. Upserting the overlap is harmless."""
    if row_count < MIN_HISTORY_DAYS_BEFORE_INCREMENTAL_SYNC:
        return today - timedelta(days=BACKFILL_DAYS)
    return (last_date + timedelta(days=1)) if last_date else (today - timedelta(days=BACKFILL_DAYS))


def sync_daily_data(db, kite_client=None):
    """Fetch the latest daily candle (or backfill) for every active
    stock_watchlist row and upsert into stock_daily_data.

    db must support .execute(sql, params).fetchall()/.fetchone() and
    .commit(), matching app.py's SupabaseDB/get_db(). kite_client defaults to
    a real KiteClient built from environment variables; pass a stub/mock to
    test without hitting Kite.

    The returned summary's 'zero_candles' list is distinct from 'failures':
    a symbol lands there when Kite responded successfully (no exception)
    but returned no candles at all for the requested date range -- worth
    watching for since it's otherwise silent and looks identical to a
    symbol that was simply already up to date.
    """
    kite_client = kite_client or KiteClient()

    watchlist_rows = db.execute(
        'SELECT id, symbol, exchange FROM stock_watchlist WHERE is_active=1'
    ).fetchall()

    today = date.today()
    inserted = 0
    failed = 0
    failures = []
    # Kite can return an empty candle list without raising -- e.g. a
    # from_date/to_date window with no completed trading sessions for that
    # instrument. That's indistinguishable from "already up to date" unless
    # tracked separately: a symbol landing here got a real (non-error)
    # response from Kite, just with zero rows in it.
    zero_candles = []

    for i, row in enumerate(watchlist_rows):
        watchlist_id = row['id']
        symbol = row['symbol']
        exchange = row['exchange']

        try:
            latest = db.execute(
                'SELECT MAX(trade_date) AS last_date, COUNT(*) AS row_count FROM stock_daily_data WHERE watchlist_id=?',
                (watchlist_id,)
            ).fetchone()
            last_date = _parse_last_date(latest['last_date'] if latest else None)
            row_count = (latest['row_count'] if latest else 0) or 0

            from_date = _compute_from_date(last_date, row_count, today)
            if from_date > today:
                continue

            # A cached Kite instrument_token (see utils/kite_instrument_map.py)
            # skips fetch_daily_candles()'s own ltp()-based lookup entirely --
            # this is what lets a BSE company whose Kite tradingsymbol doesn't
            # match our stored scrip code still sync. None just means "not
            # matched yet", falling back to the original ltp() lookup.
            instrument_token = get_cached_instrument_token(db, symbol, exchange)
            candles = kite_client.fetch_daily_candles(
                symbol, exchange, from_date, today, instrument_token=instrument_token
            )

            if not candles:
                zero_candles.append({
                    'symbol': symbol, 'exchange': exchange,
                    'from_date': from_date.isoformat(), 'to_date': today.isoformat(),
                })

            for candle in candles:
                trade_date = candle['trade_date']
                trade_date = trade_date.isoformat() if hasattr(trade_date, 'isoformat') else trade_date

                db.execute(
                    '''INSERT INTO stock_daily_data
                           (watchlist_id, trade_date, open, high, low, close, volume)
                       VALUES (?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT (watchlist_id, trade_date) DO UPDATE SET
                           open = EXCLUDED.open,
                           high = EXCLUDED.high,
                           low = EXCLUDED.low,
                           close = EXCLUDED.close,
                           volume = EXCLUDED.volume''',
                    (watchlist_id, trade_date, candle['open'], candle['high'],
                     candle['low'], candle['close'], candle['volume'])
                )
                db.commit()
                inserted += 1

        except Exception as exc:
            failed += 1
            failures.append({'symbol': symbol, 'exchange': exchange, 'error': str(exc)})
            print(f'Stock sync failed for {exchange}:{symbol}: {exc}')

        report_progress(i + 1, len(watchlist_rows))

    return {
        'watchlist_count': len(watchlist_rows),
        'inserted': inserted,
        'failed': failed,
        'failures': failures,
        'zero_candles': zero_candles,
    }


def _upsert_daily_candle(db, trade_date, candle, watchlist_id=None, universe_id=None):
    """Upserts one stock_daily_data row, keyed by whichever identity
    applies -- same dual-path reasoning as
    fundamentals_ingestion._upsert_fundamentals_snapshot: when a company is
    both watchlisted and in the scrape-eligible universe, the row is keyed
    by watchlist_id (the original identity) with universe_id also stamped
    on it, so the same company's price history doesn't fragment across two
    row identities depending on which sync last touched it. When only
    universe_id is available, the row is keyed by universe_id instead."""
    if watchlist_id is None and universe_id is None:
        raise ValueError('_upsert_daily_candle requires watchlist_id and/or universe_id')

    if watchlist_id is not None:
        db.execute(
            '''INSERT INTO stock_daily_data (watchlist_id, universe_id, trade_date, open, high, low, close, volume)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (watchlist_id, trade_date) DO UPDATE SET
                   universe_id = EXCLUDED.universe_id,
                   open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
                   close = EXCLUDED.close, volume = EXCLUDED.volume''',
            (watchlist_id, universe_id, trade_date, candle['open'], candle['high'],
             candle['low'], candle['close'], candle['volume'])
        )
    else:
        db.execute(
            '''INSERT INTO stock_daily_data (universe_id, trade_date, open, high, low, close, volume)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (universe_id, trade_date) WHERE universe_id IS NOT NULL DO UPDATE SET
                   open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
                   close = EXCLUDED.close, volume = EXCLUDED.volume''',
            (universe_id, trade_date, candle['open'], candle['high'],
             candle['low'], candle['close'], candle['volume'])
        )
    db.commit()


def sync_daily_data_universe(db, kite_client=None, sleep_seconds=UNIVERSE_SYNC_DELAY_SECONDS):
    """Same job as sync_daily_data, but over the full scrape-eligible
    stock_universe set (~1,067 companies) instead of just the ~80-company
    watchlist -- this is what lets technical indicators (golden cross etc.)
    get computed for a company that hasn't been fundamentally shortlisted
    yet, see stock_indicators.run_indicator_calculation_universe.

    LEFT JOINed against stock_watchlist so a company that's in both sets
    keeps one unified stock_daily_data row (see _upsert_daily_candle)
    rather than fragmenting its price history across two row identities.

    Paced with a short sleep between Kite calls (sleep_seconds) to stay
    under Kite's historical-data rate limit across ~1,067 sequential
    requests -- sync_daily_data never needed this at ~80 symbols. Meant to
    run as a background job (see app.py) given how long a full run takes."""
    kite_client = kite_client or KiteClient()

    rows = db.execute(
        '''SELECT u.id AS universe_id, u.symbol, u.exchange, w.id AS watchlist_id
           FROM stock_universe u
           LEFT JOIN stock_watchlist w ON w.symbol = u.symbol AND w.exchange = u.exchange AND w.is_active = 1
           WHERE u.is_scrape_eligible = true'''
    ).fetchall()

    today = date.today()
    inserted = 0
    failed = 0
    failures = []
    zero_candles = []

    for i, row in enumerate(rows):
        universe_id = row['universe_id']
        watchlist_id = row.get('watchlist_id')
        symbol = row['symbol']
        exchange = row['exchange']

        try:
            latest = db.execute(
                'SELECT MAX(trade_date) AS last_date, COUNT(*) AS row_count FROM stock_daily_data WHERE universe_id=?',
                (universe_id,)
            ).fetchone()
            last_date = _parse_last_date(latest['last_date'] if latest else None)
            row_count = (latest['row_count'] if latest else 0) or 0

            from_date = _compute_from_date(last_date, row_count, today)
            if from_date > today:
                continue

            instrument_token = get_cached_instrument_token(db, symbol, exchange)
            candles = kite_client.fetch_daily_candles(
                symbol, exchange, from_date, today, instrument_token=instrument_token
            )

            if not candles:
                zero_candles.append({
                    'symbol': symbol, 'exchange': exchange,
                    'from_date': from_date.isoformat(), 'to_date': today.isoformat(),
                })

            for candle in candles:
                trade_date = candle['trade_date']
                trade_date = trade_date.isoformat() if hasattr(trade_date, 'isoformat') else trade_date
                _upsert_daily_candle(db, trade_date, candle, watchlist_id=watchlist_id, universe_id=universe_id)
                inserted += 1

        except Exception as exc:
            failed += 1
            failures.append({'symbol': symbol, 'exchange': exchange, 'error': str(exc)})
            print(f'Universe stock sync failed for {exchange}:{symbol}: {exc}')

        report_progress(i + 1, len(rows))

        if sleep_seconds and i < len(rows) - 1:
            time.sleep(sleep_seconds)

    return {
        'universe_count': len(rows),
        'inserted': inserted,
        'failed': failed,
        'failures': failures,
        'zero_candles': zero_candles,
    }
