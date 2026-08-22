"""Nari Nakhre Stocks suggestion engine. Filter/scoring logic here is pure
Python (no DB access) so it's directly unit-testable; generate_daily_suggestions()
at the bottom is the only DB-orchestrating piece, mirroring the split already
used elsewhere in this codebase (e.g. fundamental_screen.py vs stock_shortlist.py)."""
import random
from datetime import date, timedelta

from stoqbell.utils.price_pattern import compute_suggestion_pricing
from stoqbell.utils.nns_score import NNS_BRONZE_MIN, compute_nns_score, nns_tier
from stoqbell.utils.stock_shortlist import _compute_industry_benchmarks

# The daily cap -- exactly one stock_suggestions row per day (the "Pick of
# the Day"), not every currently-qualifying candidate. Also the default for
# select_top_suggestions, a general-purpose "top N candidates" helper used
# the same way.
TOP_N_SUGGESTIONS = 1
HOLDING_PERIOD_DAYS = 10
TARGET_MULTIPLIER = 1.05   # +5%, used as the fallback when no chart pattern applies -- see compute_suggestion_pricing
STOP_LOSS_MULTIPLIER = 0.97  # -3%, same fallback role

# How far back a candidate's own last suggestion counts as "too recent to
# repeat" (see _is_genuine_change/generate_daily_suggestions) -- the point
# of the 1-a-day cap is to work through the qualifying list one stock at a
# time rather than fixating on the single top-ranked one every single day,
# so a repeat within this window is skipped in favor of the next-best
# candidate UNLESS the pick has genuinely changed since then. 15 days
# (not 30) -- a stock that already had its turn shouldn't be locked out
# for a full month; two weeks is enough of a gap to keep the daily pick
# from feeling repetitive while still cycling back through the qualifying
# list reasonably often.
SUGGESTION_REPEAT_WINDOW_DAYS = 15
# How much the NNS Score has to move (absolute, 0-10 scale) to count as a
# genuine change worth resending within the repeat window.
NNS_SCORE_CHANGE_THRESHOLD = 1.0
# How much the target sell price has to move (%) to count as a genuine
# change worth resending within the repeat window.
TARGET_PRICE_CHANGE_THRESHOLD_PCT = 3.0

# How far back to pull daily closes for pattern detection (see
# utils.price_pattern.detect_head_and_shoulders/detect_rounding_pattern) --
# both patterns can take months to form, so a short lookback would miss
# them entirely. ~900 calendar days is roughly 2.5 years, comfortably
# covering even the slower-forming rounding-bottom case (published research
# puts typical formation at 3-12 months -- see PATTERN_RESEARCH_CONTEXT).
PATTERN_LOOKBACK_DAYS = 900

RSI_MIN, RSI_MAX = 40, 65

STOCK_SUGGESTIONS_TABLE_SQL = [
    '''CREATE TABLE IF NOT EXISTS stock_suggestions (
        id BIGSERIAL PRIMARY KEY,
        watchlist_id BIGINT NOT NULL REFERENCES stock_watchlist(id),
        suggestion_date DATE NOT NULL,
        buy_price NUMERIC(12,2),
        target_sell_price NUMERIC(12,2),
        stop_loss_price NUMERIC(12,2),
        holding_period_days INTEGER,
        rsi_at_suggestion NUMERIC(6,2),
        pe_at_suggestion NUMERIC(10,2),
        peg_at_suggestion NUMERIC(10,2),
        score NUMERIC(6,4),
        rationale TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(watchlist_id, suggestion_date)
    )'''
]

# Added after stock_suggestions already had data -- separate additive
# migration, same pattern as everywhere else in this codebase.
# fundamental_tier snapshots the watchlist row's golden/silver classification
# (see fundamental_screen.classify_fundamental_tier) at suggestion time, same
# reasoning as pe_at_suggestion/peg_at_suggestion already snapshotting their
# values rather than being looked up live later. opm_at_suggestion is new
# alongside it -- OPM was already used in scoring but never persisted per
# suggestion before, and a silver suggestion's PE/OPM values need to be
# displayable without a live re-fetch.
STOCK_SUGGESTIONS_ALTER_SQL = [
    'ALTER TABLE stock_suggestions ADD COLUMN IF NOT EXISTS opm_at_suggestion NUMERIC(6,2)',
    "ALTER TABLE stock_suggestions ADD COLUMN IF NOT EXISTS fundamental_tier TEXT "
    "CHECK (fundamental_tier IS NULL OR fundamental_tier IN ('golden', 'silver'))",
    # pattern_name/pattern_note: set only when compute_suggestion_pricing()
    # (utils/price_pattern.py) found a confirmed chart pattern to base
    # buy/target/stop-loss on instead of the flat percentage fallback --
    # NULL means the flat fallback was used, same as before this existed.
    # pattern_note is the full cited caveat sentence (hit rate, typical
    # duration range, source) the email/viewer pages show INSTEAD OF a
    # "hold N days" figure for these -- there's deliberately no
    # pattern-specific day-count column: chart-pattern shape doesn't
    # reliably predict timing, so none is stored.
    'ALTER TABLE stock_suggestions ADD COLUMN IF NOT EXISTS pattern_name TEXT',
    'ALTER TABLE stock_suggestions ADD COLUMN IF NOT EXISTS pattern_note TEXT',
    # nns_tier: golden/silver/bronze from the NNS Score (see
    # utils/nns_score.py's compute_nns_score/nns_tier) -- separate from
    # fundamental_tier above, which reflects watchlist MEMBERSHIP
    # eligibility (classify_fundamental_tier's own golden/silver), not this
    # suggestion's overall composite ranking. The existing `score` column
    # (NUMERIC(6,4), plenty of room for 0-10 with one decimal) now holds
    # the NNS Score itself rather than the older 0-1 four-metric score it
    # originally stored -- same column, redefined meaning, since a
    # suggestion only ever needs the one current "the score".
    "ALTER TABLE stock_suggestions ADD COLUMN IF NOT EXISTS nns_tier TEXT "
    "CHECK (nns_tier IS NULL OR nns_tier IN ('golden', 'silver', 'bronze'))",
    # Set only by the intraday (every-5-minutes, market hours only) live
    # target-hit check (see utils/admin_alerts.py's
    # find_and_notify_intraday_target_hits) once it's emailed Raghav that
    # this suggestion reached its target -- deliberately NOT the same as
    # `status` turning 'target_hit', which only the existing once-daily
    # customer-facing check (find_pending_target_hit_suggestions) sets.
    # Keeping these two independent means the intraday check can tell
    # Raghav promptly without ever causing the once-daily customer
    # notification to skip this row as already-handled -- see
    # find_and_notify_intraday_target_hits' own docstring for the full
    # reasoning. NULL means "not yet alerted intraday"; once set, this
    # row is never re-checked/re-emailed by the intraday job again.
    'ALTER TABLE stock_suggestions ADD COLUMN IF NOT EXISTS intraday_alert_sent_at TIMESTAMP',
]


