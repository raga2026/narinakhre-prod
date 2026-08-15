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


def initialize_stock_indicators_table_if_needed(client):
    for sql in STOCK_INDICATORS_TABLE_SQL:
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

            if len(closes) < MIN_DAYS_REQUIRED:
                skipped += 1
                print(f'Indicator calc skipped for {symbol}: only {len(closes)} days '
                      f'of price history (need {MIN_DAYS_REQUIRED}+).')
                continue

            mas = calculate_moving_averages(closes)
            ma21, ma50, ma200 = mas[21], mas[50], mas[200]
            rsi14 = calculate_rsi(closes)
            cross_status = detect_cross_status(ma21, ma50, ma200)

            price_trend = _price_trend_direction(ma21, ma50)
            recent_volumes = volumes[-20:]
            volume_trend = detect_volume_trend(recent_volumes, price_trend)
            volume_avg_20d = round(sum(recent_volumes) / len(recent_volumes)) if len(recent_volumes) >= 20 else None

            db.execute(
                '''INSERT INTO stock_indicators
                       (watchlist_id, calc_date, ma_21, ma_50, ma_200, rsi_14,
                        volume_avg_20d, volume_trend, cross_status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (watchlist_id, calc_date) DO UPDATE SET
                       ma_21 = EXCLUDED.ma_21,
                       ma_50 = EXCLUDED.ma_50,
                       ma_200 = EXCLUDED.ma_200,
                       rsi_14 = EXCLUDED.rsi_14,
                       volume_avg_20d = EXCLUDED.volume_avg_20d,
                       volume_trend = EXCLUDED.volume_trend,
                       cross_status = EXCLUDED.cross_status''',
                (watchlist_id, today, ma21, ma50, ma200, rsi14,
                 volume_avg_20d, volume_trend, cross_status)
            )
            db.commit()
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
