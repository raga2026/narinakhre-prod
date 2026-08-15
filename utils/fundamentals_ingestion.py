from datetime import date

from utils.screener_client import fetch_fundamentals

STOCK_FUNDAMENTALS_TABLE_SQL = [
    '''CREATE TABLE IF NOT EXISTS stock_fundamentals (
        id BIGSERIAL PRIMARY KEY,
        watchlist_id BIGINT NOT NULL REFERENCES stock_watchlist(id),
        snapshot_date DATE NOT NULL,
        pe_ratio NUMERIC,
        peg_ratio NUMERIC,
        eps NUMERIC,
        market_cap NUMERIC,
        roe NUMERIC,
        debt_to_equity NUMERIC,
        earnings_growth_pct NUMERIC,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(watchlist_id, snapshot_date)
    )'''
]

# Screening-logic fields added later than the table above -- kept as a
# separate additive migration (ADD COLUMN IF NOT EXISTS) so it applies
# cleanly to a stock_fundamentals table that already has data, same pattern
# app.py's own schema uses. All nullable: the scraper that will actually
# populate them is future work, not part of this change. debt_to_equity
# already existed (see table above) and covers what a "debt_to_equity_ratio"
# column would have meant, so it's reused as-is rather than duplicated.
STOCK_FUNDAMENTALS_ALTER_SQL = [
    'ALTER TABLE stock_fundamentals ADD COLUMN IF NOT EXISTS sector_avg_pe NUMERIC',
    'ALTER TABLE stock_fundamentals ADD COLUMN IF NOT EXISTS price_to_book NUMERIC',
    'ALTER TABLE stock_fundamentals ADD COLUMN IF NOT EXISTS opm_pct NUMERIC',
    'ALTER TABLE stock_fundamentals ADD COLUMN IF NOT EXISTS roce_pct NUMERIC',
    'ALTER TABLE stock_fundamentals ADD COLUMN IF NOT EXISTS roa_pct NUMERIC',
    'ALTER TABLE stock_fundamentals ADD COLUMN IF NOT EXISTS current_ratio NUMERIC',
    'ALTER TABLE stock_fundamentals ADD COLUMN IF NOT EXISTS tol_by_tnw NUMERIC',
    'ALTER TABLE stock_fundamentals ADD COLUMN IF NOT EXISTS promoter_holding_pct NUMERIC',
    'ALTER TABLE stock_fundamentals ADD COLUMN IF NOT EXISTS fii_holding_pct NUMERIC',
    'ALTER TABLE stock_fundamentals ADD COLUMN IF NOT EXISTS public_holding_pct NUMERIC',
    'ALTER TABLE stock_fundamentals ADD COLUMN IF NOT EXISTS quarterly_profit_growth_pct NUMERIC',
    'ALTER TABLE stock_fundamentals ADD COLUMN IF NOT EXISTS quarterly_revenue_growth_pct NUMERIC',
    'ALTER TABLE stock_fundamentals ADD COLUMN IF NOT EXISTS free_cash_flow NUMERIC',
]


def initialize_fundamentals_table_if_needed(client):
    for sql in STOCK_FUNDAMENTALS_TABLE_SQL + STOCK_FUNDAMENTALS_ALTER_SQL:
        try:
            client.rpc('execute_sql', {'query': sql}).execute()
        except Exception as e:
            print(f'Stock fundamentals table init warning (may already exist): {e}')


def _compute_peg(pe_ratio, earnings_growth_pct):
    if pe_ratio is None or not earnings_growth_pct:
        return None
    return round(pe_ratio / earnings_growth_pct, 2)


def sync_fundamentals(db, fetch_fn=None):
    """Fetches Screener.in fundamentals for every active stock_watchlist row
    and upserts one snapshot per symbol per day into stock_fundamentals.
    Meant to run weekly (see /admin/stocks/fundamentals/sync and
    /cron/stocks-fundamentals-sync in app.py), not on every price sync --
    fundamentals don't move day to day. fetch_fn defaults to
    screener_client.fetch_fundamentals; tests pass a stub instead."""
    fetch = fetch_fn or fetch_fundamentals

    watchlist_rows = db.execute(
        'SELECT id, symbol, exchange FROM stock_watchlist WHERE is_active=1'
    ).fetchall()

    today = date.today().isoformat()
    inserted = 0
    failed = 0
    failures = []

    for row in watchlist_rows:
        watchlist_id = row['id']
        symbol = row['symbol']

        try:
            data = fetch(symbol)
            peg_ratio = _compute_peg(data.get('pe_ratio'), data.get('earnings_growth_pct'))

            db.execute(
                '''INSERT INTO stock_fundamentals
                       (watchlist_id, snapshot_date, pe_ratio, peg_ratio, eps,
                        market_cap, roe, debt_to_equity, earnings_growth_pct)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (watchlist_id, snapshot_date) DO UPDATE SET
                       pe_ratio = EXCLUDED.pe_ratio,
                       peg_ratio = EXCLUDED.peg_ratio,
                       eps = EXCLUDED.eps,
                       market_cap = EXCLUDED.market_cap,
                       roe = EXCLUDED.roe,
                       debt_to_equity = EXCLUDED.debt_to_equity,
                       earnings_growth_pct = EXCLUDED.earnings_growth_pct''',
                (watchlist_id, today, data.get('pe_ratio'), peg_ratio, data.get('eps'),
                 data.get('market_cap'), data.get('roe'), data.get('debt_to_equity'),
                 data.get('earnings_growth_pct'))
            )
            db.commit()
            inserted += 1
        except Exception as exc:
            failed += 1
            failures.append({'symbol': symbol, 'error': str(exc)})
            print(f'Fundamentals sync failed for {symbol}: {exc}')

    return {
        'watchlist_count': len(watchlist_rows),
        'inserted': inserted,
        'failed': failed,
        'failures': failures,
    }
