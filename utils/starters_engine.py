"""Nari Nakhre Stocks "Starters" tier (Rs 99/month, see STOCKS_AUTH_ALTER_SQL's
stocks_plan column) -- up to TOP_N_STARTERS separately-curated, golden-tier-only
picks a week instead of the daily engine's Pick of the Day. Deliberately a
PARALLEL table and pipeline, not a filter bolted onto stock_suggestions --
see generate_weekly_starters_pick's docstring for why. Reuses the daily
engine's candidate pool/scoring/pricing (utils/suggestion_engine.py)
wholesale; only the selection policy (stricter quality bar, weekly cadence,
up to 2 picks instead of 1) and storage are different, so nothing about
golden-cross detection, NNS Score, or buy/target/stop-loss pricing is
duplicated here."""
from datetime import date, timedelta

from utils.nns_score import NNS_GOLDEN_MIN, nns_tier
from utils.price_pattern import compute_suggestion_pricing
from utils.suggestion_engine import (
    HOLDING_PERIOD_DAYS,
    STOP_LOSS_MULTIPLIER,
    TARGET_MULTIPLIER,
    _build_pattern_note,
    _build_rationale,
    _fetch_price_history,
    _is_genuine_change,
    _rank_todays_candidates,
)

# The weekly cap -- up to two stock_starters_suggestions rows per week (two
# emails' worth of picks bundled into the one Monday send), not every
# currently-qualifying golden-tier candidate. Mirrors TOP_N_SUGGESTIONS'
# role in utils/suggestion_engine.py, just a different count.
TOP_N_STARTERS = 2

# How far back a candidate's own last weekly pick counts as "too recent to
# repeat" -- same rotation reasoning as SUGGESTION_REPEAT_WINDOW_DAYS in
# utils/suggestion_engine.py, just its own independent window since this
# runs weekly rather than daily. 28 days (4 weeks) means a stock that had
# its turn one Monday won't be picked again for roughly a month unless it
# genuinely changed (see _is_genuine_change), even if it's still the
# highest-scored golden-tier candidate every week in between.
STARTERS_REPEAT_WINDOW_DAYS = 28

STOCK_STARTERS_SUGGESTIONS_TABLE_SQL = [
    '''CREATE TABLE IF NOT EXISTS stock_starters_suggestions (
        id BIGSERIAL PRIMARY KEY,
        watchlist_id BIGINT NOT NULL REFERENCES stock_watchlist(id),
        week_start_date DATE NOT NULL,
        buy_price NUMERIC(12,2),
        target_sell_price NUMERIC(12,2),
        stop_loss_price NUMERIC(12,2),
        holding_period_days INTEGER,
        rsi_at_suggestion NUMERIC(6,2),
        pe_at_suggestion NUMERIC(10,2),
        peg_at_suggestion NUMERIC(10,2),
        opm_at_suggestion NUMERIC(6,2),
        fundamental_tier TEXT CHECK (fundamental_tier IS NULL OR fundamental_tier IN ('golden', 'silver')),
        pattern_name TEXT,
        pattern_note TEXT,
        score NUMERIC(6,4),
        nns_tier TEXT CHECK (nns_tier IS NULL OR nns_tier IN ('golden', 'silver', 'bronze')),
        rationale TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(watchlist_id, week_start_date)
    )'''
]


def initialize_starters_suggestions_table_if_needed(client):
    for sql in STOCK_STARTERS_SUGGESTIONS_TABLE_SQL:
        try:
            client.rpc('execute_sql', {'query': sql}).execute()
        except Exception as e:
            print(f'Starters suggestions table init warning (may already exist): {e}')


