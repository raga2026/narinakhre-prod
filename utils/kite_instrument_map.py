"""Persists the symbol -> Kite instrument_token mapping produced by
utils/kite_instrument_matching.py, and looks it up during price syncing so
fetch_daily_candles() can skip Kite's ltp() lookup (and the symbol-mismatch
failures that come with it) once a company has been matched once."""
from utils.kite_instrument_matching import match_instruments_to_universe
from utils.job_progress import report as report_progress

KITE_INSTRUMENT_MAP_TABLE_SQL = [
    '''CREATE TABLE IF NOT EXISTS stock_kite_instrument_map (
        id BIGSERIAL PRIMARY KEY,
        symbol TEXT NOT NULL,
        exchange TEXT NOT NULL,
        kite_tradingsymbol TEXT NOT NULL,
        kite_instrument_token BIGINT NOT NULL,
        confidence TEXT NOT NULL CHECK (confidence IN ('exact', 'fuzzy')),
        matched_name TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(symbol, exchange)
    )'''
]

# How many rows per upsert statement -- same batching pattern and size as
# seed_stock_universe.py's stock_universe seeding, for the same reason (a
# single VALUES list covering thousands of rows is unwieldy and risks
# hitting a query-size limit).
UPSERT_BATCH_SIZE = 200


def initialize_kite_instrument_map_table_if_needed(client):
    for sql in KITE_INSTRUMENT_MAP_TABLE_SQL:
        try:
            client.rpc('execute_sql', {'query': sql}).execute()
        except Exception as e:
            print(f'Kite instrument map table init warning (may already exist): {e}')


def get_cached_instrument_token(db, symbol, exchange):
    """Returns the cached Kite instrument_token for (symbol, exchange), or
    None if it's never been matched (or matching found nothing similar
    enough -- see FUZZY_MATCH_THRESHOLD). None means "fall back to ltp()",
    not "this symbol is bad"."""
    row = db.execute(
        'SELECT kite_instrument_token FROM stock_kite_instrument_map WHERE symbol=? AND exchange=?',
        (symbol, exchange)
    ).fetchone()
    return row['kite_instrument_token'] if row else None


def get_cached_kite_tradingsymbol(db, symbol, exchange):
    """Returns Kite's OWN tradingsymbol for (symbol, exchange), or None if
    never matched. Required before placing any real order (see
    utils.auto_trader's live-mode path) -- Kite's order-placement API needs
    ITS tradingsymbol, which for a lot of BSE listings is not the same
    string as this app's own stock_universe/stock_watchlist.symbol (often a
    numeric scrip code Kite's order API doesn't accept at all). A live
    order is never placed with the raw internal symbol as a fallback --
    None here means "don't place this order," not "guess." """
    row = db.execute(
        'SELECT kite_tradingsymbol FROM stock_kite_instrument_map WHERE symbol=? AND exchange=?',
        (symbol, exchange)
    ).fetchone()
    return row['kite_tradingsymbol'] if row else None


def get_cached_kite_name(db, symbol, exchange):
    """Returns the company name Kite itself uses for (symbol, exchange), or
    None if it's never been matched. Kite's naming is internally consistent
    (never mixes 'Ltd' and 'Limited' for the same company across exchanges
    the way our own NSE/BSE source lists do), so callers that need one
    canonical display name for a company -- e.g. after picking a single
    NSE/BSE listing to watchlist -- prefer this over stock_universe's raw
    company_name."""
    row = db.execute(
        'SELECT matched_name FROM stock_kite_instrument_map WHERE symbol=? AND exchange=?',
        (symbol, exchange)
    ).fetchone()
    return row['matched_name'] if row and row['matched_name'] else None


def upsert_instrument_map(db, matches):
    """Upserts match_instruments_to_universe()'s output. Re-running this
    (e.g. after Kite lists a new instrument, or a company's Kite
    tradingsymbol changes) safely overwrites a stale mapping rather than
    duplicating rows, via ON CONFLICT (symbol, exchange)."""
    total = len(matches)
    for i in range(0, total, UPSERT_BATCH_SIZE):
        batch = matches[i:i + UPSERT_BATCH_SIZE]
        values_sql = ',\n'.join(
            '(?, ?, ?, ?, ?, ?)' for _ in batch
        )
        params = []
        for m in batch:
            params.extend([
                m['symbol'], m['exchange'], m['kite_tradingsymbol'],
                m['kite_instrument_token'], m['confidence'], m.get('matched_name'),
            ])
        sql = f'''INSERT INTO stock_kite_instrument_map
                       (symbol, exchange, kite_tradingsymbol, kite_instrument_token, confidence, matched_name)
                   VALUES {values_sql}
                   ON CONFLICT (symbol, exchange) DO UPDATE SET
                       kite_tradingsymbol = EXCLUDED.kite_tradingsymbol,
                       kite_instrument_token = EXCLUDED.kite_instrument_token,
                       confidence = EXCLUDED.confidence,
                       matched_name = EXCLUDED.matched_name,
                       updated_at = NOW()'''
        db.execute(sql, tuple(params))
        db.commit()
        report_progress(min(i + UPSERT_BATCH_SIZE, total), total, label='Saving matched Kite instruments')


def sync_kite_instrument_map(db, kite_client):
    """Fetches Kite's full NSE + BSE equity instrument list, matches it
    against every stock_universe company by name (see
    kite_instrument_matching.match_instruments_to_universe), and stores the
    result. Scoped to all of stock_universe rather than just the current
    watchlist -- the watchlist churns weekly via the fundamental shortlist,
    so matching the full universe once means a newly-shortlisted company
    already has its Kite token cached instead of needing another live match
    run. Safe to re-run periodically; unmatched companies simply stay
    unmatched (fetch_daily_candles() falls back to its existing ltp()
    lookup for those, unchanged from before this mapping existed).

    Returns a summary: candidates evaluated, matched (with exact/fuzzy
    breakdown), and unmatched count."""
    universe_rows = db.execute(
        'SELECT symbol, exchange, company_name FROM stock_universe'
    ).fetchall()

    kite_instruments = (
        kite_client.fetch_instruments('NSE') + kite_client.fetch_instruments('BSE')
    )

    report_progress(0, len(universe_rows) or 1, label='Matching companies against Kite instrument list')
    matches = match_instruments_to_universe(universe_rows, kite_instruments)
    upsert_instrument_map(db, matches)

    exact = sum(1 for m in matches if m['confidence'] == 'exact')
    fuzzy = sum(1 for m in matches if m['confidence'] == 'fuzzy')

    return {
        'universe_count': len(universe_rows),
        'kite_instrument_count': len(kite_instruments),
        'matched': len(matches),
        'exact': exact,
        'fuzzy': fuzzy,
        'unmatched': len(universe_rows) - len(matches),
    }