def initialize_stock_suggestions_table_if_needed(client):
    for sql in STOCK_SUGGESTIONS_TABLE_SQL + STOCK_SUGGESTIONS_ALTER_SQL:
        try:
            client.rpc('execute_sql', {'query': sql}).execute()
        except Exception as e:
            print(f'Stock suggestions table init warning (may already exist): {e}')


def passes_hard_filters(candidate):
    """candidate is dict-like with cross_status/volume_trend/rsi_14 keys.
    All three must pass. NOT used to gate suggestion generation anymore
    (see is_suggestion_eligible/generate_daily_suggestions below, and the
    module docstring note on why -- this exact three-way combination
    proved too strict against a ~40-company watchlist and produced zero
    suggestions for days on end) -- kept only for the "Recommended to buy"
    badge on /stocks/watchlist (see enrich_and_sort_watchlist_rows), a
    stricter, purely informational label distinct from what actually gets
    sent as the Pick of the Day."""
    return (
        candidate.get('cross_status') == 'golden_cross'
        and candidate.get('volume_trend') == 'confirming'
        and candidate.get('rsi_14') is not None
        and RSI_MIN <= candidate['rsi_14'] <= RSI_MAX
    )


def is_suggestion_eligible(candidate):
    """The actual gate for suggestion generation (see generate_daily_suggestions/
    select_top_suggestions) -- golden cross is required, full stop, but
    that's it: no separate volume-trend or RSI hard cutoff. RSI still
    matters, just as one of the NNS Score's own ten sub-scores (see
    utils.nns_score.compute_nns_score's rsi_position) rather than an
    all-or-nothing gate -- a candidate with poor RSI scores lower and
    ranks behind better ones, it doesn't get excluded outright over it
    alone. Quality is enforced afterward by score_candidates' own
    NNS_BRONZE_MIN floor, not here."""
    return candidate.get('cross_status') == 'golden_cross'


def score_candidates(candidates, previous_snapshots_by_watchlist=None, industry_benchmarks_by_industry=None):
    """Scores each candidate with its NNS Score (see
    utils.nns_score.compute_nns_score -- 0-10, one decimal, ten
    equally-weighted quantified sub-scores). previous_snapshots_by_watchlist
    ({watchlist_id: previous stock_fundamentals row}) and
    industry_benchmarks_by_industry ({industry: {'pe_ratio','price_to_book'}})
    are optional batched lookups a caller with DB access can supply (see
    generate_daily_suggestions) -- omitted (the common case in a plain unit
    test), every candidate's holding-trend sub-score is 0 and PE/price-to-book
    fall back to the flat bands, same as compute_nns_score's own defaults.

    Returns [(candidate, score), ...] sorted highest score first,
    EXCLUDING anything that doesn't reach at least NNS_BRONZE_MIN --
    being golden-cross gets a candidate INTO consideration here, not
    automatically a suggestion (see is_suggestion_eligible / nns_tier).
    Does NOT apply is_suggestion_eligible itself -- callers are expected
    to filter first (see select_top_suggestions)."""
    previous_snapshots_by_watchlist = previous_snapshots_by_watchlist or {}
    industry_benchmarks_by_industry = industry_benchmarks_by_industry or {}

    scored = []
    for candidate in candidates:
        previous = previous_snapshots_by_watchlist.get(candidate.get('watchlist_id'))
        benchmarks = industry_benchmarks_by_industry.get(candidate.get('industry'))
        score, _breakdown = compute_nns_score(candidate, previous, benchmarks)
        if score < NNS_BRONZE_MIN:
            continue
        scored.append((candidate, score))

    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored


def select_top_suggestions(candidates, top_n=TOP_N_SUGGESTIONS, previous_snapshots_by_watchlist=None,
                            industry_benchmarks_by_industry=None):
    """Filters candidates to golden-cross ones (see is_suggestion_eligible),
    scores them (see score_candidates), and returns the top_n as
    [(candidate, score), ...], highest first -- since score_candidates
    sorts strictly by score descending and a silver-or-better score (>=
    NNS_SILVER_MIN) always outranks any bronze one, this naturally prefers
    silver+ candidates and only ever falls through to a bronze one when no
    silver+ candidate exists at all, with no separate two-pass logic
    needed for that. Returns fewer than top_n (even zero) if fewer
    candidates are golden-cross or clear the NNS_BRONZE_MIN scoring floor
    -- never pads with a non-golden-cross or below-bronze candidate just
    to reach a count."""
    eligible = [c for c in candidates if is_suggestion_eligible(c)]
    if not eligible:
        return []
    return score_candidates(eligible, previous_snapshots_by_watchlist, industry_benchmarks_by_industry)[:top_n]


def _build_rationale(candidate, tier=None):
    parts = ['Golden cross']
    if candidate.get('volume_trend') == 'confirming':
        parts.append('confirming volume')
    if candidate.get('peg_ratio') is not None:
        parts.append(f"PEG {candidate['peg_ratio']:.2f}")
    if candidate.get('opm_pct') is not None:
        parts.append(f"OPM {candidate['opm_pct']:.0f}%")
    rationale = ', '.join(parts)
    if tier == 'bronze':
        # Deliberately doesn't say "NNS Score" or a number -- customer-
        # facing text (email/viewer pages) explains strength in plain
        # language, not by naming the internal scoring mechanism.
        rationale += (
            '. Weaker overall profile than our usual highly-recommended picks, included only because '
            'nothing stronger cleared the bar today -- extra caution advised; research this one more '
            'closely than usual before acting.'
        )
    return rationale


def _is_genuine_change(existing, new_score, new_target_price):
    """existing: {'score', 'target_sell_price', 'pattern_name'} -- the most
    recent stock_suggestions row for this watchlist_id within
    SUGGESTION_REPEAT_WINDOW_DAYS. Returns True if today's pick differs
    enough from that one to be worth resending despite the cooldown:
    either the NNS Score moved by at least NNS_SCORE_CHANGE_THRESHOLD, the
    target price moved by at least TARGET_PRICE_CHANGE_THRESHOLD_PCT, or
    the pattern basis itself changed (gaining or losing a confirmed chart
    pattern is a genuinely different reason to buy even if the score
    happens to land close to where it was). False means "same pick as
    last time, nothing meaningfully new to say" -- generate_daily_suggestions
    skips it and tries the next-best candidate instead."""
    old_score = existing.get('score')
    if old_score is not None and abs(new_score - old_score) >= NNS_SCORE_CHANGE_THRESHOLD:
        return True
    old_target = existing.get('target_sell_price')
    if old_target and new_target_price is not None:
        pct_change = abs(new_target_price - old_target) / old_target * 100
        if pct_change >= TARGET_PRICE_CHANGE_THRESHOLD_PCT:
            return True
    return False


