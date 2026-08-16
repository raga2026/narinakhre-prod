from datetime import date, timedelta

from utils.fundamental_screen import classify_fundamental_tier
from utils.kite_instrument_map import get_cached_instrument_token, get_cached_kite_name
from utils.job_progress import report as report_progress

SHORTLIST_SOURCE = 'auto_fundamental_shortlist'
# How stale a stock_fundamentals snapshot can be and still count for
# screening -- older than this, the company is treated as not-yet-evaluated
# this run rather than screened on stale data.
MAX_SNAPSHOT_AGE_DAYS = 20


def _pick_canonical_listing(db, qualifying_rows):
    """qualifying_rows: candidate dicts (all classified golden/silver) that
    share the same ISIN -- i.e. the same real company listed under more
    than one exchange (NSE and BSE both have their own stock_universe row
    for it, each with its own symbol). Only one should ever end up
    watchlisted, or the same company shows up twice in every list/email
    under two different names/symbols.

    Prefers whichever listing actually has a resolved Kite instrument
    token (see utils/kite_instrument_map.py) -- that's the one that can
    actually get price data through /stocks/sync. Falls back to NSE if
    both or neither resolve.

    The winning row's company_name is also swapped for Kite's own name for
    that listing when one is on record (_apply_kite_name below) -- our
    NSE and BSE source lists don't agree on 'Ltd' vs 'Limited' etc. for
    the same company, which otherwise makes the exact same stock look
    like two different names depending on which exchange happened to
    win. Returns a single row."""
    if len(qualifying_rows) == 1:
        return _apply_kite_name(db, qualifying_rows[0])

    with_kite_token = [
        r for r in qualifying_rows
        if get_cached_instrument_token(db, r['symbol'], r['exchange']) is not None
    ]
    if len(with_kite_token) == 1:
        return _apply_kite_name(db, with_kite_token[0])

    nse_rows = [r for r in qualifying_rows if r['exchange'] == 'NSE']
    if nse_rows:
        return _apply_kite_name(db, nse_rows[0])
    return _apply_kite_name(db, qualifying_rows[0])


def _apply_kite_name(db, row):
    """Overrides row['company_name'] with Kite's own name for this listing,
    when one is on record -- falls back to leaving the original
    stock_universe name untouched if this listing was never matched to a
    Kite instrument (see get_cached_kite_name)."""
    kite_name = get_cached_kite_name(db, row['symbol'], row['exchange'])
    if kite_name:
        return {**row, 'company_name': kite_name}
    return row


def _compute_industry_benchmarks(rows):
    """Groups rows (dicts with 'industry', 'pe_ratio', 'price_to_book') by
    industry and computes each industry's average PE and average
    price-to-book, plus how many companies contributed to each -- the
    per-industry context utils.fundamental_screen's industry-relative PE/
    price-to-book bands need (see its MIN_INDUSTRY_SAMPLE_SIZE). Computed
    once per screening run from whatever candidates were already fetched --
    no extra DB round trip, same batching reasoning as the previous-snapshot
    lookup above.

    A company missing 'industry' entirely (not yet scraped -- see
    screener_client._parse_industry_classification) doesn't contribute to
    any average. Within an industry, a company missing just PE or just
    price-to-book contributes to whichever of the two it does have, not
    both -- one company's missing PE shouldn't drag its industry's
    price-to-book average down (or vice versa) by being silently treated
    as zero.

    Returns {industry: {'pe_ratio': {'avg','count'}|None,
    'price_to_book': {'avg','count'}|None}}. A metric key is None when no
    company in that industry has a value for it at all -- fundamental_screen.py's
    benchmark.get('count', 0) check treats that the same as "no benchmark",
    falling back to its flat band."""
    pe_values_by_industry = {}
    pb_values_by_industry = {}
    for row in rows:
        industry = row.get('industry')
        if not industry:
            continue
        if row.get('pe_ratio') is not None:
            pe_values_by_industry.setdefault(industry, []).append(row['pe_ratio'])
        if row.get('price_to_book') is not None:
            pb_values_by_industry.setdefault(industry, []).append(row['price_to_book'])

    industries = set(pe_values_by_industry) | set(pb_values_by_industry)
    benchmarks = {}
    for industry in industries:
        pe_values = pe_values_by_industry.get(industry)
        pb_values = pb_values_by_industry.get(industry)
        benchmarks[industry] = {
            'pe_ratio': {'avg': sum(pe_values) / len(pe_values), 'count': len(pe_values)} if pe_values else None,
            'price_to_book': {'avg': sum(pb_values) / len(pb_values), 'count': len(pb_values)} if pb_values else None,
        }
    return benchmarks


