import csv
import io
import json
from datetime import date

# The full NSE/BSE listed-company universe -- separate from stock_watchlist,
# which is the manually-curated (and, going forward, auto-shortlisted)
# subset actually tracked day to day. last_fundamentals_fetch lives here
# rather than on stock_fundamentals because the future rotation scraper
# needs to rank the *entire* universe by staleness to pick each day's 300,
# and most of the universe will never have a stock_fundamentals row at all
# until the scraper reaches it.
STOCK_UNIVERSE_TABLE_SQL = [
    '''CREATE TABLE IF NOT EXISTS stock_universe (
        id BIGSERIAL PRIMARY KEY,
        symbol TEXT NOT NULL,
        exchange TEXT NOT NULL DEFAULT 'NSE' CHECK (exchange IN ('NSE', 'BSE')),
        company_name TEXT,
        shares_outstanding BIGINT,
        isin TEXT,
        is_active BOOLEAN DEFAULT true,
        last_market_cap NUMERIC(18,2),
        last_market_cap_date DATE,
        last_fundamentals_fetch TIMESTAMPTZ,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(symbol, exchange)
    )'''
]


# Added after the table above already had data in it -- kept separate so
# ADD COLUMN IF NOT EXISTS applies cleanly, same additive-migration pattern
# used everywhere else in this codebase. market_cap_band/is_scrape_eligible
# are computed by rebucket_market_cap_bands() below from last_market_cap,
# not set directly -- default here just covers freshly inserted rows before
# the first rebucket ever runs.
STOCK_UNIVERSE_ALTER_SQL = [
    "ALTER TABLE stock_universe ADD COLUMN IF NOT EXISTS is_scrape_eligible BOOLEAN DEFAULT false",
    "ALTER TABLE stock_universe ADD COLUMN IF NOT EXISTS market_cap_band TEXT DEFAULT 'unknown'",
    # Screener.in's sector/industry breadcrumb (see
    # screener_client._parse_industry_classification), stamped here -- a
    # company-level attribute, not a per-snapshot one -- whenever
    # sync_fundamentals_rotation scrapes this company. NULL until first
    # scraped. Feeds fundamental_screen.py's industry-relative PE/
    # price-to-book screening (see stock_shortlist._compute_industry_benchmarks).
    "ALTER TABLE stock_universe ADD COLUMN IF NOT EXISTS industry TEXT",
    # Large-cap tier (see rebucket_large_cap_eligibility below and
    # utils/fundamental_screen.py's score_fundamentals_large_cap) -- a
    # wholly separate, parallel eligibility flag alongside is_scrape_eligible
    # above, not a replacement for it. True whenever last_market_cap is
    # above 30000cr (same threshold market_cap_band's 'above_30000cr' band
    # already uses), with NO upper bound, unlike is_scrape_eligible's
    # 5000-30000cr window.
    "ALTER TABLE stock_universe ADD COLUMN IF NOT EXISTS is_large_cap_eligible BOOLEAN DEFAULT false",
]


def initialize_stock_universe_table_if_needed(client):
    for sql in STOCK_UNIVERSE_TABLE_SQL + STOCK_UNIVERSE_ALTER_SQL:
        try:
            client.rpc('execute_sql', {'query': sql}).execute()
        except Exception as e:
            print(f'Stock universe table init warning (may already exist): {e}')


def parse_nse_equity_csv(csv_text):
    """Parses NSE's official bulk equity list (the EQUITY_L.csv format:
    SYMBOL, NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE,
    MARKET LOT, ISIN NUMBER, FACE VALUE -- the real file has inconsistent
    leading spaces on all but the first header name, hence the strip() on
    keys/values below). Returns a list of {symbol, exchange, company_name,
    isin} dicts; exchange is always 'NSE' since this source only covers
    NSE-listed equities -- shares_outstanding and market cap aren't in this
    file and are left for a later per-company backfill."""
    reader = csv.DictReader(io.StringIO(csv_text))
    rows = []
    for raw_row in reader:
        row = {(k or '').strip(): (v or '').strip() for k, v in raw_row.items()}
        symbol = row.get('SYMBOL', '')
        if not symbol:
            continue
        rows.append({
            'symbol': symbol,
            'exchange': 'NSE',
            'company_name': row.get('NAME OF COMPANY', ''),
            'isin': row.get('ISIN NUMBER', ''),
        })
    return rows


