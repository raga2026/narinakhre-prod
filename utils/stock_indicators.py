from datetime import date

from utils.indicator_engine import calculate_moving_averages, calculate_rsi, detect_cross_status, detect_volume_trend

# How many trailing rows of stock_daily_data to pull per symbol -- 200 days
# is what ma_200 needs; a small margin above that avoids an off-by-one
# short-changing the longest window.
PRICE_HISTORY_LOOKBACK_ROWS = 250
MIN_DAYS_REQUIRED = 21

STOCK_INDICATORS_TABLE_SQL = [
    '''CREATE TABLE IF NOT EXISTS stock_indicators (
        id BIGSERIAL PRIMARY KEY,
        watchlist_id BIGINT NOT NULL REFERENCES stock_watchlist(id),
        calc_date DATE NOT NULL,
        ma_21 NUMERIC(12,2),
        ma_50 NUMERIC(12,2),
        ma_200 NUMERIC(12,2),
        rsi_14 NUMERIC(6,2),
        volume_avg_20d BIGINT,
        volume_trend TEXT CHECK (volume_trend IN ('confirming', 'diverging', 'insufficient_data')),
        cross_status TEXT CHECK (cross_status IN ('golden_cross', 'death_cross', 'no_clear_trend')),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(watchlist_id, calc_date)
    )'''
]

# Same watchlist_id/universe_id duality as stock_fundamentals and
# stock_daily_data -- run_indicator_calculation_universe below computes
# technicals (including golden cross) for the full ~1,067-company
# scrape-eligible stock_universe set, not just the ~80-company watchlist,
# so a company can be surfaced as "golden cross, but fails fundamentals"
# even though it never made stock_watchlist. ma_5 is new alongside it --
# was never stored before, only ma_21/50/200.
STOCK_INDICATORS_ALTER_SQL = [
    'ALTER TABLE stock_indicators ALTER COLUMN watchlist_id DROP NOT NULL',
    'ALTER TABLE stock_indicators ADD COLUMN IF NOT EXISTS universe_id BIGINT REFERENCES stock_universe(id)',
    'ALTER TABLE stock_indicators ADD COLUMN IF NOT EXISTS ma_5 NUMERIC(12,2)',
    '''CREATE UNIQUE INDEX IF NOT EXISTS stock_indicators_universe_calc_date_uniq
       ON stock_indicators (universe_id, calc_date) WHERE universe_id IS NOT NULL''',
]


def initialize_stock_indicators_table_if_needed(client):
    for sql in STOCK_INDICATORS_TABLE_SQL + STOCK_INDICATORS_ALTER_SQL:
        try:
            client.rpc('execute_sql', {'query': sql}).execute()
        except Exception as e:
            print(f'Stock indicators table init warning (may already exist): {e}')


def _price_trend_direction(ma21, ma50):
    """Simple trend proxy from the two shorter MAs -- used only to tell
    detect_volume_trend() which direction volume should be confirming.
    Returns None (not 'flat') when either MA is missing, since
    detect_volume_trend treats anything other than 'up'/'down' the same way
    (insufficient_data) regardless."""
    if ma21 is None or ma50 is None:
        return None
    if ma21 > ma50:
        return 'up'
    if ma21 < ma50:
        return 'down'
    return 'flat'