def run_fundamental_shortlist(db):
    """Runs classify_fundamental_tier() against every scrape-eligible
    stock_universe company with a stock_fundamentals snapshot no older than
    MAX_SNAPSHOT_AGE_DAYS, and syncs the result into stock_watchlist:
      - Companies classified 'golden' or 'silver' (see
        fundamental_screen.classify_fundamental_tier) get upserted with
        source=SHORTLIST_SOURCE, is_active=true, fundamental_tier set to
        whichever tier they earned.
      - Everything previously auto-shortlisted that isn't in this run's
        passing set (golden or silver) gets is_active=false -- never
        deleted, history stays. Note this covers two cases, both mapped to
        the same outcome: a company that was evaluated and excluded, and
        one that wasn't evaluated at all this run (no longer
        is_scrape_eligible, or its fundamentals data is stale past
        MAX_SNAPSHOT_AGE_DAYS) -- either way, it stops being actively
        watchlisted until fresh data confirms it qualifies again.
      - Rows with any other source value (manual additions) are never
        touched, in either direction -- enforced by the conditional
        ON CONFLICT below, not just by never selecting them.

    Implemented as deactivate-everything-auto-shortlisted-first, then
    reactivate/insert whatever currently qualifies -- simpler than computing
    a dynamic "stopped qualifying" list, and reaches the identical end state.

    A company listed on both NSE and BSE has two separate stock_universe
    rows (different symbols, same ISIN) and could otherwise pass the
    screen under both and get watchlisted twice under two different
    names/symbols for what's really one company. Qualifying candidates
    are grouped by ISIN before writing anything, and only one listing per
    ISIN is kept -- see _pick_canonical_listing above. The loser still
    counts toward 'passed'/tier breakdown (it did qualify fundamentally)
    but not toward what actually gets watchlisted -- see 'deduped' in the
    returned summary.

    Every row written also has its name normalized to Kite's own naming
    (via _apply_kite_name) when that listing has a Kite match on record,
    so the same company never shows as 'Ltd' in one place and 'Limited'
    in another just because our NSE/BSE source lists don't agree.

    Returns a summary including a golden/silver breakdown, how many
    passing candidates were the losing side of an ISIN duplicate
    ('deduped'), and aggregate exclusion-reason counts (e.g. how many
    were excluded on PE range) for the route to report.
    failed_criteria_counts only reflects companies that were actually
    excluded (tier is None) -- a silver company's PE/OPM "failure" isn't
    counted there, since it didn't cause exclusion."""
    db.execute(
        'UPDATE stock_watchlist SET is_active=0, updated_at=NOW() WHERE source=? AND is_active=1',
        (SHORTLIST_SOURCE,)
    )
    db.commit()

    cutoff = (date.today() - timedelta(days=MAX_SNAPSHOT_AGE_DAYS)).isoformat()
    candidates = db.execute(
        '''SELECT u.id AS universe_id, u.symbol, u.exchange, u.company_name, u.isin, u.industry,
                  f.pe_ratio, f.peg_ratio, f.eps, f.opm_pct, f.roce_pct, f.roa_pct,
                  f.price_to_book, f.promoter_holding_pct, f.fii_holding_pct,
                  f.quarterly_profit_growth_pct, f.quarterly_revenue_growth_pct,
                  f.snapshot_date
           FROM stock_universe u
           JOIN stock_fundamentals f ON f.universe_id = u.id
               AND f.snapshot_date = (
                   SELECT MAX(f2.snapshot_date) FROM stock_fundamentals f2 WHERE f2.universe_id = u.id
               )
           WHERE u.is_scrape_eligible = true
             AND f.snapshot_date >= ?''',
        (cutoff,)
    ).fetchall()

    # Computed once from this run's own candidates, before classifying any
    # of them -- every company's PE/price-to-book needs its industry's
    # average already known to be screened against it. See
    # _compute_industry_benchmarks and fundamental_screen.py's
    # industry-relative bands.
    industry_benchmarks = _compute_industry_benchmarks(candidates)

    # Every candidate's promoter/FII holding-trend check needs that
    # company's previous snapshot -- originally fetched with one query PER
    # candidate (up to 1,067 round trips, ~3+ minutes against live
    # Supabase). Fetched here as a single batched query instead, then
    # matched up in Python -- this is what actually made a full run slow
    # enough to plausibly get interrupted mid-way (see run_fundamental_shortlist's
    # docstring: it deactivates everything before reactivating winners, so
    # an interruption leaves the watchlist emptier than before, not
    # unchanged).
    previous_snapshots_by_universe = {}
    universe_ids = [row['universe_id'] for row in candidates]
    if universe_ids:
        ids_sql = ','.join(str(int(uid)) for uid in universe_ids)
        all_snapshots = db.execute(
            f'''SELECT universe_id, promoter_holding_pct, fii_holding_pct, snapshot_date
                FROM stock_fundamentals
                WHERE universe_id IN ({ids_sql})
                ORDER BY universe_id, snapshot_date DESC'''
        ).fetchall()
        for snap in all_snapshots:
            previous_snapshots_by_universe.setdefault(snap['universe_id'], []).append(snap)

    def _previous_snapshot(universe_id, current_snapshot_date):
        for snap in previous_snapshots_by_universe.get(universe_id, []):
            if snap['snapshot_date'] < current_snapshot_date:
                return snap
        return None

    golden = 0
    silver = 0
    failed = 0
    failed_criteria_counts = {}
    qualifying_by_isin = {}  # isin -> list of (row, tier); None-ISIN rows never grouped
    ungrouped_qualifying = []  # rows with no ISIN on record -- can't be deduped, always kept

    for i, row in enumerate(candidates):
        report_progress(i + 1, len(candidates))

        universe_id = row['universe_id']
        previous = _previous_snapshot(universe_id, row['snapshot_date'])

        tier, failed_criteria = classify_fundamental_tier(
            row, previous, industry_benchmarks=industry_benchmarks.get(row.get('industry'))
        )

        if not tier:
            failed += 1
            for criterion in failed_criteria:
                failed_criteria_counts[criterion] = failed_criteria_counts.get(criterion, 0) + 1
            continue

        if tier == 'golden':
            golden += 1
        else:
            silver += 1

        row_with_tier = {**row, '_tier': tier}
        isin = (row.get('isin') or '').strip()
        if isin:
            qualifying_by_isin.setdefault(isin, []).append(row_with_tier)
        else:
            ungrouped_qualifying.append(_apply_kite_name(db, row_with_tier))

    to_watchlist = list(ungrouped_qualifying)
    deduped = 0
    for isin, rows_for_isin in qualifying_by_isin.items():
        winner = _pick_canonical_listing(db, rows_for_isin)
        to_watchlist.append(winner)
        deduped += len(rows_for_isin) - 1

    passed = len(to_watchlist) + deduped

    for row in to_watchlist:
        tier = row['_tier']
        db.execute(
            '''INSERT INTO stock_watchlist (symbol, exchange, name, is_active, source, fundamental_tier)
               VALUES (?, ?, ?, 1, ?, ?)
               ON CONFLICT (symbol, exchange) DO UPDATE SET
                   is_active = 1,
                   source = ?,
                   name = EXCLUDED.name,
                   fundamental_tier = ?,
                   updated_at = NOW()
               WHERE stock_watchlist.source = ? OR stock_watchlist.source IS NULL''',
            (row['symbol'], row['exchange'], row.get('company_name'), SHORTLIST_SOURCE, tier,
             SHORTLIST_SOURCE, tier, SHORTLIST_SOURCE)
        )
        db.commit()

    return {
        'evaluated': len(candidates),
        'passed': passed,
        'golden': golden,
        'silver': silver,
        'deduped': deduped,
        'failed': failed,
        'failed_criteria_counts': failed_criteria_counts,
    }