def parse_bse_equity_json(json_text):
    """Parses BSE's official active-equity scrip list.

    The URL originally given for this (bseindia.com/downloads1/List_of_companies.csv)
    turned out to serve a completely different dataset -- BSE's "GSM"
    (Graded Surveillance Measure) watchlist, ~850 flagged companies with
    columns Sr. No./Scrip code/Security Name/ISIN/GSM Stage, not the general
    equity universe. The actual source used instead is BSE's scrip-master
    API: api.bseindia.com/BseIndiaAPI/api/ListofScripData/w (JSON, requires
    a Referer header or it 200s with an HTML page instead of data) -- see
    seed_stock_universe.py. Because the source is JSON with an entirely
    different shape, this is a dedicated parser rather than a reuse of
    parse_nse_equity_csv.

    Uses SCRIP_CD (BSE's numeric scrip code, e.g. "500002") as symbol --
    not scrip_id, a shorter mnemonic that isn't populated on every row --
    since the numeric code is BSE's always-present identifier and what
    brokers/Kite actually use to trade BSE equities. Unlike NSE's bulk file,
    this source does include a market cap figure (Mktcap), so it's parsed
    here too even though stock_universe's other seed path doesn't have one.
    Returns a list of {symbol, exchange, company_name, isin, market_cap}
    dicts; market_cap is None when missing/unparseable."""
    records = json.loads(json_text)
    rows = []
    for record in records:
        if (record.get('Status') or '').strip().lower() != 'active':
            continue
        if (record.get('Segment') or '').strip().lower() != 'equity':
            continue
        symbol = (record.get('SCRIP_CD') or '').strip()
        if not symbol:
            continue

        market_cap = None
        raw_mktcap = record.get('Mktcap')
        if raw_mktcap not in (None, '', 'null'):
            try:
                market_cap = float(raw_mktcap)
            except (TypeError, ValueError):
                market_cap = None

        rows.append({
            'symbol': symbol,
            'exchange': 'BSE',
            'company_name': (record.get('Scrip_Name') or '').strip(),
            'isin': (record.get('ISIN_NUMBER') or '').strip(),
            'market_cap': market_cap,
        })
    return rows


def _sql_escape(value):
    if value is None or value == '':
        return 'NULL'
    escaped = str(value).replace("'", "''")
    return f"'{escaped}'"


def seed_stock_universe(client, rows, batch_size=200):
    """Upserts parsed rows (from parse_nse_equity_csv) into stock_universe in
    batches, ON CONFLICT (symbol, exchange) updating only company_name/isin
    -- is_active/shares_outstanding/last_market_cap are deliberately left
    alone on conflict so re-running this is safe and doesn't clobber data a
    later process has since filled in. Runs against the raw Supabase client,
    not app.py's db wrapper -- called from the standalone
    seed_stock_universe.py script, outside any Flask request. Returns the
    number of rows upserted."""
    inserted = 0
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        values_sql = ',\n'.join(
            f"({_sql_escape(r['symbol'])}, {_sql_escape(r['exchange'])}, "
            f"{_sql_escape(r['company_name'])}, {_sql_escape(r['isin'])})"
            for r in batch
        )
        sql = f'''INSERT INTO stock_universe (symbol, exchange, company_name, isin)
                   VALUES {values_sql}
                   ON CONFLICT (symbol, exchange) DO UPDATE SET
                       company_name = EXCLUDED.company_name,
                       isin = EXCLUDED.isin,
                       updated_at = NOW()'''
        client.rpc('execute_sql', {'query': sql}).execute()
        inserted += len(batch)
    return inserted


