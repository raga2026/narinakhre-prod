"""Nari Nakhre Stocks suggestion engine. Filter/scoring logic here is pure
Python (no DB access) so it's directly unit-testable; generate_daily_suggestions()
at the bottom is the only DB-orchestrating piece, mirroring the split already
used elsewhere in this codebase (e.g. fundamental_screen.py vs stock_shortlist.py)."""
from datetime import date, timedelta

TOP_N_SUGGESTIONS = 3
HOLDING_PERIOD_DAYS = 10
TARGET_MULTIPLIER = 1.05   # +5%
STOP_LOSS_MULTIPLIER = 0.97  # -3%

RSI_MIN, RSI_MAX = 40, 65

# Equal weighting (25% each) across four fundamentals metrics -- the
# simplest, most transparent scheme absent any specific guidance on
# relative importance between them. PEG is inverted (lower raw PEG scores
# higher) since lower PEG is better; the other three are used as-is since
# higher is better for each. Documented here rather than tuned/hidden so
# the scoring is inspectable, not a black box.
SCORE_WEIGHTS = {
    'peg_ratio': 0.25,
    'quarterly_profit_growth_pct': 0.25,
    'opm_pct': 0.25,
    'roce_pct': 0.25,
}

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
]


def initialize_stock_suggestions_table_if_needed(client):
    for sql in STOCK_SUGGESTIONS_TABLE_SQL + STOCK_SUGGESTIONS_ALTER_SQL:
        try:
            client.rpc('execute_sql', {'query': sql}).execute()
        except Exception as e:
            print(f'Stock suggestions table init warning (may already exist): {e}')


def passes_hard_filters(candidate):
    """candidate is dict-like with cross_status/volume_trend/rsi_14 keys.
    All three must pass for a candidate to be suggestion-eligible at all,
    regardless of how well it would otherwise score."""
    return (
        candidate.get('cross_status') == 'golden_cross'
        and candidate.get('volume_trend') == 'confirming'
        and candidate.get('rsi_14') is not None
        and RSI_MIN <= candidate['rsi_14'] <= RSI_MAX
    )


def _normalize(values):
    """Min-max normalize a list of values to [0, 1]. Returns 0.5 for every
    value when they're all equal (avoids a divide-by-zero, and a field
    that doesn't vary across the candidate pool shouldn't swing anyone's
    score either direction)."""
    lo, hi = min(values), max(values)
    if hi == lo:
        return [0.5] * len(values)
    return [(v - lo) / (hi - lo) for v in values]


def score_candidates(candidates):
    """Scores each candidate 0-1 using SCORE_WEIGHTS over four normalized
    fundamentals metrics (peg_ratio, quarterly_profit_growth_pct, opm_pct,
    roce_pct -- all required to be present and numeric on every candidate).
    Returns [(candidate, score), ...] sorted highest score first. Does NOT
    apply passes_hard_filters -- callers are expected to filter first (see
    select_top_suggestions)."""
    peg_norm = _normalize([c['peg_ratio'] for c in candidates])
    profit_growth_norm = _normalize([c['quarterly_profit_growth_pct'] for c in candidates])
    opm_norm = _normalize([c['opm_pct'] for c in candidates])
    roce_norm = _normalize([c['roce_pct'] for c in candidates])

    scored = []
    for i, candidate in enumerate(candidates):
        score = (
            SCORE_WEIGHTS['peg_ratio'] * (1 - peg_norm[i])
            + SCORE_WEIGHTS['quarterly_profit_growth_pct'] * profit_growth_norm[i]
            + SCORE_WEIGHTS['opm_pct'] * opm_norm[i]
            + SCORE_WEIGHTS['roce_pct'] * roce_norm[i]
        )
        scored.append((candidate, round(score, 4)))

    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored


def select_top_suggestions(candidates, top_n=TOP_N_SUGGESTIONS):
    """Filters candidates to hard-filter-passers, scores them, and returns
    the top_n as [(candidate, score), ...], highest first. Returns fewer
    than top_n (even zero) if fewer candidates pass the hard filters --
    never pads with filter-failing candidates just to reach a count."""
    eligible = [c for c in candidates if passes_hard_filters(c)]
    if not eligible:
        return []
    return score_candidates(eligible)[:top_n]


def _build_rationale(candidate):
    parts = ['Golden cross with confirming volume']
    if candidate.get('peg_ratio') is not None:
        parts.append(f"PEG {candidate['peg_ratio']:.2f}")
    if candidate.get('opm_pct') is not None:
        parts.append(f"OPM {candidate['opm_pct']:.0f}%")
    return ', '.join(parts)