# Multi-horizon projected price (1 month/6 months/1 year) moved to
# utils/price_pattern.py's compute_projection_targets -- it needs
# PATTERN_RESEARCH_CONTEXT and pattern_name to ground the projection in the
# specific chart pattern that drove this suggestion's own target_sell_price
# (when there is one), which live in that module, not this one.


_PATTERN_LABELS = {
    'head_and_shoulders_bottom': 'a reverse head-and-shoulders pattern (confirmed breakout)',
    'rounding_bottom': 'a rounding-bottom pattern (confirmed breakout)',
}


def _build_pattern_note(pattern_name, pattern_research):
    """The sentence shown INSTEAD OF a 'hold N days' figure whenever
    compute_suggestion_pricing() (utils/price_pattern.py) used a confirmed
    chart pattern for this suggestion's target/stop-loss -- cites the
    pattern's published hit rate and typical duration (general research on
    the pattern TYPE, from PATTERN_RESEARCH_CONTEXT), explicitly stating
    this isn't a per-stock timing prediction. Returns None when no pattern
    was used (pattern_name/pattern_research both None) -- callers fall
    back to the plain holding_period_days figure in that case."""
    if not pattern_name or not pattern_research:
        return None

    label = _PATTERN_LABELS.get(pattern_name, pattern_name)
    lo_days, hi_days = pattern_research['typical_move_duration_days']
    duration = f'{round(lo_days / 30)}-{round(hi_days / 30)} months'

    hit_rate = pattern_research.get('target_hit_rate_pct')
    directional_rate = pattern_research.get('directional_hit_rate_pct')
    if hit_rate is not None:
        reliability = f'the full target has historically been reached about {hit_rate}% of the time'
    elif directional_rate is not None:
        reliability = f'price has historically continued in the predicted direction about {directional_rate}% of the time'
    else:
        reliability = 'published reliability figures for this exact pattern are limited'

    return (
        f'Target and stop-loss are based on {label} in this stock\'s own price history -- the target uses the '
        f'standard measured-move formula (the head-to-neckline distance projected past the breakout). '
        f'Per published technical-analysis research ({pattern_research["source"]}), {reliability}, with the full '
        f'move typically taking about {duration} historically -- across many past instances at OTHER companies, '
        f'not a prediction for this stock specifically. There is no reliable way to predict exact timing from '
        f'chart shape alone.'
    )


def _fetch_price_history(db, watchlist_id, lookback_days=PATTERN_LOOKBACK_DAYS):
    """Closing prices, oldest first, for pattern detection (see
    utils.price_pattern.compute_suggestion_pricing) -- capped at
    lookback_days most recent rows regardless of how much history exists."""
    rows = db.execute(
        '''SELECT close FROM stock_daily_data WHERE watchlist_id=?
           ORDER BY trade_date DESC LIMIT ?''',
        (watchlist_id, lookback_days)
    ).fetchall()
    closes_desc = [r['close'] for r in rows if r['close'] is not None]
    return list(reversed(closes_desc))


def _fetch_candidates(db, market_cap_tier=None):
    """market_cap_tier=None (every existing caller today) fetches the same
    mixed mid-cap/large-cap pool as always -- passing 'large_cap' or
    'mid_cap' additionally restricts to stock_watchlist rows tagged that
    tier (see utils.stock_shortlist.run_large_cap_shortlist/
    run_fundamental_shortlist), for utils.large_cap_engine's twice-weekly
    bonus pick, which must only ever draw from the large-cap tier. Adding
    this as an optional filter (rather than a separate query) keeps this
    the single shared candidate-fetch path for every engine."""
    today = date.today().isoformat()
    fundamentals_cutoff = (date.today() - timedelta(days=20)).isoformat()
    tier_clause = ' AND w.market_cap_tier = ?' if market_cap_tier else ''
    params = (today, fundamentals_cutoff) + ((market_cap_tier,) if market_cap_tier else ())
    return db.execute(
        f'''SELECT w.id AS watchlist_id, w.symbol, w.exchange, w.name AS company_name,
                  w.fundamental_tier, w.market_cap_tier,
                  u.id AS universe_id, u.industry,
                  i.rsi_14, i.cross_status, i.volume_trend,
                  f.pe_ratio, f.peg_ratio, f.opm_pct, f.roce_pct, f.roa_pct,
                  f.quarterly_profit_growth_pct, f.quarterly_revenue_growth_pct,
                  f.price_to_book, f.promoter_holding_pct, f.fii_holding_pct, f.snapshot_date,
                  d.close AS latest_close
           FROM stock_watchlist w
           LEFT JOIN stock_universe u ON u.symbol = w.symbol AND u.exchange = w.exchange
           JOIN stock_indicators i ON i.watchlist_id = w.id AND i.calc_date = ?
           JOIN stock_fundamentals f ON f.watchlist_id = w.id
               AND f.snapshot_date = (
                   SELECT MAX(f2.snapshot_date) FROM stock_fundamentals f2
                   WHERE f2.watchlist_id = w.id AND f2.snapshot_date >= ?
               )
           JOIN stock_daily_data d ON d.watchlist_id = w.id
               AND d.trade_date = (
                   SELECT MAX(d2.trade_date) FROM stock_daily_data d2 WHERE d2.watchlist_id = w.id
               )
           WHERE w.is_active = 1{tier_clause}''',
        params
    ).fetchall()


def _fetch_previous_snapshots(db, candidates):
    """{watchlist_id: previous stock_fundamentals row} for NNS Score's
    promoter/FII holding-trend sub-score (see utils.nns_score.compute_nns_score)
    -- one batched query covering every candidate's universe_id at once,
    not a query per candidate (same N+1-avoidance reasoning, and the exact
    same "first snapshot strictly older than the current one" matching
    logic, as stock_shortlist.py's own previous-snapshot batching -- see
    that module's docstring for the live incident that pattern was built
    to fix). This candidate pool is much smaller (already hard-filtered),
    but there's no reason to reintroduce a per-row query here either.
    Candidates with no universe_id (LEFT JOIN didn't match) OR no
    snapshot_date of their own (no stock_fundamentals row synced yet at
    all -- possible here since /stocks/watchlist's own query, unlike
    _fetch_candidates' INNER JOIN, LEFT JOINs fundamentals so a
    just-added, not-yet-synced company still shows up with nulls rather
    than being hidden entirely) are skipped -- nothing to compare a
    "previous" snapshot against either way, holding_trend just scores 0
    for them."""
    universe_ids = [c['universe_id'] for c in candidates if c.get('universe_id') is not None]
    if not universe_ids:
        return {}
    ids_sql = ','.join(str(int(uid)) for uid in universe_ids)
    all_snapshots = db.execute(
        f'''SELECT universe_id, promoter_holding_pct, fii_holding_pct, snapshot_date
            FROM stock_fundamentals
            WHERE universe_id IN ({ids_sql})
            ORDER BY universe_id, snapshot_date DESC'''
    ).fetchall()
    snapshots_by_universe = {}
    for snap in all_snapshots:
        snapshots_by_universe.setdefault(snap['universe_id'], []).append(snap)

    previous_by_watchlist = {}
    for candidate in candidates:
        universe_id = candidate.get('universe_id')
        if universe_id is None or candidate.get('snapshot_date') is None:
            continue
        for snap in snapshots_by_universe.get(universe_id, []):
            if snap['snapshot_date'] < candidate['snapshot_date']:
                previous_by_watchlist[candidate['watchlist_id']] = snap
                break
    return previous_by_watchlist


