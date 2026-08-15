"""
Read-only dry run of utils/stock_shortlist.py's fundamental screen -- runs
the exact same candidate query and classify_fundamental_tier() logic that
/stocks/watchlist/refresh-shortlist uses, but never writes to
stock_watchlist. Use this to sanity-check how many companies would pass
(and how many golden vs silver) before actually triggering a refresh. Run
manually:

    python dry_run_shortlist.py

Requires SUPABASE_URL and SUPABASE_KEY in the environment (.env is loaded
automatically, same as the rest of this project).
"""
import os
from datetime import date, timedelta

from dotenv import load_dotenv
from supabase import create_client

from utils.fundamental_screen import classify_fundamental_tier
from utils.stock_shortlist import MAX_SNAPSHOT_AGE_DAYS


def run(client):
    cutoff = (date.today() - timedelta(days=MAX_SNAPSHOT_AGE_DAYS)).isoformat()

    # Mirrors run_fundamental_shortlist's candidate query exactly (see
    # utils/stock_shortlist.py) -- same JOIN, same staleness cutoff.
    sql = f'''SELECT u.id AS universe_id, u.symbol, u.exchange, u.company_name,
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
             AND f.snapshot_date >= '{cutoff}\''''

    # .strip() before sending, same defensive habit SupabaseCursor.__init__
    # always applies in app.py -- the execute_sql RPC silently returns
    # nothing if the query starts with a newline/tab instead of the SELECT
    # keyword itself (leading plain spaces are fine). Skipping this here
    # would silently produce a false "0 candidates" reading.
    result = client.rpc('execute_sql', {'query': sql.strip()}).execute()
    candidates = result.data or []

    passed = 0
    golden = 0
    silver = 0
    failed_counts = {}
    for row in candidates:
        tier, failed = classify_fundamental_tier(row, previous_fundamentals_row=None)
        if tier:
            passed += 1
            if tier == 'golden':
                golden += 1
            else:
                silver += 1
        else:
            for criterion in failed:
                failed_counts[criterion] = failed_counts.get(criterion, 0) + 1

    return {
        'candidates': len(candidates),
        'passed': passed,
        'golden': golden,
        'silver': silver,
        'failed_criteria_counts': failed_counts,
    }


if __name__ == '__main__':
    load_dotenv()
    supabase_url = os.environ.get('SUPABASE_URL')
    supabase_key = os.environ.get('SUPABASE_KEY')
    if not supabase_url or not supabase_key:
        raise SystemExit('SUPABASE_URL and SUPABASE_KEY must be set (check .env).')

    client = create_client(supabase_url, supabase_key)
    summary = run(client)

    print(f"Candidates (fresh enough snapshot): {summary['candidates']}")
    print(f"Would pass: {summary['passed']} (golden: {summary['golden']}, silver: {summary['silver']})")
    print('Exclusion reason breakdown (companies NOT included, golden or silver):')
    for criterion, count in sorted(summary['failed_criteria_counts'].items(), key=lambda kv: -kv[1]):
        print(f'  {criterion}: {count}')
