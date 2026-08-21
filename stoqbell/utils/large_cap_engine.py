"""Nari Nakhre Stocks Standard-tier (Rs 299/month) bonus large-cap pick --
up to TOP_N_LARGE_CAP_BONUS separately-tracked picks per run, sent twice a
week (see app.py's /stocks/large-cap-bonus/send-email) IN ADDITION TO the
daily Pick of the Day, drawn only from the large-cap tier (>30,000cr, see
utils.stock_universe.rebucket_large_cap_eligibility and
utils.stock_shortlist.run_large_cap_shortlist) rather than the full mixed
mid-cap/large-cap candidate pool the daily pick draws from. A parallel
table and pipeline, same reasoning as utils/starters_engine.py's own
module docstring: get_suggestions' DISTINCT ON, the auto-trader, and the
recommendation tracker all assume exactly one row per (watchlist_id,
suggestion_date) in stock_suggestions specifically -- a separate table
with its own UNIQUE constraint needs none of that touched. Reuses the
daily engine's candidate pool/scoring/pricing (utils/suggestion_engine.py)
wholesale via _rank_todays_candidates(db, market_cap_tier='large_cap');
only the candidate pool restriction, cadence, and storage differ."""
from datetime import date, timedelta

from stoqbell.utils.nns_score import nns_tier
from stoqbell.utils.price_pattern import compute_suggestion_pricing
from stoqbell.utils.suggestion_engine import (
    HOLDING_PERIOD_DAYS,
    STOP_LOSS_MULTIPLIER,
    TARGET_MULTIPLIER,
    _build_pattern_note,
    _build_rationale,
    _fetch_price_history,
    _is_genuine_change,
    _rank_todays_candidates,
)

# One bonus pick per run -- mirrors TOP_N_SUGGESTIONS' role in
# utils/suggestion_engine.py. Since this runs twice a week (see app.py),
# that's up to 2 bonus large-cap picks a week in total, not 2 per run.
TOP_N_LARGE_CAP_BONUS = 1

# How far back a candidate's own last bonus pick counts as "too recent to
# repeat" -- same rotation reasoning as SUGGESTION_REPEAT_WINDOW_DAYS, just
# this engine's own independent window since it runs twice a week rather
# than daily. 21 days means a stock picked this week won't be picked again
# for roughly 3 weeks (about 5-6 runs) unless it genuinely changed (see
# _is_genuine_change), even if it's still the top-ranked large-cap
# candidate every run in between.
LARGE_CAP_BONUS_REPEAT_WINDOW_DAYS = 21

STOCK_LARGE_CAP_BONUS_SUGGESTIONS_TABLE_SQL = [
    '''CREATE TABLE IF NOT EXISTS stock_large_cap_bonus_suggestions (
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
        opm_at_suggestion NUMERIC(6,2),
        fundamental_tier TEXT CHECK (fundamental_tier IS NULL OR fundamental_tier IN ('golden', 'silver')),
        pattern_name TEXT,
        pattern_note TEXT,
        score NUMERIC(6,4),
        nns_tier TEXT CHECK (nns_tier IS NULL OR nns_tier IN ('golden', 'silver', 'bronze')),
        rationale TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(watchlist_id, suggestion_date)
    )'''
]


def initialize_large_cap_bonus_suggestions_table_if_needed(client):
    for sql in STOCK_LARGE_CAP_BONUS_SUGGESTIONS_TABLE_SQL:
        try:
            client.rpc('execute_sql', {'query': sql}).execute()
        except Exception as e:
            print(f'Large-cap bonus suggestions table init warning (may already exist): {e}')