def compute_watchlist_nns_scores(db, watchlist_rows):
    """Annotates every row from /stocks/watchlist's own query with
    nns_score/nns_tier, using the exact same compute_nns_score the
    suggestion engine itself scores candidates with -- but for EVERY
    watchlist row, not just the ones that also cleared passes_hard_filters
    (see generate_daily_suggestions/score_candidates, which only ever
    scores hard-filter-passers). This is what lets staff see how strong a
    company looks right now even on a day it wasn't -- and couldn't have
    been -- sent as the Pick of the Day.

    Each row must already carry universe_id, industry, snapshot_date and
    the fundamentals/indicators columns compute_nns_score reads (see
    app.py's stocks_watchlist route for the exact SELECT) -- same shape
    _fetch_candidates below produces, since this reuses the same
    industry-benchmark and previous-snapshot batching those candidates get.
    Returns NEW dicts (does not mutate the input rows); a row with missing
    data still gets a score (compute_nns_score treats missing fields as
    failing that sub-score, same as everywhere else it's used)."""
    rows = [dict(r) for r in watchlist_rows]
    for row in rows:
        row.setdefault('watchlist_id', row.get('id'))
    industry_benchmarks = _compute_industry_benchmarks(rows)
    previous_snapshots = _fetch_previous_snapshots(db, rows)
    for row in rows:
        previous = previous_snapshots.get(row.get('watchlist_id'))
        benchmarks = industry_benchmarks.get(row.get('industry'))
        score, _breakdown = compute_nns_score(row, previous, benchmarks)
        row['nns_score'] = score
        row['nns_tier'] = nns_tier(score)
    return rows


def get_top_stocks(db, limit=5):
    """Viewer-safe 'quality stocks' data (see app.py's /stocks/quality-stocks)
    -- reuses the exact same candidate pool and NNS scoring as the daily
    suggestion engine (_fetch_candidates/compute_watchlist_nns_scores), but
    returns only each stock's tier badge, never the raw NNS Score number --
    same "viewers see is_recommended/tier, not the score itself" policy
    compute_watchlist_nns_scores' own docstring already describes for the
    staff-only /stocks/watchlist page.

    Golden/silver tier only (bronze is "included only because nothing
    stronger cleared the bar today" territory in the daily engine -- not a
    genuine highlight worth surfacing here), sorted highest-scored first.
    limit=5 (the default) caps the result -- pass limit=None for the full,
    uncapped list (relies on Python's list[:None] slicing meaning "no
    bound", not a special case here) which is what
    /stocks/quality-stocks' full browse page uses. Returns [] on a day
    nothing silver-or-better is currently in the watchlist -- expected
    some days, not an error."""
    candidates = _fetch_candidates(db)
    scored_rows = compute_watchlist_nns_scores(db, candidates)
    qualifying = [r for r in scored_rows if r.get('nns_tier') in ('golden', 'silver')]
    qualifying.sort(key=lambda r: r['nns_score'], reverse=True)
    return [
        {
            'symbol': r['symbol'], 'exchange': r['exchange'],
            'company_name': r.get('company_name') or r['symbol'],
            'nns_tier': r['nns_tier'], 'latest_close': r.get('latest_close'),
        }
        for r in qualifying[:limit]
    ]


def get_suggestions(db, start_date=None, end_date=None):
    """Fetches stock_suggestions rows (symbol/exchange joined from
    stock_watchlist), most recent first, optionally bounded by
    suggestion_date on either end (either or both may be omitted).

    DISTINCT ON (s.suggestion_date) -- combined with ORDER BY
    s.suggestion_date DESC, s.score DESC -- collapses each day down to its
    single highest-scored row, no matter how many rows actually exist for
    that date in the table. This is the real, robust guarantee behind
    "one Pick of the Day": generate_daily_suggestions only ever INSERTS
    one new row per call, but nothing stops the underlying job from being
    triggered more than once on the same day (a manual re-run, an admin
    testing a button, a retried cron) -- if the top-ranked candidate
    happened to differ between those calls, each call's own top pick gets
    its own row (ON CONFLICT only dedupes the SAME watchlist_id), leaving
    more than one row for that date sitting in the table. Filtering here,
    at the single shared read path every caller goes through (the daily
    email, /stocks/suggestions/resend, and both viewer pages), is what
    actually prevents more than one from ever being shown or sent for a
    given day, regardless of how many accumulated upstream.

    universe_id (LEFT JOINed by symbol/exchange, same match as
    _fetch_candidates -- may be None if the company was ever removed from
    stock_universe) is what suggestion_email.py links out to
    /stocks/universe/<universe_id> with -- the general-audience company
    detail page (fundamentals/technicals, no Recommended flag), distinct
    from /stocks/company/<watchlist_id> (linked from /stocks/watchlist),
    which shows staff the Recommended column too. Both are open to any
    logged-in Stocks account now (see stocks_watchlist_access_required) --
    the difference between them is what each page's template shows, not
    who can reach it. Shared by the daily email (utils/suggestion_email.py)
    and the read-only viewer pages (/stocks/my/suggestions,
    /stocks/my/history) so there's exactly one place building this join,
    not three."""
    conditions = []
    params = []
    if start_date:
        conditions.append('s.suggestion_date >= ?')
        params.append(start_date)
    if end_date:
        conditions.append('s.suggestion_date <= ?')
        params.append(end_date)
    where_clause = ('WHERE ' + ' AND '.join(conditions)) if conditions else ''

    return db.execute(
        f'''SELECT DISTINCT ON (s.suggestion_date)
                   s.id AS suggestion_id,
                   w.id AS watchlist_id, w.symbol, w.exchange, w.name AS company_name,
                   u.id AS universe_id,
                   s.suggestion_date, s.buy_price,
                   s.target_sell_price, s.stop_loss_price, s.holding_period_days,
                   s.rsi_at_suggestion, s.pe_at_suggestion, s.peg_at_suggestion,
                   s.opm_at_suggestion, s.fundamental_tier,
                   s.pattern_name, s.pattern_note,
                   s.score AS nns_score, s.nns_tier, s.rationale, s.status
            FROM stock_suggestions s
            JOIN stock_watchlist w ON w.id = s.watchlist_id
            LEFT JOIN stock_universe u ON u.symbol = w.symbol AND u.exchange = w.exchange
            {where_clause}
            ORDER BY s.suggestion_date DESC, s.score DESC''',
        tuple(params)
    ).fetchall()


