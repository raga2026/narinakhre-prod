from datetime import date, timedelta

from utils.suggestion_engine import (
    HOLDING_PERIOD_DAYS,
    TOP_N_SUGGESTIONS,
    generate_daily_suggestions,
    passes_hard_filters,
    score_candidates,
    select_top_suggestions,
)

GOOD_INDICATORS = {'cross_status': 'golden_cross', 'volume_trend': 'confirming', 'rsi_14': 50}


def _candidate(watchlist_id, symbol, peg_ratio, quarterly_profit_growth_pct, opm_pct, roce_pct, **overrides):
    row = {
        'watchlist_id': watchlist_id, 'symbol': symbol, 'exchange': 'NSE',
        'peg_ratio': peg_ratio, 'quarterly_profit_growth_pct': quarterly_profit_growth_pct,
        'opm_pct': opm_pct, 'roce_pct': roce_pct,
        'pe_ratio': 20, 'latest_close': 100.0,
        **GOOD_INDICATORS,
    }
    row.update(overrides)
    return row


def test_scoring_picks_the_objectively_better_candidate():
    # BETTER is strictly better on every metric: lower PEG, higher growth,
    # higher OPM, higher ROCE. Must score higher regardless of order.
    worse = _candidate(1, 'WORSE', peg_ratio=1.5, quarterly_profit_growth_pct=8, opm_pct=15, roce_pct=10)
    better = _candidate(2, 'BETTER', peg_ratio=0.5, quarterly_profit_growth_pct=20, opm_pct=35, roce_pct=25)

    scored = score_candidates([worse, better])

    assert scored[0][0]['symbol'] == 'BETTER'
    assert scored[0][1] > scored[1][1]


def test_candidate_failing_rsi_range_excluded_even_with_great_score():
    # Excellent fundamentals, but RSI is way outside the 40-65 window.
    great_but_overbought = _candidate(
        1, 'OVERBOUGHT', peg_ratio=0.3, quarterly_profit_growth_pct=30, opm_pct=40, roce_pct=30,
        rsi_14=85,
    )
    mediocre_but_in_range = _candidate(2, 'INRANGE', peg_ratio=1.2, quarterly_profit_growth_pct=10, opm_pct=18, roce_pct=12)

    assert passes_hard_filters(great_but_overbought) is False
    assert passes_hard_filters(mediocre_but_in_range) is True

    top = select_top_suggestions([great_but_overbought, mediocre_but_in_range])

    symbols = [c['symbol'] for c, _ in top]
    assert 'OVERBOUGHT' not in symbols
    assert 'INRANGE' in symbols


def test_hard_filters_check_cross_status_and_volume_trend_too():
    death_cross = _candidate(1, 'DEATHX', peg_ratio=0.5, quarterly_profit_growth_pct=20, opm_pct=30, roce_pct=20,
                              cross_status='death_cross')
    diverging_volume = _candidate(2, 'DIVERGE', peg_ratio=0.5, quarterly_profit_growth_pct=20, opm_pct=30, roce_pct=20,
                                   volume_trend='diverging')

    assert passes_hard_filters(death_cross) is False
    assert passes_hard_filters(diverging_volume) is False


def test_top_n_selection_respects_top_n_suggestions_with_more_candidates():
    candidates = [
        _candidate(i, f'SYM{i}', peg_ratio=1.0 - i * 0.1, quarterly_profit_growth_pct=10 + i,
                   opm_pct=20 + i, roce_pct=15 + i)
        for i in range(5)
    ]

    top_default = select_top_suggestions(candidates)
    assert len(top_default) == TOP_N_SUGGESTIONS

    top_custom = select_top_suggestions(candidates, top_n=2)
    assert len(top_custom) == 2
    # The two selected must be the two highest-scoring of the five.
    all_scored = score_candidates(candidates)
    assert [c['symbol'] for c, _ in top_custom] == [c['symbol'] for c, _ in all_scored[:2]]


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeSuggestionDB:
    """Minimal stand-in for app.py's SupabaseDB -- pre-seeded with the
    candidate rows _fetch_candidates() would have returned, so this tests
    generate_daily_suggestions()'s insert/dedup logic without needing to
    replicate the real multi-table JOIN in Python."""

    def __init__(self, candidate_rows, existing_suggestions=None):
        self.candidate_rows = candidate_rows
        self.suggestions = existing_suggestions or []  # list of dicts: watchlist_id, status, suggestion_date

    def execute(self, sql, params=None):
        params = params or ()
        normalized = ' '.join(sql.split())

        if normalized.startswith('SELECT w.id AS watchlist_id, w.symbol, w.exchange'):
            return FakeCursor(self.candidate_rows)

        if normalized.startswith("SELECT id FROM stock_suggestions WHERE watchlist_id=? AND status='pending'"):
            watchlist_id, cutoff = params
            matches = [
                s for s in self.suggestions
                if s['watchlist_id'] == watchlist_id and s['status'] == 'pending' and s['suggestion_date'] >= cutoff
            ]
            return FakeCursor([{'id': 1}] if matches else [])

        if normalized.startswith('INSERT INTO stock_suggestions'):
            (watchlist_id, suggestion_date, buy_price, target_sell_price, stop_loss_price,
             holding_period_days, rsi_at_suggestion, pe_at_suggestion, peg_at_suggestion,
             score, rationale) = params
            self.suggestions.append({
                'watchlist_id': watchlist_id, 'suggestion_date': suggestion_date,
                'status': 'pending', 'buy_price': buy_price,
            })
            return FakeCursor([])

        raise AssertionError(f'Unexpected SQL in test: {sql}')

    def commit(self):
        pass


def test_no_duplicate_suggestion_for_symbol_with_existing_open_one():
    candidates = [_candidate(1, 'ALREADYOPEN', peg_ratio=0.5, quarterly_profit_growth_pct=20, opm_pct=30, roce_pct=20)]
    recent_date = (date.today() - timedelta(days=2)).isoformat()
    db = FakeSuggestionDB(
        candidates,
        existing_suggestions=[{'watchlist_id': 1, 'status': 'pending', 'suggestion_date': recent_date}],
    )

    summary = generate_daily_suggestions(db)

    assert summary['created'] == []
    assert summary['skipped_duplicates'] == ['ALREADYOPEN']
    # Still only the one pre-existing row -- nothing new inserted.
    assert len(db.suggestions) == 1


def test_new_suggestion_created_when_no_open_one_exists():
    candidates = [_candidate(1, 'FRESH', peg_ratio=0.5, quarterly_profit_growth_pct=20, opm_pct=30, roce_pct=20)]
    db = FakeSuggestionDB(candidates)

    summary = generate_daily_suggestions(db)

    assert len(summary['created']) == 1
    assert summary['created'][0]['symbol'] == 'FRESH'
    assert summary['skipped_duplicates'] == []
    assert len(db.suggestions) == 1