def generate_large_cap_bonus_pick(db, pick_date=None):
    """Same candidate scoring/eligibility as generate_daily_suggestions
    (golden-cross required, NNS_BRONZE_MIN floor, silver/golden preferred
    -- see is_suggestion_eligible/score_candidates), but the candidate pool
    itself is restricted to market_cap_tier='large_cap' watchlist rows
    (see _rank_todays_candidates's market_cap_tier param) -- this is
    intentionally the SAME quality bar as the daily pick, just over a
    different, smaller, already fundamentals-screened-for-scale pool, not
    an extra golden-only bar the way Starters uses.

    Same cooldown/rotation rule as the daily engine (_is_genuine_change
    against this candidate's own most recent
    stock_large_cap_bonus_suggestions row within
    LARGE_CAP_BONUS_REPEAT_WINDOW_DAYS), capped at TOP_N_LARGE_CAP_BONUS
    (1) row per call -- called twice a week (see app.py), so up to 2 bonus
    picks reach a Standard subscriber's inbox in a week, never more.

    Zero rows is possible (no large-cap candidate is both golden-cross and
    off cooldown that run) -- returns {'candidates_evaluated', 'created',
    'skipped_duplicates'}, same shape as generate_daily_suggestions."""
    ranked = _rank_todays_candidates(db, market_cap_tier='large_cap')

    pick_date = pick_date or date.today()
    date_iso = pick_date.isoformat()
    repeat_window_cutoff = (pick_date - timedelta(days=LARGE_CAP_BONUS_REPEAT_WINDOW_DAYS)).isoformat()

    created = []
    skipped_duplicates = []

    for candidate, nns_score in ranked:
        if len(created) >= TOP_N_LARGE_CAP_BONUS:
            break

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
            '''SELECT score, target_sell_price, pattern_name FROM stock_large_cap_bonus_suggestions
               WHERE watchlist_id=? AND suggestion_date >= ? AND suggestion_date < ?
               ORDER BY suggestion_date DESC LIMIT 1''',
            (watchlist_id, repeat_window_cutoff, date_iso)
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
            '''INSERT INTO stock_large_cap_bonus_suggestions
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
            (watchlist_id, date_iso, buy_price, target_sell_price, stop_loss_price,
             HOLDING_PERIOD_DAYS, candidate['rsi_14'], candidate['pe_ratio'],
             candidate['peg_ratio'], candidate['opm_pct'], candidate.get('fundamental_tier'),
             pattern_name, pattern_note, nns_score, tier, rationale)
        )
        db.commit()
        suggestion_row = db.execute(
            'SELECT id FROM stock_large_cap_bonus_suggestions WHERE watchlist_id=? AND suggestion_date=?',
            (watchlist_id, date_iso)
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


def get_large_cap_bonus_suggestions(db, start_date=None, end_date=None):
    """Fetches stock_large_cap_bonus_suggestions rows (symbol/exchange
    joined from stock_watchlist), most recent first, optionally bounded by
    suggestion_date on either end -- mirrors
    utils.suggestion_engine.get_suggestions exactly (same DISTINCT ON
    (s.suggestion_date) collapsing, since generate_large_cap_bonus_pick
    only ever inserts up to TOP_N_LARGE_CAP_BONUS (1) row per call the same
    way generate_daily_suggestions does, so a re-run needs the same
    highest-score-wins defensive collapsing, not a ROW_NUMBER() top-N the
    way Starters' 2-per-week get_starters_suggestions needs). Shared by
    the bonus email (utils/suggestion_email.py) and, if ever added, a
    Standard-tier viewer history view."""
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
            FROM stock_large_cap_bonus_suggestions s
            JOIN stock_watchlist w ON w.id = s.watchlist_id
            LEFT JOIN stock_universe u ON u.symbol = w.symbol AND u.exchange = w.exchange
            {where_clause}
            ORDER BY s.suggestion_date DESC, s.score DESC''',
        tuple(params)
    ).fetchall()


def get_large_cap_bonus_suggestion_by_id(db, suggestion_id):
    """Single stock_large_cap_bonus_suggestions row, by its own id -- for
    the recommendation-analysis detail page (see app.py's
    /stocks/analysis/<source>/<id>). Mirrors
    utils.suggestion_engine.get_suggestion_by_id's shape/columns exactly
    so both (and utils.starters_engine.get_starters_suggestion_by_id) can
    feed the same analysis template regardless of which of the three
    suggestion engines produced the pick. Returns None if no such row
    exists."""
    return db.execute(
        '''SELECT s.id AS suggestion_id, w.id AS watchlist_id, w.symbol, w.exchange, w.name AS company_name,
                  u.id AS universe_id,
                  s.suggestion_date, s.buy_price,
                  s.target_sell_price, s.stop_loss_price, s.holding_period_days,
                  s.rsi_at_suggestion, s.pe_at_suggestion, s.peg_at_suggestion,
                  s.opm_at_suggestion, s.fundamental_tier,
                  s.pattern_name, s.pattern_note,
                  s.score AS nns_score, s.nns_tier, s.rationale, s.status
           FROM stock_large_cap_bonus_suggestions s
           JOIN stock_watchlist w ON w.id = s.watchlist_id
           LEFT JOIN stock_universe u ON u.symbol = w.symbol AND u.exchange = w.exchange
           WHERE s.id = ?''',
        (suggestion_id,)
    ).fetchone()
