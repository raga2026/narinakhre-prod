from datetime import date, timedelta

from utils.kite_client import KiteClient
from utils.kite_instrument_map import get_cached_instrument_token

# How far back to pull the first time a symbol has no rows yet in
# stock_daily_data. Kept short since this is Phase 1 (ingestion only) --
# nothing downstream needs deep history yet.
BACKFILL_DAYS = 30

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
]


def initialize_stock_tables_if_needed(client):
    """Create stock_watchlist / stock_daily_data if they don't exist yet.
    Call once at app startup, same as app.py's initialize_database_if_needed()
    -- idempotent, existing data is never touched."""
    for sql in STOCK_TABLES_SQL + STOCK_WATCHLIST_ALTER_SQL:
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

    for row in watchlist_rows:
        watchlist_id = row['id']
        symbol = row['symbol']
        exchange = row['exchange']

        try:
            latest = db.execute(
                'SELECT MAX(trade_date) AS last_date FROM stock_daily_data WHERE watchlist_id=?',
                (watchlist_id,)
            ).fetchone()
            last_date = _parse_last_date(latest['last_date'] if latest else None)

            from_date = (last_date + timedelta(days=1)) if last_date else (today - timedelta(days=BACKFILL_DAYS))
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

    return {
        'watchlist_count': len(watchlist_rows),
        'inserted': inserted,
        'failed': failed,
        'failures': failures,
        'zero_candles': zero_candles,
    }
