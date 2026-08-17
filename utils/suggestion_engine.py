"""Nari Nakhre Stocks suggestion engine. Filter/scoring logic here is pure
Python (no DB access) so it's directly unit-testable; generate_daily_suggestions()
at the bottom is the only DB-orchestrating piece, mirroring the split already
used elsewhere in this codebase (e.g. fundamental_screen.py vs stock_shortlist.py)."""
from datetime import date, timedelta

from utils.price_pattern import compute_suggestion_pricing
from utils.nns_score import NNS_BRONZE_MIN, compute_nns_score, nns_tier
from utils.stock_shortlist import _compute_industry_benchmarks

# Default cap for select_top_suggestions (a general-purpose "top N
# candidates" helper, still used as such/tested independently) -- NOT used
# by generate_daily_suggestions itself, which sends every golden-cross
# candidate that clears NNS_BRONZE_MIN, uncapped (see that function's
# docstring for why: "send out all the golden cross stocks", not a
# single Pick of the Day).
TOP_N_SUGGESTIONS = 1
HOLDING_PERIOD_DAYS = 10
TARGET_MULTIPLIER = 1.05   # +5%, used as the fallback when no chart pattern applies -- see compute_suggestion_pricing
STOP_LOSS_MULTIPLIER = 0.97  # -3%, same fallback role

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


def _fetch_candidates(db):
    today = date.today().isoformat()
    fundamentals_cutoff = (date.today() - timedelta(days=20)).isoformat()
    return db.execute(
        '''SELECT w.id AS watchlist_id, w.symbol, w.exchange, w.fundamental_tier,
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
           WHERE w.is_active = 1''',
        (today, fundamentals_cutoff)
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


def get_suggestions(db, start_date=None, end_date=None):
    """Fetches stock_suggestions rows (symbol/exchange joined from
    stock_watchlist), most recent first, optionally bounded by
    suggestion_date on either end (either or both may be omitted). Shared
    by the daily email (utils/suggestion_email.py) and the read-only viewer
    pages (/stocks/my/suggestions, /stocks/my/history) so there's exactly
    one place building this join, not three."""
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
        f'''SELECT w.id AS watchlist_id, w.symbol, w.exchange, s.suggestion_date, s.buy_price,
                   s.target_sell_price, s.stop_loss_price, s.holding_period_days,
                   s.rsi_at_suggestion, s.pe_at_suggestion, s.peg_at_suggestion,
                   s.opm_at_suggestion, s.fundamental_tier,
                   s.pattern_name, s.pattern_note,
                   s.score AS nns_score, s.nns_tier, s.rationale, s.status
            FROM stock_suggestions s
            JOIN stock_watchlist w ON w.id = s.watchlist_id
            {where_clause}
            ORDER BY s.suggestion_date DESC, s.score DESC''',
        tuple(params)
    ).fetchall()


def compute_tracker_row_stats(buy_price, target_sell_price, stop_loss_price, latest_price, suggestion_date, today=None):
    """Pure per-row math for the recommendation tracker (see
    get_recommendation_tracker and app.py's /stocks/recommendations/tracker)
    -- kept separate from the DB query so the profit/loss and outcome
    arithmetic is unit-testable without a database. suggestion_date/today
    are date objects (or ISO strings, which get parsed) -- today defaults to
    date.today(). Returns {'days_elapsed', 'pct_change', 'outcome'}:
      - days_elapsed: whole days since the suggestion, >= 0 (never negative
        -- a suggestion is never "in the future" relative to today).
      - pct_change: (latest - buy) / buy * 100, rounded to 1 decimal, or
        None if buy_price or latest_price is missing (nothing to divide).
      - outcome: 'target_hit' if latest_price has reached target_sell_price,
        'stop_loss_hit' if it's dropped to/through stop_loss_price (checked
        AFTER target, so a pattern-based suggestion with an unusually tight
        stop that also happens to sit at/above target reads as the good
        outcome, not the bad one), else 'open'. 'unknown' if latest_price
        is missing entirely (nothing synced yet)."""
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

    return {'days_elapsed': days_elapsed, 'pct_change': pct_change, 'outcome': outcome}


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
    definition a watchlist row)."""
    return db.execute(
        '''SELECT s.id, w.id AS watchlist_id, w.symbol, w.exchange, s.suggestion_date,
                  s.buy_price, s.target_sell_price, s.stop_loss_price, s.status,
                  s.nns_tier, s.score AS nns_score, s.pattern_name, s.rationale,
                  d.close AS latest_price, d.trade_date AS price_date
           FROM stock_suggestions s
           JOIN stock_watchlist w ON w.id = s.watchlist_id
           LEFT JOIN stock_daily_data d ON d.watchlist_id = w.id
               AND d.trade_date = (
                   SELECT MAX(d2.trade_date) FROM stock_daily_data d2 WHERE d2.watchlist_id = w.id
               )
           ORDER BY s.suggestion_date DESC, s.id DESC'''
    ).fetchall()


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

    Every golden-cross candidate that clears NNS_BRONZE_MIN gets a row for
    today, uncapped -- this is a daily "here's the current full picture"
    digest, not a single novelty pick, so a stock that also qualified
    yesterday (or every day this month) is included again today rather
    than being suppressed as a repeat. A day can still end up with zero
    rows if no golden-cross candidate clears NNS_BRONZE_MIN at all.

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
    candidates = _fetch_candidates(db)
    previous_snapshots_by_watchlist = _fetch_previous_snapshots(db, candidates)
    industry_benchmarks_by_industry = _compute_industry_benchmarks(candidates)
    eligible = [c for c in candidates if is_suggestion_eligible(c)]
    ranked = score_candidates(eligible, previous_snapshots_by_watchlist, industry_benchmarks_by_industry)

    today = date.today().isoformat()

    created = []

    for candidate, nns_score in ranked:
        watchlist_id = candidate['watchlist_id']

        price_history = _fetch_price_history(db, watchlist_id)
        pricing = compute_suggestion_pricing(
            price_history, candidate['latest_close'], TARGET_MULTIPLIER, STOP_LOSS_MULTIPLIER
        )

        buy_price = pricing['buy_price']
        target_sell_price = pricing['target_sell_price']
        stop_loss_price = pricing['stop_loss_price']
        pattern_name = pricing['pattern_name']
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
        created.append({
            'suggestion_id': suggestion_row['id'] if suggestion_row else None,
            'watchlist_id': watchlist_id, 'symbol': candidate['symbol'], 'exchange': candidate['exchange'],
            'buy_price': buy_price, 'target_sell_price': target_sell_price, 'stop_loss_price': stop_loss_price,
            'nns_score': nns_score, 'nns_tier': tier, 'pattern_name': pattern_name,
        })

    return {
        'candidates_evaluated': len(candidates),
        'created': created,
    }