def get_suggestion_by_id(db, suggestion_id):
    """Single stock_suggestions row, for the manual-buy confirmation page
    and route (/stocks/suggestions/<id>/buy) -- same join/columns as
    get_suggestions plus s.id AS suggestion_id (which get_suggestions
    itself doesn't need, since it's never used to open a trade against).
    Unlike get_suggestions, this deliberately does NOT collapse via
    DISTINCT ON -- a manual buy targets one specific already-sent
    suggestion row by its own id, not "today's pick" in the abstract.
    Returns None if no such row exists."""
    return db.execute(
        '''SELECT s.id AS suggestion_id, w.id AS watchlist_id, w.symbol, w.exchange, w.name AS company_name,
                  u.id AS universe_id,
                  s.suggestion_date, s.buy_price,
                  s.target_sell_price, s.stop_loss_price, s.holding_period_days,
                  s.rsi_at_suggestion, s.pe_at_suggestion, s.peg_at_suggestion,
                  s.opm_at_suggestion, s.fundamental_tier,
                  s.pattern_name, s.pattern_note,
                  s.score AS nns_score, s.nns_tier, s.rationale, s.status
           FROM stock_suggestions s
           JOIN stock_watchlist w ON w.id = s.watchlist_id
           LEFT JOIN stock_universe u ON u.symbol = w.symbol AND u.exchange = w.exchange
           WHERE s.id = ?''',
        (suggestion_id,)
    ).fetchone()


def compute_tracker_row_stats(buy_price, target_sell_price, stop_loss_price, latest_price, suggestion_date, today=None, target_hit_date=None):
    """Pure per-row math for the recommendation tracker (see
    get_recommendation_tracker and app.py's /stocks/recommendations/tracker)
    -- kept separate from the DB query so the profit/loss and outcome
    arithmetic is unit-testable without a database. suggestion_date/today/
    target_hit_date are date objects (or ISO strings, which get parsed) --
    today defaults to date.today(). Returns {'days_elapsed', 'pct_change',
    'outcome', 'target_hit_date', 'days_to_target_hit'}:
      - days_elapsed: whole days since the suggestion, >= 0 (never negative
        -- a suggestion is never "in the future" relative to today).
      - pct_change: (latest - buy) / buy * 100, rounded to 1 decimal, or
        None if buy_price or latest_price is missing (nothing to divide).
      - outcome: 'target_hit' if latest_price has reached target_sell_price,
        'stop_loss_hit' if it's dropped to/through stop_loss_price (checked
        AFTER target, so a pattern-based suggestion with an unusually tight
        stop that also happens to sit at/above target reads as the good
        outcome, not the bad one), else 'open'. 'unknown' if latest_price
        is missing entirely (nothing synced yet).
      - target_hit_date/days_to_target_hit: target_hit_date is the first
        trade date (from get_recommendation_tracker's own correlated
        subquery, not this function) on/after suggestion_date whose close
        reached target_sell_price -- passed straight through as a date
        object (parsed if given as a string). days_to_target_hit is the
        whole-day gap from suggestion_date to it, or None if no hit date
        was given (never hit, or outcome isn't currently 'target_hit')."""
    if isinstance(suggestion_date, str):
        suggestion_date = date.fromisoformat(suggestion_date[:10])
    today = today or date.today()
    days_elapsed = max(0, (today - suggestion_date).days)

    pct_change = None
    if buy_price and latest_price is not None:
        pct_change = round((latest_price - buy_price) / buy_price * 100, 1)

    if latest_price is None:
        outcome = 'unknown'
    elif target_sell_price is not None and latest_price >= target_sell_price:
        outcome = 'target_hit'
    elif stop_loss_price is not None and latest_price <= stop_loss_price:
        outcome = 'stop_loss_hit'
    else:
        outcome = 'open'

    days_to_target_hit = None
    if isinstance(target_hit_date, str):
        target_hit_date = date.fromisoformat(target_hit_date[:10])
    if target_hit_date is not None:
        days_to_target_hit = max(0, (target_hit_date - suggestion_date).days)

    return {
        'days_elapsed': days_elapsed, 'pct_change': pct_change, 'outcome': outcome,
        'target_hit_date': target_hit_date, 'days_to_target_hit': days_to_target_hit,
    }


def get_recommendation_tracker(db):
    """Every stock_suggestions row ever sent, newest first, joined to its
    current price -- the super_admin/child_admin "recommendation tracker"
    view (app.py's /stocks/recommendations/tracker). Unlike get_suggestions
    above (bounded to a recent window for the viewer-facing pages), this is
    deliberately all-time and unfiltered -- it's meant to answer "how did
    every pick we've ever sent actually do", not just the recent ones.
    latest_price/price_date come from whichever of watchlist_id or
    universe_id the price row is actually keyed by (see the dual-identity
    pattern noted throughout this codebase -- a company that's both
    watchlisted and in the universe has ONE stock_daily_data row keyed by
    watchlist_id with universe_id also stamped, so joining on watchlist_id
    alone is sufficient here since every suggestion candidate is by
    definition a watchlist row).

    holding_period_days/pattern_note (added alongside the rest -- same
    "timing" pair _timing_text in utils/suggestion_email.py already reads
    off a stock_suggestions row) are what let a caller show how long a
    pick is expected to take, e.g. the auto-trader dashboard's own
    buy-list of recent recommendations.

    target_hit_date is the first trade_date on/after suggestion_date whose
    close reached target_sell_price -- computed fresh from stock_daily_data's
    full history every time (like latest_price above), not from
    stock_suggestions.status/find_pending_target_hit_suggestions' once-daily
    'target_hit' marking, so it stays in sync with this same query's own
    live-recomputed 'outcome' (see compute_tracker_row_stats) instead of
    disagreeing with it if a price later drops back below target after a
    transient hit."""
    return db.execute(
        '''SELECT s.id, w.id AS watchlist_id, w.symbol, w.exchange, s.suggestion_date,
                  s.buy_price, s.target_sell_price, s.stop_loss_price, s.status,
                  s.nns_tier, s.score AS nns_score, s.pattern_name, s.pattern_note,
                  s.holding_period_days, s.rationale,
                  d.close AS latest_price, d.trade_date AS price_date,
                  (SELECT MIN(d3.trade_date) FROM stock_daily_data d3
                    WHERE d3.watchlist_id = w.id AND d3.trade_date >= s.suggestion_date
                      AND s.target_sell_price IS NOT NULL AND d3.close >= s.target_sell_price
                  ) AS target_hit_date
           FROM stock_suggestions s
           JOIN stock_watchlist w ON w.id = s.watchlist_id
           LEFT JOIN stock_daily_data d ON d.watchlist_id = w.id
               AND d.trade_date = (
                   SELECT MAX(d2.trade_date) FROM stock_daily_data d2 WHERE d2.watchlist_id = w.id
               )
           ORDER BY s.suggestion_date DESC, s.id DESC'''
    ).fetchall()