def _compute_indicators(closes, volumes):
    """Pure computation shared by both run_indicator_calculation (watchlist)
    and run_indicator_calculation_universe (full universe) -- closes/volumes
    ordered oldest-first. Returns None if there's fewer than
    MIN_DAYS_REQUIRED days of closes (nothing at all is computable below
    that), else a dict of every stock_indicators column this app fills in
    (excluding the id columns/calc_date, which the caller stamps itself)."""
    if len(closes) < MIN_DAYS_REQUIRED:
        return None

    mas = calculate_moving_averages(closes)
    ma5, ma21, ma50, ma200 = mas[5], mas[21], mas[50], mas[200]
    rsi14 = calculate_rsi(closes)
    cross_status = detect_cross_status(ma21, ma50, ma200)

    price_trend = _price_trend_direction(ma21, ma50)
    recent_volumes = volumes[-20:]
    volume_trend = detect_volume_trend(recent_volumes, price_trend)
    volume_avg_20d = round(sum(recent_volumes) / len(recent_volumes)) if len(recent_volumes) >= 20 else None

    return {
        'ma_5': ma5, 'ma_21': ma21, 'ma_50': ma50, 'ma_200': ma200, 'rsi_14': rsi14,
        'volume_avg_20d': volume_avg_20d, 'volume_trend': volume_trend, 'cross_status': cross_status,
    }


def _upsert_indicator_snapshot(db, calc_date, data, watchlist_id=None, universe_id=None):
    """Upserts one stock_indicators row, keyed by whichever identity
    applies -- same dual-path reasoning as
    fundamentals_ingestion._upsert_fundamentals_snapshot and
    stock_ingestion._upsert_daily_candle."""
    if watchlist_id is None and universe_id is None:
        raise ValueError('_upsert_indicator_snapshot requires watchlist_id and/or universe_id')

    if watchlist_id is not None:
        db.execute(
            '''INSERT INTO stock_indicators
                   (watchlist_id, universe_id, calc_date, ma_5, ma_21, ma_50, ma_200, rsi_14,
                    volume_avg_20d, volume_trend, cross_status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (watchlist_id, calc_date) DO UPDATE SET
                   universe_id = EXCLUDED.universe_id,
                   ma_5 = EXCLUDED.ma_5, ma_21 = EXCLUDED.ma_21, ma_50 = EXCLUDED.ma_50,
                   ma_200 = EXCLUDED.ma_200, rsi_14 = EXCLUDED.rsi_14,
                   volume_avg_20d = EXCLUDED.volume_avg_20d, volume_trend = EXCLUDED.volume_trend,
                   cross_status = EXCLUDED.cross_status''',
            (watchlist_id, universe_id, calc_date, data['ma_5'], data['ma_21'], data['ma_50'], data['ma_200'],
             data['rsi_14'], data['volume_avg_20d'], data['volume_trend'], data['cross_status'])
        )
    else:
        db.execute(
            '''INSERT INTO stock_indicators
                   (universe_id, calc_date, ma_5, ma_21, ma_50, ma_200, rsi_14,
                    volume_avg_20d, volume_trend, cross_status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (universe_id, calc_date) WHERE universe_id IS NOT NULL DO UPDATE SET
                   ma_5 = EXCLUDED.ma_5, ma_21 = EXCLUDED.ma_21, ma_50 = EXCLUDED.ma_50,
                   ma_200 = EXCLUDED.ma_200, rsi_14 = EXCLUDED.rsi_14,
                   volume_avg_20d = EXCLUDED.volume_avg_20d, volume_trend = EXCLUDED.volume_trend,
                   cross_status = EXCLUDED.cross_status''',
            (universe_id, calc_date, data['ma_5'], data['ma_21'], data['ma_50'], data['ma_200'],
             data['rsi_14'], data['volume_avg_20d'], data['volume_trend'], data['cross_status'])
        )
    db.commit()