def _fetch_candidates(db):
    today = date.today().isoformat()
    fundamentals_cutoff = (date.today() - timedelta(days=20)).isoformat()
    return db.execute(
        '''SELECT w.id AS watchlist_id, w.symbol, w.exchange, w.fundamental_tier,
                  i.rsi_14, i.cross_status, i.volume_trend,
                  f.pe_ratio, f.peg_ratio, f.opm_pct, f.roce_pct,
                  f.quarterly_profit_growth_pct,
                  d.close AS latest_close
           FROM stock_watchlist w
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
        f'''SELECT w.symbol, w.exchange, s.suggestion_date, s.buy_price,
                   s.target_sell_price, s.stop_loss_price, s.holding_period_days,
                   s.rsi_at_suggestion, s.pe_at_suggestion, s.peg_at_suggestion,
                   s.opm_at_suggestion, s.fundamental_tier,
                   s.score, s.rationale, s.status
            FROM stock_suggestions s
            JOIN stock_watchlist w ON w.id = s.watchlist_id
            {where_clause}
            ORDER BY s.suggestion_date DESC, s.score DESC''',
        tuple(params)
    ).fetchall()


def generate_daily_suggestions(db):
    """Builds the candidate pool (active watchlist joined to today's
    indicators and each symbol's latest fundamentals snapshot within 20
    days), applies the hard filters and scoring (see select_top_suggestions),
    and inserts a stock_suggestions row for each of the top TOP_N_SUGGESTIONS
    -- skipping (not backfilling from the next-best candidate) any symbol
    that already has an open (status='pending') suggestion from within the
    last HOLDING_PERIOD_DAYS. That means a symbol being skipped here can
    result in fewer than TOP_N_SUGGESTIONS new suggestions on a given day,
    by design -- this is meant to avoid re-suggesting a stock that's
    already an active pending call."""
    candidates = _fetch_candidates(db)
    top = select_top_suggestions(candidates, top_n=TOP_N_SUGGESTIONS)

    today = date.today().isoformat()
    open_suggestion_cutoff = (date.today() - timedelta(days=HOLDING_PERIOD_DAYS)).isoformat()

    created = []
    skipped_duplicates = []

    for candidate, score in top:
        watchlist_id = candidate['watchlist_id']

        existing_open = db.execute(
            '''SELECT id FROM stock_suggestions
               WHERE watchlist_id=? AND status='pending' AND suggestion_date >= ?
               LIMIT 1''',
            (watchlist_id, open_suggestion_cutoff)
        ).fetchone()
        if existing_open:
            skipped_duplicates.append(candidate['symbol'])
            continue

        buy_price = candidate['latest_close']
        target_sell_price = round(buy_price * TARGET_MULTIPLIER, 2)
        stop_loss_price = round(buy_price * STOP_LOSS_MULTIPLIER, 2)
        rationale = _build_rationale(candidate)

        db.execute(
            '''INSERT INTO stock_suggestions
                   (watchlist_id, suggestion_date, buy_price, target_sell_price,
                    stop_loss_price, holding_period_days, rsi_at_suggestion,
                    pe_at_suggestion, peg_at_suggestion, opm_at_suggestion,
                    fundamental_tier, score, rationale, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
               ON CONFLICT (watchlist_id, suggestion_date) DO UPDATE SET
                   buy_price = EXCLUDED.buy_price,
                   target_sell_price = EXCLUDED.target_sell_price,
                   stop_loss_price = EXCLUDED.stop_loss_price,
                   rsi_at_suggestion = EXCLUDED.rsi_at_suggestion,
                   pe_at_suggestion = EXCLUDED.pe_at_suggestion,
                   peg_at_suggestion = EXCLUDED.peg_at_suggestion,
                   opm_at_suggestion = EXCLUDED.opm_at_suggestion,
                   fundamental_tier = EXCLUDED.fundamental_tier,
                   score = EXCLUDED.score,
                   rationale = EXCLUDED.rationale''',
            (watchlist_id, today, buy_price, target_sell_price, stop_loss_price,
             HOLDING_PERIOD_DAYS, candidate['rsi_14'], candidate['pe_ratio'],
             candidate['peg_ratio'], candidate['opm_pct'], candidate.get('fundamental_tier'),
             score, rationale)
        )
        db.commit()
        created.append({'symbol': candidate['symbol'], 'buy_price': buy_price, 'score': score})

    return {
        'candidates_evaluated': len(candidates),
        'created': created,
        'skipped_duplicates': skipped_duplicates,
    }