def find_pending_target_hit_suggestions(db):
    """Pending (status='pending') stock_suggestions rows whose
    target_sell_price has been reached at the latest synced close --
    checked once a day against the daily close, same "not intraday"
    caveat as everywhere else in this codebase that compares a price
    level against stock_daily_data (see e.g. utils.auto_trader.determine_exit).
    This is what actually gives stock_suggestions.status a real meaning
    for the first time -- every row previously stayed 'pending' forever
    (see get_recommendation_tracker's note on this) since nothing ever
    moved it. Only status='pending' rows are considered, so a row already
    marked 'target_hit' by a previous run of this same check (see
    mark_suggestions_target_hit) is never re-detected/re-notified about.

    Returns a list of dicts: id, watchlist_id, symbol, exchange,
    company_name, suggestion_date, buy_price, target_sell_price,
    latest_price, latest_price_date -- everything
    send_target_achieved_email needs to build one subscriber-facing
    achievement entry, sorted oldest suggestion first."""
    return db.execute(
        '''SELECT s.id, w.id AS watchlist_id, w.symbol, w.exchange, w.name AS company_name,
                  s.suggestion_date, s.buy_price, s.target_sell_price,
                  d.close AS latest_price, d.trade_date AS latest_price_date
           FROM stock_suggestions s
           JOIN stock_watchlist w ON w.id = s.watchlist_id
           JOIN stock_daily_data d ON d.watchlist_id = w.id
               AND d.trade_date = (
                   SELECT MAX(d2.trade_date) FROM stock_daily_data d2 WHERE d2.watchlist_id = w.id
               )
           WHERE s.status = 'pending'
             AND s.target_sell_price IS NOT NULL
             AND d.close >= s.target_sell_price
           ORDER BY s.suggestion_date ASC, s.id ASC'''
    ).fetchall()


def mark_suggestions_target_hit(db, suggestion_ids):
    """Sets status='target_hit' for the given stock_suggestions ids --
    the other half of find_pending_target_hit_suggestions, called once
    those suggestions have actually been emailed about (see app.py's
    /stocks/suggestions/notify-target-hits) so the same target hit is
    never notified about twice. No-op on an empty list -- callers don't
    need to guard against that themselves."""
    suggestion_ids = list(suggestion_ids)
    if not suggestion_ids:
        return
    placeholders = ','.join('?' * len(suggestion_ids))
    db.execute(
        f"UPDATE stock_suggestions SET status='target_hit' WHERE id IN ({placeholders})",
        tuple(suggestion_ids)
    )
    db.commit()


def _rank_todays_candidates(db, market_cap_tier=None):
    """Fetches today's candidate pool and scores it -- shared by
    generate_daily_suggestions below, utils.starters_engine.generate_weekly_starters_pick,
    and utils.large_cap_engine.generate_large_cap_bonus_pick, all of which
    reuse the exact same universe/scoring and only differ in what they do
    with the ranked list afterward (a stricter golden-tier-only bar and
    weekly cadence for Starters; a market_cap_tier='large_cap' restriction
    and twice-weekly cadence for the bonus pick). market_cap_tier=None (the
    daily/Starters case) scores the full mixed pool exactly as before this
    param existed. Returns [(candidate, score), ...] sorted highest score
    first, already excluding anything that doesn't clear NNS_BRONZE_MIN or
    isn't golden-cross -- see is_suggestion_eligible/score_candidates."""
    candidates = _fetch_candidates(db, market_cap_tier=market_cap_tier)
    previous_snapshots_by_watchlist = _fetch_previous_snapshots(db, candidates)
    industry_benchmarks_by_industry = _compute_industry_benchmarks(candidates)
    eligible = [c for c in candidates if is_suggestion_eligible(c)]
    return score_candidates(eligible, previous_snapshots_by_watchlist, industry_benchmarks_by_industry)


def generate_daily_suggestions(db):
    """Builds the candidate pool (active watchlist joined to today's
    indicators and each symbol's latest fundamentals snapshot within 20
    days), narrows to golden-cross candidates and ranks them by NNS Score
    (see is_suggestion_eligible/score_candidates, utils.nns_score.compute_nns_score),
    and works down that ranked list looking for the single "Pick of the
    Day" (TOP_N_SUGGESTIONS -- see its definition) to insert as a
    stock_suggestions row. Because score_candidates sorts strictly by
    score descending, this naturally tries every silver-or-better
    candidate (NNS_SILVER_MIN, 6.0+) before ever falling through to a
    bronze one (4.0-6.0) -- a bronze pick only ever goes out when nothing
    silver+ is both golden-cross and off cooldown that day, and gets an
    explicit caution note in its rationale (see _build_rationale) rather
    than being presented the same as a stronger pick.

    Exactly ONE stock_suggestions row goes out per day -- this is a single
    daily pick, not a full digest of everything currently qualifying. To
    avoid fixating on the same top-ranked stock every single day when it
    keeps qualifying, a candidate whose own last suggestion falls within
    SUGGESTION_REPEAT_WINDOW_DAYS is skipped in favor of the next-best
    candidate UNLESS the pick has genuinely changed since then (see
    _is_genuine_change) -- this is what "gives the list out one at a time"
    rather than repeating the single best stock indefinitely: the pool of
    currently-qualifying candidates naturally rotates through as each gets
    its turn and then cools down. A day can end up with zero rows if every
    golden-cross candidate that clears NNS_BRONZE_MIN is on cooldown (or
    none clear the bar at all).

    buy/target/stop-loss come from compute_suggestion_pricing()
    (utils/price_pattern.py), which prefers a confirmed chart pattern
    (reverse head-and-shoulders, then rounding bottom) found in the last
    PATTERN_LOOKBACK_DAYS of this stock's own price history, falling back
    to the flat TARGET_MULTIPLIER/STOP_LOSS_MULTIPLIER percentages when no
    pattern applies. holding_period_days stays the flat HOLDING_PERIOD_DAYS
    default in EVERY row regardless (it's only ever used as a fallback
    display value) -- when a pattern was used, pattern_name/pattern_note
    are set instead, and it's pattern_note (not holding_period_days) that
    the email/viewer pages show for those, since chart-pattern shape
    doesn't reliably predict timing the way a fixed number of days would
    misleadingly imply."""
    ranked = _rank_todays_candidates(db)

    today_date = date.today()
    today = today_date.isoformat()
    repeat_window_cutoff = (today_date - timedelta(days=SUGGESTION_REPEAT_WINDOW_DAYS)).isoformat()

    created = []
    skipped_duplicates = []

    for candidate, nns_score in ranked:
        if len(created) >= TOP_N_SUGGESTIONS:
            break

        created_row, skipped_row = _create_or_update_suggestion(
            db, candidate, nns_score, today, repeat_window_cutoff
        )
        if created_row:
            created.append(created_row)
        else:
            skipped_duplicates.append(skipped_row)

    return {
        # Candidates that cleared golden-cross + the NNS_BRONZE_MIN scoring
        # floor and got ranked this run (see _rank_todays_candidates) --
        # not the full raw watchlist pool, which score_candidates already
        # filters out before this function ever sees it.
        'candidates_evaluated': len(ranked),
        'created': created,
        'skipped_duplicates': skipped_duplicates,
    }