def run_indicator_calculation(db):
    """For every active stock_watchlist row, pulls up to the last
    PRICE_HISTORY_LOOKBACK_ROWS trading days of close/volume from
    stock_daily_data (oldest first) and computes MAs/RSI/cross status/
    volume trend, upserting one row per symbol per day into
    stock_indicators (keyed on watchlist_id, calc_date=today). Symbols with
    fewer than MIN_DAYS_REQUIRED days of price history are skipped
    (logged, not crashed) -- nothing at all is computable below that."""
    watchlist_rows = db.execute(
        'SELECT id, symbol, exchange FROM stock_watchlist WHERE is_active=1'
    ).fetchall()

    today = date.today().isoformat()
    calculated = 0
    skipped = 0
    failures = []

    for row in watchlist_rows:
        watchlist_id = row['id']
        symbol = row['symbol']

        try:
            price_rows = db.execute(
                '''SELECT close, volume FROM stock_daily_data
                   WHERE watchlist_id=?
                   ORDER BY trade_date ASC''',
                (watchlist_id,)
            ).fetchall()
            if len(price_rows) > PRICE_HISTORY_LOOKBACK_ROWS:
                price_rows = price_rows[-PRICE_HISTORY_LOOKBACK_ROWS:]

            closes = [float(r['close']) for r in price_rows if r['close'] is not None]
            volumes = [int(r['volume']) for r in price_rows if r['volume'] is not None]

            data = _compute_indicators(closes, volumes)
            if data is None:
                skipped += 1
                print(f'Indicator calc skipped for {symbol}: only {len(closes)} days '
                      f'of price history (need {MIN_DAYS_REQUIRED}+).')
                continue

            _upsert_indicator_snapshot(db, today, data, watchlist_id=watchlist_id)
            calculated += 1
        except Exception as exc:
            failures.append({'symbol': symbol, 'error': str(exc)})
            print(f'Indicator calculation failed for {symbol}: {exc}')

    return {
        'watchlist_count': len(watchlist_rows),
        'calculated': calculated,
        'skipped': skipped,
        'failed': len(failures),
        'failures': failures,
    }


def run_indicator_calculation_universe(db):
    """Same job as run_indicator_calculation, but over the full
    scrape-eligible stock_universe set (~1,067 companies) instead of just
    the ~80-company watchlist -- this is what lets a company be surfaced
    as "golden cross, but fails fundamentals" (see app.py's watchlist
    route) even though it was never fundamentally shortlisted.

    Price history is read by universe_id, LEFT JOINed against
    stock_watchlist so a company that's in both sets keeps one unified
    stock_indicators row (see _upsert_indicator_snapshot) rather than
    fragmenting across two row identities. Requires
    sync_daily_data_universe to have already populated stock_daily_data
    for these rows -- a company with no universe_id-keyed price history
    yet is just skipped like any other under MIN_DAYS_REQUIRED."""
    universe_rows = db.execute(
        '''SELECT u.id AS universe_id, u.symbol, u.exchange, w.id AS watchlist_id
           FROM stock_universe u
           LEFT JOIN stock_watchlist w ON w.symbol = u.symbol AND w.exchange = u.exchange AND w.is_active = 1
           WHERE u.is_scrape_eligible = true'''
    ).fetchall()

    today = date.today().isoformat()
    calculated = 0
    skipped = 0
    failures = []

    for row in universe_rows:
        universe_id = row['universe_id']
        watchlist_id = row.get('watchlist_id')
        symbol = row['symbol']

        try:
            price_rows = db.execute(
                '''SELECT close, volume FROM stock_daily_data
                   WHERE universe_id=?
                   ORDER BY trade_date ASC''',
                (universe_id,)
            ).fetchall()
            if len(price_rows) > PRICE_HISTORY_LOOKBACK_ROWS:
                price_rows = price_rows[-PRICE_HISTORY_LOOKBACK_ROWS:]

            closes = [float(r['close']) for r in price_rows if r['close'] is not None]
            volumes = [int(r['volume']) for r in price_rows if r['volume'] is not None]

            data = _compute_indicators(closes, volumes)
            if data is None:
                skipped += 1
                continue

            _upsert_indicator_snapshot(db, today, data, watchlist_id=watchlist_id, universe_id=universe_id)
            calculated += 1
        except Exception as exc:
            failures.append({'symbol': symbol, 'error': str(exc)})
            print(f'Universe indicator calculation failed for {symbol}: {exc}')

    return {
        'universe_count': len(universe_rows),
        'calculated': calculated,
        'skipped': skipped,
        'failed': len(failures),
        'failures': failures,
    }
