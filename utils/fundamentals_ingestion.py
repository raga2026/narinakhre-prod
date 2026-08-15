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
#
# watchlist_id/universe_id: the rotation scraper (sync_fundamentals_rotation)
# covers the full ~1,067-company scrape-eligible stock_universe set, most of
# which are NOT in stock_watchlist (that's the much smaller curated/
# shortlisted subset) -- inserting for them under the original NOT NULL
# watchlist_id FK would fail outright. watchlist_id is relaxed to nullable
# and a parallel nullable universe_id (FK to stock_universe) is added, so a
# company gets one row keyed by whichever identity actually applies -- both,
# if it happens to be in both sets (see _upsert_fundamentals_snapshot). The
# original UNIQUE(watchlist_id, snapshot_date) table constraint still
# governs the watchlist-scoped path unchanged; a separate partial unique
# index covers the universe-scoped path (a plain UNIQUE across a nullable
# column wouldn't reliably block duplicates, since NULL never equals NULL
# in SQL).
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
    'ALTER TABLE stock_fundamentals ALTER COLUMN watchlist_id DROP NOT NULL',
    'ALTER TABLE stock_fundamentals ADD COLUMN IF NOT EXISTS universe_id BIGINT REFERENCES stock_universe(id)',
    '''CREATE UNIQUE INDEX IF NOT EXISTS stock_fundamentals_universe_snapshot_uniq
       ON stock_fundamentals (universe_id, snapshot_date) WHERE universe_id IS NOT NULL''',
]

# Every column stock_fundamentals tracks beyond (id, watchlist_id,
# universe_id, snapshot_date, created_at) -- shared by both upsert paths so
# there's exactly one place that has to match the table's real columns.
FUNDAMENTALS_COLUMNS = [
    'pe_ratio', 'peg_ratio', 'eps', 'market_cap', 'roe', 'debt_to_equity',
    'earnings_growth_pct', 'sector_avg_pe', 'price_to_book', 'opm_pct',
    'roce_pct', 'roa_pct', 'current_ratio', 'tol_by_tnw',
    'promoter_holding_pct', 'fii_holding_pct', 'public_holding_pct',
    'quarterly_profit_growth_pct', 'quarterly_revenue_growth_pct', 'free_cash_flow',
]

# How many of the scrape-eligible stock_universe rows to refresh per
# rotation run. At 1,067 eligible companies, 300/day cycles the full set
# roughly every 4 days.
ROTATION_BATCH_SIZE = 300


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


def _upsert_fundamentals_snapshot(db, snapshot_date, data, watchlist_id=None, universe_id=None):
    """Upserts one stock_fundamentals row covering every FUNDAMENTALS_COLUMNS
    field. Requires watchlist_id and/or universe_id. When both are
    available (a company that's in both stock_watchlist and the rotation-
    eligible stock_universe set), the row is keyed by watchlist_id (the
    original identity) with universe_id also stamped onto it, so the same
    company's snapshots don't fragment across two different row identities
    depending on which scraper last touched it. When only universe_id is
    available (the common case for rotation -- most of the universe was
    never manually watchlisted), the row is keyed by universe_id instead,
    using the partial unique index since watchlist_id is NULL there."""
    if watchlist_id is None and universe_id is None:
        raise ValueError('_upsert_fundamentals_snapshot requires watchlist_id and/or universe_id')

    peg_ratio = _compute_peg(data.get('pe_ratio'), data.get('earnings_growth_pct'))
    values = {**data, 'peg_ratio': peg_ratio}

    columns_sql = ', '.join(FUNDAMENTALS_COLUMNS)
    placeholders_sql = ', '.join(['?'] * len(FUNDAMENTALS_COLUMNS))
    update_sql = ', '.join(f'{col} = EXCLUDED.{col}' for col in FUNDAMENTALS_COLUMNS)
    data_params = tuple(values.get(col) for col in FUNDAMENTALS_COLUMNS)

    if watchlist_id is not None:
        db.execute(
            f'''INSERT INTO stock_fundamentals (watchlist_id, universe_id, snapshot_date, {columns_sql})
                VALUES (?, ?, ?, {placeholders_sql})
                ON CONFLICT (watchlist_id, snapshot_date) DO UPDATE SET
                    universe_id = EXCLUDED.universe_id, {update_sql}''',
            (watchlist_id, universe_id, snapshot_date) + data_params
        )
    else:
        db.execute(
            f'''INSERT INTO stock_fundamentals (universe_id, snapshot_date, {columns_sql})
                VALUES (?, ?, {placeholders_sql})
                ON CONFLICT (universe_id, snapshot_date) WHERE universe_id IS NOT NULL DO UPDATE SET
                    {update_sql}''',
            (universe_id, snapshot_date) + data_params
        )
    db.commit()


def sync_fundamentals(db, fetch_fn=None):
    """Fetches Screener.in fundamentals for every active stock_watchlist row
    and upserts one snapshot per symbol per day into stock_fundamentals.
    Meant to run weekly (see /stocks/fundamentals/sync and
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
            _upsert_fundamentals_snapshot(db, today, data, watchlist_id=watchlist_id)
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


def sync_fundamentals_rotation(db, fetch_fn=None, batch_size=ROTATION_BATCH_SIZE):
    """Daily rotation over the full scrape-eligible universe (stock_universe
    WHERE is_scrape_eligible=true) -- picks the batch_size stalest rows
    (oldest last_fundamentals_fetch first, NULLs -- never fetched -- first
    of all) each run. Upserts into stock_fundamentals via the same
    _upsert_fundamentals_snapshot() sync_fundamentals() uses (LEFT JOINed
    against stock_watchlist so a company that's in both sets keeps one
    unified row), then stamps stock_universe.last_fundamentals_fetch so the
    next run picks up where this one left off.

    Per-symbol failures log and continue, and deliberately do NOT update
    last_fundamentals_fetch for that row -- a failed fetch stays near the
    front of the next run's stalest-first queue instead of being skipped
    for another ~4 days."""
    fetch = fetch_fn or fetch_fundamentals

    rows = db.execute(
        '''SELECT u.id AS universe_id, u.symbol, u.exchange, w.id AS watchlist_id
           FROM stock_universe u
           LEFT JOIN stock_watchlist w ON w.symbol = u.symbol AND w.exchange = u.exchange
           WHERE u.is_scrape_eligible = true
           ORDER BY u.last_fundamentals_fetch ASC NULLS FIRST
           LIMIT ?''',
        (batch_size,)
    ).fetchall()

    today = date.today().isoformat()
    scraped = 0
    failed = 0
    failures = []

    for row in rows:
        universe_id = row['universe_id']
        watchlist_id = row['watchlist_id']
        symbol = row['symbol']

        try:
            data = fetch(symbol)
            _upsert_fundamentals_snapshot(db, today, data, watchlist_id=watchlist_id, universe_id=universe_id)
            db.execute(
                'UPDATE stock_universe SET last_fundamentals_fetch = NOW() WHERE id=?',
                (universe_id,)
            )
            db.commit()
            scraped += 1
        except Exception as exc:
            failed += 1
            failures.append({'symbol': symbol, 'error': str(exc)})
            print(f'Rotation fundamentals sync failed for {symbol}: {exc}')

    return {
        'batch_size': len(rows),
        'scraped': scraped,
        'failed': failed,
        'failures': failures,
    }