def _create_or_update_suggestion(db, candidate, nns_score, today, repeat_window_cutoff):
    """Shared per-candidate row-creation logic, extracted from
    generate_daily_suggestions's loop so get_candidates_for_manual_pick/
    create_manual_suggestions (the notifications-page manual picker) can
    create a stock_suggestions row for an admin-CHOSEN candidate the exact
    same way the automatic daily job creates one for its algorithmically-
    top-ranked candidate -- same pricing computation, same cooldown/
    duplicate check, same upsert. today/repeat_window_cutoff are ISO date
    strings (today = date.today().isoformat()).

    Returns (created_dict, None) on success, or (None, skipped_dict) if this
    candidate's own last suggestion is within the repeat window and isn't a
    genuine change (see _is_genuine_change) -- the caller decides what to do
    with a skip (generate_daily_suggestions moves on to the next-ranked
    candidate; the manual picker instead reports it so the admin knows why
    a chosen stock didn't go out)."""
    watchlist_id = candidate['watchlist_id']

    price_history = _fetch_price_history(db, watchlist_id)
    pricing = compute_suggestion_pricing(
        price_history, candidate['latest_close'], TARGET_MULTIPLIER, STOP_LOSS_MULTIPLIER
    )

    buy_price = pricing['buy_price']
    target_sell_price = pricing['target_sell_price']
    stop_loss_price = pricing['stop_loss_price']
    pattern_name = pricing['pattern_name']

    existing_recent = db.execute(
        '''SELECT score, target_sell_price, pattern_name FROM stock_suggestions
           WHERE watchlist_id=? AND suggestion_date >= ? AND suggestion_date < ?
           ORDER BY suggestion_date DESC LIMIT 1''',
        (watchlist_id, repeat_window_cutoff, today)
    ).fetchone()
    if existing_recent and not _is_genuine_change(existing_recent, nns_score, target_sell_price):
        return None, {
            'watchlist_id': watchlist_id, 'symbol': candidate['symbol'], 'exchange': candidate['exchange'],
        }

    pattern_note = _build_pattern_note(pattern_name, pricing['pattern_research'])
    tier = nns_tier(nns_score)
    rationale = _build_rationale(candidate, tier)

    db.execute(
        '''INSERT INTO stock_suggestions
               (watchlist_id, suggestion_date, buy_price, target_sell_price,
                stop_loss_price, holding_period_days, rsi_at_suggestion,
                pe_at_suggestion, peg_at_suggestion, opm_at_suggestion,
                fundamental_tier, pattern_name, pattern_note, score, nns_tier, rationale, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
           ON CONFLICT (watchlist_id, suggestion_date) DO UPDATE SET
               buy_price = EXCLUDED.buy_price,
               target_sell_price = EXCLUDED.target_sell_price,
               stop_loss_price = EXCLUDED.stop_loss_price,
               rsi_at_suggestion = EXCLUDED.rsi_at_suggestion,
               pe_at_suggestion = EXCLUDED.pe_at_suggestion,
               peg_at_suggestion = EXCLUDED.peg_at_suggestion,
               opm_at_suggestion = EXCLUDED.opm_at_suggestion,
               fundamental_tier = EXCLUDED.fundamental_tier,
               pattern_name = EXCLUDED.pattern_name,
               pattern_note = EXCLUDED.pattern_note,
               score = EXCLUDED.score,
               nns_tier = EXCLUDED.nns_tier,
               rationale = EXCLUDED.rationale''',
        (watchlist_id, today, buy_price, target_sell_price, stop_loss_price,
         HOLDING_PERIOD_DAYS, candidate['rsi_14'], candidate['pe_ratio'],
         candidate['peg_ratio'], candidate['opm_pct'], candidate.get('fundamental_tier'),
         pattern_name, pattern_note, nns_score, tier, rationale)
    )
    db.commit()
    suggestion_row = db.execute(
        'SELECT id FROM stock_suggestions WHERE watchlist_id=? AND suggestion_date=?',
        (watchlist_id, today)
    ).fetchone()
    return {
        'suggestion_id': suggestion_row['id'] if suggestion_row else None,
        'watchlist_id': watchlist_id, 'symbol': candidate['symbol'], 'exchange': candidate['exchange'],
        'buy_price': buy_price, 'target_sell_price': target_sell_price, 'stop_loss_price': stop_loss_price,
        'nns_score': nns_score, 'nns_tier': tier, 'pattern_name': pattern_name,
    }, None


def _todays_eligible_candidates_off_cooldown(db):
    """Shared by get_candidates_for_manual_pick/create_manual_suggestions:
    today's ranked, golden-cross-eligible candidates (see
    _rank_todays_candidates), minus any currently on cooldown (the same
    repeat-window/genuine-change check _create_or_update_suggestion applies)
    -- so the manual picker never offers, or tries to create a row for, a
    stock that would just get silently skipped as a duplicate anyway.
    Returns the filtered [(candidate, nns_score), ...] list, still sorted by
    score descending."""
    ranked = _rank_todays_candidates(db)
    today_date = date.today()
    today = today_date.isoformat()
    repeat_window_cutoff = (today_date - timedelta(days=SUGGESTION_REPEAT_WINDOW_DAYS)).isoformat()

    off_cooldown = []
    for candidate, nns_score in ranked:
        watchlist_id = candidate['watchlist_id']
        existing_recent = db.execute(
            '''SELECT score, target_sell_price, pattern_name FROM stock_suggestions
               WHERE watchlist_id=? AND suggestion_date >= ? AND suggestion_date < ?
               ORDER BY suggestion_date DESC LIMIT 1''',
            (watchlist_id, repeat_window_cutoff, today)
        ).fetchone()
        # Reuses compute_suggestion_pricing's target price would require a
        # price-history fetch per candidate just to filter -- cheap enough
        # given the candidate pool is already small (golden-cross + NNS_BRONZE_MIN
        # gated), so this filter step pays that cost once per candidate,
        # not per pick request.
        if existing_recent:
            price_history = _fetch_price_history(db, watchlist_id)
            pricing = compute_suggestion_pricing(
                price_history, candidate['latest_close'], TARGET_MULTIPLIER, STOP_LOSS_MULTIPLIER
            )
            if not _is_genuine_change(existing_recent, nns_score, pricing['target_sell_price']):
                continue
        off_cooldown.append((candidate, nns_score))

    return off_cooldown


