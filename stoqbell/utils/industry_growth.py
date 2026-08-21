"""Industry-wise growth widget for the Stocks home page (see app.py's
/stocks/home) -- NOT real Nifty/Bank Nifty/Sensex index values (this
codebase has no live market-index data source at all), but the average
day-over-day % price change per industry across our OWN tracked universe
(stock_universe.industry, ~1,067 companies with daily prices synced via
sync_universe_daily_prices -- see utils/stock_ingestion.py). Clearly
labeled as such by the caller/template -- this is a proxy built from what
we already scrape, not a substitute for an official index."""

# Same reasoning as fundamental_screen.MIN_INDUSTRY_SAMPLE_SIZE (a lone
# stock or two shouldn't stand in for an entire industry's average move)
# -- its own independent constant since this is a different kind of
# aggregation (daily price change, not PE/price-to-book).
INDUSTRY_GROWTH_MIN_SAMPLE_SIZE = 3


def compute_industry_growth(db, top_n=5):
    """Compares the two most recent trade dates present in stock_daily_data
    (universe-wide, via universe_id -- covers the full ~1,067-company
    scrape-eligible universe, not just the smaller shortlisted watchlist)
    and averages each stock's % close-to-close change within its own
    stock_universe.industry.

    Returns {'available': False} if fewer than two distinct trade dates
    exist yet (e.g. right after a fresh deploy, before price_sync has run
    twice) -- callers should render a "not enough data yet" state rather
    than a misleading single-day snapshot.

    Otherwise returns {'available': True, 'latest_date', 'previous_date',
    'overall_avg_change_pct', 'overall_sample_size', 'gainers', 'losers'}.
    'gainers'/'losers' are each up to top_n {'industry', 'avg_change_pct',
    'sample_size'} dicts -- industries with fewer than
    INDUSTRY_GROWTH_MIN_SAMPLE_SIZE stocks reporting both days' closes are
    excluded entirely (too small a sample to call it an industry trend).
    'losers' never repeats an industry already shown in 'gainers' (matters
    when there are only a handful of qualifying industries total)."""
    dates = db.execute(
        '''SELECT DISTINCT trade_date FROM stock_daily_data
           WHERE universe_id IS NOT NULL ORDER BY trade_date DESC LIMIT 2'''
    ).fetchall()
    if len(dates) < 2:
        return {'available': False}

    latest_date = dates[0]['trade_date']
    previous_date = dates[1]['trade_date']

    rows = db.execute(
        '''SELECT u.industry, d1.close AS latest_close, d0.close AS prev_close
           FROM stock_daily_data d1
           JOIN stock_daily_data d0 ON d0.universe_id = d1.universe_id AND d0.trade_date = ?
           JOIN stock_universe u ON u.id = d1.universe_id
           WHERE d1.trade_date = ? AND d1.universe_id IS NOT NULL
             AND u.industry IS NOT NULL AND d0.close IS NOT NULL AND d0.close > 0 AND d1.close IS NOT NULL''',
        (previous_date, latest_date)
    ).fetchall()

    changes_by_industry = {}
    overall_changes = []
    for row in rows:
        pct_change = (row['latest_close'] - row['prev_close']) / row['prev_close'] * 100
        changes_by_industry.setdefault(row['industry'], []).append(pct_change)
        overall_changes.append(pct_change)

    industries = []
    for industry, changes in changes_by_industry.items():
        if len(changes) < INDUSTRY_GROWTH_MIN_SAMPLE_SIZE:
            continue
        industries.append({
            'industry': industry,
            'avg_change_pct': round(sum(changes) / len(changes), 2),
            'sample_size': len(changes),
        })
    industries.sort(key=lambda i: i['avg_change_pct'], reverse=True)

    # Split strictly by sign, not just "top N vs bottom N" -- with only a
    # handful of qualifying industries (or all of them moving the same
    # direction that day), a plain top-N/bottom-N split could put the very
    # same industries in both lists, or label a mildly positive industry a
    # "loser" just because it's near the bottom of an all-gaining day.
    gainers = [i for i in industries if i['avg_change_pct'] >= 0][:top_n]
    losers = [i for i in reversed(industries) if i['avg_change_pct'] < 0][:top_n]

    overall_avg_change_pct = round(sum(overall_changes) / len(overall_changes), 2) if overall_changes else None

    return {
        'available': True,
        'latest_date': latest_date,
        'previous_date': previous_date,
        'overall_avg_change_pct': overall_avg_change_pct,
        'overall_sample_size': len(overall_changes),
        'gainers': gainers,
        'losers': losers,
    }