def get_golden_cross_not_qualified(db):
    """Companies currently showing a golden cross (see
    utils.stock_indicators.run_indicator_calculation_universe) that are
    NOT on the active watchlist -- i.e. technically in an uptrend but
    excluded fundamentally. For each, runs classify_fundamental_tier()
    against its latest fundamentals to say exactly which criteria it's
    failing, so "golden cross" isn't shown without the reason it isn't
    actually being recommended.

    previous_fundamentals_row is always None here (unlike
    run_fundamental_shortlist, which looks up the prior snapshot) -- this
    is a read-only explainer, not a screening decision, so the
    promoter/FII holding-trend checks are simply skipped rather than
    fetched; a company already excluded on other grounds doesn't need
    that precision, and one passing everything else but only failing a
    trend check would still correctly show as qualifying here.

    Deduped by ISIN the same way run_fundamental_shortlist watchlists only
    one NSE/BSE listing per real company -- see _pick_canonical_listing.

    Returns a list of {universe_id, symbol, exchange, company_name,
    cross_status, rsi_14, ma_5, ma_21, ma_50, ma_200, failed_criteria}."""
    rows = db.execute(
        '''SELECT u.id AS universe_id, u.symbol, u.exchange, u.company_name, u.isin, u.industry,
                  i.cross_status, i.rsi_14, i.ma_5, i.ma_21, i.ma_50, i.ma_200,
                  f.pe_ratio, f.peg_ratio, f.eps, f.opm_pct, f.roce_pct, f.roa_pct,
                  f.price_to_book, f.promoter_holding_pct, f.fii_holding_pct,
                  f.quarterly_profit_growth_pct, f.quarterly_revenue_growth_pct
           FROM stock_universe u
           JOIN stock_indicators i ON i.universe_id = u.id
               AND i.calc_date = (
                   SELECT MAX(i2.calc_date) FROM stock_indicators i2 WHERE i2.universe_id = u.id
               )
           LEFT JOIN stock_fundamentals f ON f.universe_id = u.id
               AND f.snapshot_date = (
                   SELECT MAX(f2.snapshot_date) FROM stock_fundamentals f2 WHERE f2.universe_id = u.id
               )
           LEFT JOIN stock_watchlist w ON w.symbol = u.symbol AND w.exchange = u.exchange AND w.is_active = 1
           WHERE i.cross_status = 'golden_cross' AND w.id IS NULL'''
    ).fetchall()

    by_isin = {}
    ungrouped = []
    for row in rows:
        isin = (row.get('isin') or '').strip()
        if isin:
            by_isin.setdefault(isin, []).append(row)
        else:
            ungrouped.append(_apply_kite_name(db, row))

    deduped_rows = list(ungrouped)
    for isin, rows_for_isin in by_isin.items():
        deduped_rows.append(_pick_canonical_listing(db, rows_for_isin))

    industry_benchmarks = _compute_industry_benchmarks(rows)

    results = []
    for row in deduped_rows:
        _tier, failed_criteria = classify_fundamental_tier(
            row, previous_fundamentals_row=None,
            industry_benchmarks=industry_benchmarks.get(row.get('industry'))
        )
        results.append({**row, 'failed_criteria': failed_criteria})

    results.sort(key=lambda r: (r.get('company_name') or r['symbol']).lower())
    return results