def propagate_bse_market_cap_to_nse(db):
    """Copies last_market_cap/last_market_cap_date from a BSE row onto the
    NSE row of the same company, matched by ISIN, for NSE rows that don't
    have a market cap yet. Never merges or deletes either row -- both stay
    as separate stock_universe rows, only the two columns get copied. Runs
    as a set-based self-join UPDATE, not a per-row loop, since
    stock_universe has thousands of rows and a Python loop issuing one RPC
    call per row would be far slower over HTTP. Takes app.py's db
    (get_db()), not the raw client -- this runs inside a request (the
    refresh route), unlike the seed_* functions above which run from the
    standalone seed script.

    "propagated" is measured as a before/after diff on the same simple
    COUNT, not a separate up-front JOIN-based estimate -- an earlier version
    used a second, differently-shaped COUNT query to predict the number
    before running the UPDATE, and in production that predicted number came
    back 0 while the UPDATE itself still correctly propagated thousands of
    rows (confirmed against live data: NSE rows with a market cap after the
    UPDATE landed exactly on the full NSE/BSE ISIN overlap count). Measuring
    the same query's state before and after removes that discrepancy
    entirely, whatever caused it, since there's only one query shape to
    trust instead of two that have to agree."""
    before = db.execute(
        "SELECT COUNT(*) AS count FROM stock_universe WHERE exchange='NSE' AND last_market_cap IS NULL"
    ).fetchone()
    before_count = (before['count'] if before else 0) or 0

    # Always run -- harmless/no-op if nothing matches, and avoids trusting a
    # separate pre-check to decide whether to bother.
    db.execute(
        '''UPDATE stock_universe AS nse
           SET last_market_cap = bse.last_market_cap,
               last_market_cap_date = bse.last_market_cap_date,
               updated_at = NOW()
           FROM stock_universe AS bse
           WHERE nse.exchange = 'NSE'
             AND bse.exchange = 'BSE'
             AND nse.isin = bse.isin
             AND nse.isin IS NOT NULL AND nse.isin != ''
             AND nse.last_market_cap IS NULL
             AND bse.last_market_cap IS NOT NULL'''
    )
    db.commit()

    remaining = db.execute(
        "SELECT COUNT(*) AS count FROM stock_universe WHERE exchange='NSE' AND last_market_cap IS NULL"
    ).fetchone()
    remaining_count = (remaining['count'] if remaining else 0) or 0
    to_propagate = before_count - remaining_count

    print(f'Market cap propagation: {to_propagate} NSE rows got a value from BSE, '
          f'{remaining_count} NSE rows still have none (no BSE-listed counterpart).')
    return {'propagated': to_propagate, 'remaining_without_market_cap': remaining_count}


def rebucket_market_cap_bands(db):
    """Recomputes market_cap_band and is_scrape_eligible for every
    stock_universe row from its current last_market_cap -- one set-based
    UPDATE, not a per-row loop. Only the '5000_to_30000cr' band is
    scrape-eligible; that's the actual daily-scrape target size for the
    future rotation scraper (not built in this task). last_market_cap is
    assumed to already be in rupees crore, matching the unit BSE's
    scrip-master API returns it in.

    WHERE id IS NOT NULL below is not decorative -- the execute_sql Postgres
    function this app calls through rejects any UPDATE with no WHERE clause
    at all ("UPDATE requires a WHERE clause", error code 21000) as a safety
    guard, and app.py's SupabaseCursor swallows that error silently (catches
    it, logs server-side only, returns as if nothing happened) rather than
    raising it to the caller. Confirmed directly against the live database
    while debugging why every row stayed at its 'unknown' default. id IS NOT
    NULL is always true (id is the primary key) so this still updates every
    row -- it exists purely to satisfy the guard."""
    db.execute(
        '''UPDATE stock_universe
           SET market_cap_band = CASE
                   WHEN last_market_cap IS NULL THEN 'unknown'
                   WHEN last_market_cap < 5000 THEN 'below_5000cr'
                   WHEN last_market_cap <= 30000 THEN '5000_to_30000cr'
                   ELSE 'above_30000cr'
               END,
               is_scrape_eligible = (last_market_cap IS NOT NULL AND last_market_cap >= 5000 AND last_market_cap <= 30000),
               updated_at = NOW()
           WHERE id IS NOT NULL'''
    )
    db.commit()

    eligible = db.execute(
        'SELECT COUNT(*) AS count FROM stock_universe WHERE is_scrape_eligible = true'
    ).fetchone()
    eligible_count = (eligible['count'] if eligible else 0) or 0

    print(f'Market cap re-bucketing complete. {eligible_count} rows are scrape-eligible (5000-30000cr band).')
    return eligible_count