def generate_weekly_starters_pick(db, week_start_date=None):
    """Same candidate pool/scoring as generate_daily_suggestions
    (utils/suggestion_engine.py -- see _rank_todays_candidates), but a
    materially stricter selection policy: ONLY a golden-tier candidate
    (NNS_GOLDEN_MIN, 8.0+) is ever picked -- no silver/bronze fallback, no
    "always create something" pressure. Since _rank_todays_candidates
    returns candidates sorted by score descending, this walks the list and
    stops entirely (not just skips) the moment a candidate falls below
    NNS_GOLDEN_MIN, since nothing further down the list could qualify
    either.

    Same cooldown/rotation rule as the daily engine (_is_genuine_change
    against this candidate's own most recent stock_starters_suggestions
    row within STARTERS_REPEAT_WINDOW_DAYS), and same TOP_N-style cap --
    up to TOP_N_STARTERS (2) picks per run instead of the daily engine's 1
    -- but its own independent history table, so a stock's daily-pick
    cooldown and its Starters cooldown never interact with each other.
    Fewer than TOP_N_STARTERS (including zero) is common and expected: only
    as many golden-tier, off-cooldown candidates as actually exist that
    week are picked, never padded out with a weaker one just to reach the
    count.

    A parallel table (not a shared-table discriminator column on
    stock_suggestions) on purpose: get_suggestions' DISTINCT ON collapsing,
    the auto-trader, and the recommendation tracker all assume exactly one
    row per (watchlist_id, suggestion_date) globally -- overloading that
    table would mean touching all three. A separate table with its own
    UNIQUE(watchlist_id, week_start_date) needs none of that.

    Zero rows most weeks is expected and normal, not a bug -- the golden
    bar alone, before cooldown is even considered, often isn't cleared by
    anything that week. Returns {'candidates_evaluated', 'created',
    'skipped_duplicates'}, same shape as generate_daily_suggestions."""
    ranked = _rank_todays_candidates(db)

    week_start = week_start_date or date.today()
    week_start_iso = week_start.isoformat()
    repeat_window_cutoff = (week_start - timedelta(days=STARTERS_REPEAT_WINDOW_DAYS)).isoformat()

    created = []
    skipped_duplicates = []

    for candidate, nns_score in ranked:
        if len(created) >= TOP_N_STARTERS:
            break
        if nns_score < NNS_GOLDEN_MIN:
            break  # ranked is sorted descending -- nothing further qualifies either

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
            '''SELECT score, target_sell_price, pattern_name FROM stock_starters_suggestions
               WHERE watchlist_id=? AND week_start_date >= ? AND week_start_date < ?
               ORDER BY week_start_date DESC LIMIT 1''',
            (watchlist_id, repeat_window_cutoff, week_start_iso)
        ).fetchone()
        if existing_recent and not _is_genuine_change(existing_recent, nns_score, target_sell_price):
            skipped_duplicates.append({
                'watchlist_id': watchlist_id, 'symbol': candidate['symbol'], 'exchange': candidate['exchange'],
            })
            continue

        pattern_note = _build_pattern_note(pattern_name, pricing['pattern_research'])
        tier = nns_tier(nns_score)
        rationale = _build_rationale(candidate, tier)

        db.execute(
            '''INSERT INTO stock_starters_suggestions
                   (watchlist_id, week_start_date, buy_price, target_sell_price,
                    stop_loss_price, holding_period_days, rsi_at_suggestion,
                    pe_at_suggestion, peg_at_suggestion, opm_at_suggestion,
                    fundamental_tier, pattern_name, pattern_note, score, nns_tier, rationale, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
               ON CONFLICT (watchlist_id, week_start_date) DO UPDATE SET
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
            (watchlist_id, week_start_iso, buy_price, target_sell_price, stop_loss_price,
             HOLDING_PERIOD_DAYS, candidate['rsi_14'], candidate['pe_ratio'],
             candidate['peg_ratio'], candidate['opm_pct'], candidate.get('fundamental_tier'),
             pattern_name, pattern_note, nns_score, tier, rationale)
        )
        db.commit()
        suggestion_row = db.execute(
            'SELECT id FROM stock_starters_suggestions WHERE watchlist_id=? AND week_start_date=?',
            (watchlist_id, week_start_iso)
        ).fetchone()
        created.append({
            'suggestion_id': suggestion_row['id'] if suggestion_row else None,
            'watchlist_id': watchlist_id, 'symbol': candidate['symbol'], 'exchange': candidate['exchange'],
            'buy_price': buy_price, 'target_sell_price': target_sell_price, 'stop_loss_price': stop_loss_price,
            'nns_score': nns_score, 'nns_tier': tier, 'pattern_name': pattern_name,
        })

    return {
        'candidates_evaluated': len(ranked),
        'created': created,
        'skipped_duplicates': skipped_duplicates,
    }


def get_starters_suggestions(db, start_date=None, end_date=None):
    """Fetches stock_starters_suggestions rows (symbol/exchange joined from
    stock_watchlist), most recent week first, optionally bounded by
    week_start_date on either end -- mirrors
    utils.suggestion_engine.get_suggestions' structure (same universe_id
    join for the "View full analysis" link), but collapses each week down
    to its top TOP_N_STARTERS rows by score (a ROW_NUMBER() window
    function, not get_suggestions' plain DISTINCT ON -- that only ever
    keeps ONE row per period, which was correct for the daily engine's
    single-pick-a-day policy but would silently drop the second Starters
    pick every week). Same defensive purpose as the daily version's
    DISTINCT ON otherwise: robust against the job ever being re-run
    mid-week with a different top-N by score, without depending on how
    many rows physically accumulated upstream. Shared by the weekly email
    (utils/suggestion_email.py) and the Starters-branch of the viewer
    dashboard (app.py's stocks_my_suggestions/stocks_my_history when
    stocks_plan=='starters')."""
    conditions = []
    params = []
    if start_date:
        conditions.append('s.week_start_date >= ?')
        params.append(start_date)
    if end_date:
        conditions.append('s.week_start_date <= ?')
        params.append(end_date)
    where_clause = ('WHERE ' + ' AND '.join(conditions)) if conditions else ''

    return db.execute(
        f'''SELECT suggestion_id, watchlist_id, symbol, exchange, company_name, universe_id,
                   suggestion_date, buy_price, target_sell_price, stop_loss_price,
                   holding_period_days, rsi_at_suggestion, pe_at_suggestion,
                   peg_at_suggestion, opm_at_suggestion, fundamental_tier,
                   pattern_name, pattern_note, nns_score, nns_tier, rationale, status
            FROM (
                SELECT s.id AS suggestion_id, w.id AS watchlist_id, w.symbol, w.exchange, w.name AS company_name,
                       u.id AS universe_id,
                       s.week_start_date AS suggestion_date, s.buy_price,
                       s.target_sell_price, s.stop_loss_price, s.holding_period_days,
                       s.rsi_at_suggestion, s.pe_at_suggestion, s.peg_at_suggestion,
                       s.opm_at_suggestion, s.fundamental_tier,
                       s.pattern_name, s.pattern_note,
                       s.score AS nns_score, s.nns_tier, s.rationale, s.status,
                       ROW_NUMBER() OVER (PARTITION BY s.week_start_date ORDER BY s.score DESC) AS rn
                FROM stock_starters_suggestions s
                JOIN stock_watchlist w ON w.id = s.watchlist_id
                LEFT JOIN stock_universe u ON u.symbol = w.symbol AND u.exchange = w.exchange
                {where_clause}
            ) ranked
            WHERE rn <= {TOP_N_STARTERS}
            ORDER BY suggestion_date DESC, nns_score DESC''',
        tuple(params)
    ).fetchall()


def get_starters_suggestion_by_id(db, suggestion_id):
    """Single stock_starters_suggestions row, by its own id -- for the
    recommendation-analysis detail page (see app.py's
    /stocks/analysis/<source>/<id>). Mirrors
    utils.suggestion_engine.get_suggestion_by_id's shape/columns exactly
    (suggestion_date aliased from week_start_date) so both (and
    utils.large_cap_engine.get_large_cap_bonus_suggestion_by_id) can feed
    the same analysis template regardless of which of the three
    suggestion engines produced the pick. Returns None if no such row
    exists."""
    return db.execute(
        '''SELECT s.id AS suggestion_id, w.id AS watchlist_id, w.symbol, w.exchange, w.name AS company_name,
                  u.id AS universe_id,
                  s.week_start_date AS suggestion_date, s.buy_price,
                  s.target_sell_price, s.stop_loss_price, s.holding_period_days,
                  s.rsi_at_suggestion, s.pe_at_suggestion, s.peg_at_suggestion,
                  s.opm_at_suggestion, s.fundamental_tier,
                  s.pattern_name, s.pattern_note,
                  s.score AS nns_score, s.nns_tier, s.rationale, s.status
           FROM stock_starters_suggestions s
           JOIN stock_watchlist w ON w.id = s.watchlist_id
           LEFT JOIN stock_universe u ON u.symbol = w.symbol AND u.exchange = w.exchange
           WHERE s.id = ?''',
        (suggestion_id,)
    ).fetchone()