def get_candidates_for_manual_pick(db, count=2, mode='top'):
    """Powers the Notifications page's manual pick-and-send flow (see
    app.py's /stocks/notifications/preview-picks): surfaces `count` of
    today's golden-cross-eligible, off-cooldown candidates for a super_admin
    to review before choosing which (if any) to actually send -- distinct
    from generate_daily_suggestions, which always takes the single top-ranked
    one automatically. mode='top' returns the `count` highest-scored
    candidates (same ordering the daily job would walk); mode='random'
    samples `count` of them at random, still only from the same eligible/
    off-cooldown pool -- "random" narrows which stock is picked, not the
    quality bar it had to clear.

    Returns [{watchlist_id, symbol, exchange, company_name, nns_score,
    nns_tier, buy_price, target_sell_price, pattern_name}, ...], length
    min(count, however many are currently eligible) -- may be shorter than
    requested, or empty, exactly like generate_daily_suggestions can produce
    zero rows on a day nothing qualifies."""
    pool = _todays_eligible_candidates_off_cooldown(db)

    if mode == 'random':
        picks = random.sample(pool, min(count, len(pool)))
    else:
        picks = pool[:count]

    results = []
    for candidate, nns_score in picks:
        watchlist_id = candidate['watchlist_id']
        price_history = _fetch_price_history(db, watchlist_id)
        pricing = compute_suggestion_pricing(
            price_history, candidate['latest_close'], TARGET_MULTIPLIER, STOP_LOSS_MULTIPLIER
        )
        results.append({
            'watchlist_id': watchlist_id, 'symbol': candidate['symbol'], 'exchange': candidate['exchange'],
            'company_name': candidate.get('company_name'),
            'nns_score': nns_score, 'nns_tier': nns_tier(nns_score),
            'buy_price': pricing['buy_price'], 'target_sell_price': pricing['target_sell_price'],
            'pattern_name': pricing['pattern_name'],
        })
    return results


def create_manual_suggestions(db, watchlist_ids):
    """Creates today's stock_suggestions row(s) for admin-chosen
    watchlist_ids (from the Notifications page's preview step, see
    get_candidates_for_manual_pick) -- rebuilds today's eligible/off-cooldown
    pool fresh (not trusting scoring/pricing from the earlier preview HTTP
    round-trip, in case anything changed in between) and creates a row via
    _create_or_update_suggestion for each requested id found in it, via the
    exact same insert path generate_daily_suggestions uses. A requested id
    that's no longer eligible (or fell onto cooldown) between preview and
    send is simply absent from the pool and gets reported skipped, not
    silently dropped -- and one that isn't found in today's pool at all
    (bad id, or eligibility changed) is also reported skipped rather than
    raising, since this is admin-facing and the caller needs to report a
    clear per-stock outcome either way.

    Returns {'created': [...], 'skipped': [...]}, same dict shapes
    generate_daily_suggestions produces."""
    pool = _todays_eligible_candidates_off_cooldown(db)
    pool_by_watchlist_id = {candidate['watchlist_id']: (candidate, nns_score) for candidate, nns_score in pool}

    today_date = date.today()
    today = today_date.isoformat()
    repeat_window_cutoff = (today_date - timedelta(days=SUGGESTION_REPEAT_WINDOW_DAYS)).isoformat()

    created = []
    skipped = []
    for watchlist_id in watchlist_ids:
        entry = pool_by_watchlist_id.get(watchlist_id)
        if entry is None:
            skipped.append({'watchlist_id': watchlist_id, 'symbol': None, 'exchange': None})
            continue
        candidate, nns_score = entry
        created_row, skipped_row = _create_or_update_suggestion(
            db, candidate, nns_score, today, repeat_window_cutoff
        )
        if created_row:
            created.append(created_row)
        else:
            skipped.append(skipped_row)

    return {'created': created, 'skipped': skipped}


def get_all_highly_recommended_today(db):
    """Powers utils/admin_alerts.py's record_and_send_highly_recommended_alerts
    -- every one of today's golden-cross-eligible candidates (see
    _rank_todays_candidates) whose NNS tier is 'golden' or 'silver'
    ("Highly Recommended", same bucket _trend_label shows customers),
    uncapped, for Raghav's own alert inbox (raga2020@gmail.com). Unlike
    generate_daily_suggestions/_create_or_update_suggestion, this
    deliberately does NOT apply the repeat-window/cooldown check -- Raghav
    wants every qualifying stock every day regardless of whether it (or the
    customer-facing Pick of the Day) already covered it recently; his alerts
    and the customer rotation are meant to be completely independent.

    Returns dicts with every field utils/suggestion_email.py's
    send_trading_alert_email (via _render_stock_card_html/_render_stock_card_text)
    needs to render exactly like a real stock_suggestions row -- watchlist_id,
    symbol, exchange, company_name, universe_id, buy_price, target_sell_price,
    stop_loss_price, holding_period_days, rsi_at_suggestion, pe_at_suggestion,
    peg_at_suggestion, opm_at_suggestion, fundamental_tier, pattern_name,
    pattern_note, score, nns_tier, rationale -- built the same way
    _create_or_update_suggestion builds a row to insert, just never inserted
    (see utils/admin_alerts.py, which stores these in its own
    stock_admin_alerts table instead). Uncapped -- may be empty on a day
    nothing clears the silver bar."""
    ranked = _rank_todays_candidates(db)
    results = []
    for candidate, nns_score in ranked:
        tier = nns_tier(nns_score)
        if tier not in ('golden', 'silver'):
            continue
        watchlist_id = candidate['watchlist_id']
        price_history = _fetch_price_history(db, watchlist_id)
        pricing = compute_suggestion_pricing(
            price_history, candidate['latest_close'], TARGET_MULTIPLIER, STOP_LOSS_MULTIPLIER
        )
        pattern_note = _build_pattern_note(pricing['pattern_name'], pricing['pattern_research'])
        rationale = _build_rationale(candidate, tier)
        results.append({
            'watchlist_id': watchlist_id, 'symbol': candidate['symbol'], 'exchange': candidate['exchange'],
            'company_name': candidate.get('company_name'), 'universe_id': candidate.get('universe_id'),
            'buy_price': pricing['buy_price'], 'target_sell_price': pricing['target_sell_price'],
            'stop_loss_price': pricing['stop_loss_price'], 'holding_period_days': HOLDING_PERIOD_DAYS,
            'rsi_at_suggestion': candidate['rsi_14'], 'pe_at_suggestion': candidate['pe_ratio'],
            'peg_at_suggestion': candidate['peg_ratio'], 'opm_at_suggestion': candidate['opm_pct'],
            'fundamental_tier': candidate.get('fundamental_tier'),
            'pattern_name': pricing['pattern_name'], 'pattern_note': pattern_note,
            'score': nns_score, 'nns_tier': tier, 'rationale': rationale,
        })
    return results