def rebucket_large_cap_eligibility(db):
    """Recomputes is_large_cap_eligible for every stock_universe row from
    its current last_market_cap -- a wholly separate, parallel companion to
    rebucket_market_cap_bands() above, not a modification of it: this
    deliberately does not call, depend on, or need to run before/after
    that function. Both are derived independently from the same
    last_market_cap column, so either can run in any order, or one could
    even be skipped entirely, without affecting the other's result.

    True whenever last_market_cap is above 30000 (crore) -- same threshold
    market_cap_band's 'above_30000cr' band already uses -- with NO upper
    bound on this tier (unlike is_scrape_eligible's 5000-30000cr window,
    a large-cap company doesn't stop being large-cap eligible just because
    it grows further).

    Same WHERE id IS NOT NULL safety-guard reasoning as
    rebucket_market_cap_bands (the execute_sql RPC function this app calls
    through rejects any UPDATE with no WHERE clause at all)."""
    db.execute(
        '''UPDATE stock_universe
           SET is_large_cap_eligible = (last_market_cap IS NOT NULL AND last_market_cap > 30000),
               updated_at = NOW()
           WHERE id IS NOT NULL'''
    )
    db.commit()

    eligible = db.execute(
        'SELECT COUNT(*) AS count FROM stock_universe WHERE is_large_cap_eligible = true'
    ).fetchone()
    eligible_count = (eligible['count'] if eligible else 0) or 0

    print(f'Large-cap eligibility re-bucketing complete. {eligible_count} rows are large-cap eligible (above 30000cr).')
    return eligible_count


def refresh_market_cap_filter(db):
    """Orchestrates both steps: propagate BSE market cap onto matching NSE
    rows by ISIN, then re-bucket every row's market_cap_band/
    is_scrape_eligible from whatever last_market_cap is now stored. Doesn't
    re-fetch BSE's own market cap -- that's a separate future job to keep
    Mktcap current; this only consumes what's already in the table. Called
    by POST /stocks/universe/refresh-market-cap-filter in app.py."""
    propagation = propagate_bse_market_cap_to_nse(db)
    eligible_count = rebucket_market_cap_bands(db)
    return {
        'propagated': propagation['propagated'],
        'remaining_without_market_cap': propagation['remaining_without_market_cap'],
        'scrape_eligible_count': eligible_count,
    }


def seed_bse_universe(client, rows, batch_size=200):
    """Upserts parsed rows (from parse_bse_equity_json) into stock_universe,
    same ON CONFLICT (symbol, exchange) pattern as seed_stock_universe()
    above -- kept as a fully separate function rather than modifying that
    one, so the existing NSE seed path is untouched. A company listed on
    both exchanges gets two rows here (one per exchange, since BSE and NSE
    use different symbols for the same company and Kite treats them as
    separate tradeable instruments) -- this never merges into or looks up
    the NSE row for the same company. Also fills last_market_cap /
    last_market_cap_date when the source has one, via COALESCE so a row
    that already has a value (e.g. filled in by something else later)
    doesn't get overwritten with NULL on a re-run that failed to fetch it
    that time. Returns the number of rows upserted."""
    today = date.today().isoformat()
    inserted = 0
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        values_sql = ',\n'.join(
            f"({_sql_escape(r['symbol'])}, {_sql_escape(r['exchange'])}, "
            f"{_sql_escape(r['company_name'])}, {_sql_escape(r['isin'])}, "
            f"{r['market_cap'] if r['market_cap'] is not None else 'NULL'}, "
            f"{_sql_escape(today) if r['market_cap'] is not None else 'NULL'})"
            for r in batch
        )
        sql = f'''INSERT INTO stock_universe
                       (symbol, exchange, company_name, isin, last_market_cap, last_market_cap_date)
                   VALUES {values_sql}
                   ON CONFLICT (symbol, exchange) DO UPDATE SET
                       company_name = EXCLUDED.company_name,
                       isin = EXCLUDED.isin,
                       last_market_cap = COALESCE(EXCLUDED.last_market_cap, stock_universe.last_market_cap),
                       last_market_cap_date = COALESCE(EXCLUDED.last_market_cap_date, stock_universe.last_market_cap_date),
                       updated_at = NOW()'''
        client.rpc('execute_sql', {'query': sql}).execute()
        inserted += len(batch)
    return inserted
