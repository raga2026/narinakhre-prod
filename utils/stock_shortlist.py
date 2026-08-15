from datetime import date, timedelta

from utils.fundamental_screen import evaluate_fundamentals

SHORTLIST_SOURCE = 'auto_fundamental_shortlist'
# How stale a stock_fundamentals snapshot can be and still count for
# screening -- older than this, the company is treated as not-yet-evaluated
# this run rather than screened on stale data.
MAX_SNAPSHOT_AGE_DAYS = 20


def run_fundamental_shortlist(db):
    """Runs evaluate_fundamentals() against every scrape-eligible
    stock_universe company with a stock_fundamentals snapshot no older than
    MAX_SNAPSHOT_AGE_DAYS, and syncs the result into stock_watchlist:
      - Passing companies get upserted with source=SHORTLIST_SOURCE,
        is_active=true.
      - Everything previously auto-shortlisted that isn't in this run's
        passing set gets is_active=false -- never deleted, history stays.
        Note this covers two cases, both mapped to the same outcome: a
        company that was evaluated and failed, and one that wasn't
        evaluated at all this run (no longer is_scrape_eligible, or its
        fundamentals data is stale past MAX_SNAPSHOT_AGE_DAYS) -- either
        way, it stops being actively watchlisted until fresh data confirms
        it qualifies again.
      - Rows with any other source value (manual additions) are never
        touched, in either direction -- enforced by the conditional
        ON CONFLICT below, not just by never selecting them.

    Implemented as deactivate-everything-auto-shortlisted-first, then
    reactivate/insert whatever currently passes -- simpler than computing a
    dynamic "stopped passing" list, and reaches the identical end state.

    Returns a summary including aggregate failure-reason counts (e.g. how
    many failed on PE range) for the route to report."""
    db.execute(
        'UPDATE stock_watchlist SET is_active=0, updated_at=NOW() WHERE source=? AND is_active=1',
        (SHORTLIST_SOURCE,)
    )
    db.commit()

    cutoff = (date.today() - timedelta(days=MAX_SNAPSHOT_AGE_DAYS)).isoformat()
    candidates = db.execute(
        '''SELECT u.id AS universe_id, u.symbol, u.exchange, u.company_name,
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

    passed = 0
    failed = 0
    failed_criteria_counts = {}

    for row in candidates:
        universe_id = row['universe_id']
        symbol = row['symbol']
        exchange = row['exchange']

        previous = db.execute(
            '''SELECT promoter_holding_pct, fii_holding_pct
               FROM stock_fundamentals
               WHERE universe_id=? AND snapshot_date < ?
               ORDER BY snapshot_date DESC LIMIT 1''',
            (universe_id, row['snapshot_date'])
        ).fetchone()

        passes, failed_criteria = evaluate_fundamentals(row, previous)

        if passes:
            passed += 1
            db.execute(
                '''INSERT INTO stock_watchlist (symbol, exchange, name, is_active, source)
                   VALUES (?, ?, ?, 1, ?)
                   ON CONFLICT (symbol, exchange) DO UPDATE SET
                       is_active = 1,
                       source = ?,
                       name = EXCLUDED.name,
                       updated_at = NOW()
                   WHERE stock_watchlist.source = ? OR stock_watchlist.source IS NULL''',
                (symbol, exchange, row.get('company_name'), SHORTLIST_SOURCE,
                 SHORTLIST_SOURCE, SHORTLIST_SOURCE)
            )
            db.commit()
        else:
            failed += 1
            for criterion in failed_criteria:
                failed_criteria_counts[criterion] = failed_criteria_counts.get(criterion, 0) + 1

    return {
        'evaluated': len(candidates),
        'passed': passed,
        'failed': failed,
        'failed_criteria_counts': failed_criteria_counts,
    }
