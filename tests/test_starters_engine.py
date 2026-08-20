"""Tests for utils/starters_engine.py -- same FakeSuggestionDB pattern as
tests/test_suggestion_engine.py (candidate rows pre-seeded, matching
normalized SQL text prefixes rather than hitting a real database), just
against stock_starters_suggestions/week_start_date instead of
stock_suggestions/suggestion_date."""
from datetime import date

from tests.test_suggestion_engine import _candidate
from utils.starters_engine import (
    STARTERS_REPEAT_WINDOW_DAYS,
    TOP_N_STARTERS,
    generate_weekly_starters_pick,
    get_starters_suggestion_by_id,
)


def _golden_candidate(watchlist_id, symbol, **overrides):
    """A candidate that actually clears NNS_GOLDEN_MIN (8.0) -- the plain
    _candidate()/GOOD_FUNDAMENTALS fixture from test_suggestion_engine only
    scores ~6.9 (silver), which is exactly the daily-engine-qualifies-but-
    Starters-doesn't case this module needs to distinguish, so a
    deliberately stronger fixture is needed for the positive-path tests."""
    return _candidate(
        watchlist_id, symbol,
        peg_ratio=0.1, quarterly_profit_growth_pct=35, quarterly_revenue_growth_pct=30,
        opm_pct=45, roce_pct=30, roa_pct=20, rsi_14=52.5, pe_ratio=12, price_to_book=2,
        **overrides
    )


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeStartersDB:
    def __init__(self, candidate_rows, existing_suggestions=None, price_history=None):
        self.candidate_rows = candidate_rows
        self.suggestions = existing_suggestions or []  # list of dicts: watchlist_id, week_start_date, ...
        self.price_history = price_history or {}
        self._next_id = 1

    def execute(self, sql, params=None):
        params = params or ()
        normalized = ' '.join(sql.split())

        if normalized.startswith('SELECT w.id AS watchlist_id, w.symbol, w.exchange'):
            return FakeCursor(self.candidate_rows)

        if normalized.startswith('SELECT close FROM stock_daily_data WHERE watchlist_id=?'):
            watchlist_id, _lookback_days = params
            closes = self.price_history.get(watchlist_id, [])
            return FakeCursor([{'close': c} for c in reversed(closes)])

        if normalized.startswith('SELECT universe_id, promoter_holding_pct, fii_holding_pct, snapshot_date'):
            return FakeCursor([])

        if normalized.startswith('SELECT score, target_sell_price, pattern_name FROM stock_starters_suggestions'):
            watchlist_id, cutoff, week_start = params
            matches = sorted(
                (s for s in self.suggestions
                 if s['watchlist_id'] == watchlist_id and cutoff <= s['week_start_date'] < week_start),
                key=lambda s: s['week_start_date'], reverse=True
            )
            return FakeCursor(matches[:1])

        if normalized.startswith('INSERT INTO stock_starters_suggestions'):
            (watchlist_id, week_start_date, buy_price, target_sell_price, stop_loss_price,
             holding_period_days, rsi_at_suggestion, pe_at_suggestion, peg_at_suggestion,
             opm_at_suggestion, fundamental_tier, pattern_name, pattern_note, nns_score, nns_tier,
             rationale) = params
            fields = {
                'status': 'pending', 'buy_price': buy_price, 'target_sell_price': target_sell_price,
                'stop_loss_price': stop_loss_price,
                'opm_at_suggestion': opm_at_suggestion, 'fundamental_tier': fundamental_tier,
                'pattern_name': pattern_name, 'pattern_note': pattern_note,
                'score': nns_score, 'nns_score': nns_score, 'nns_tier': nns_tier,
            }
            existing = next(
                (s for s in self.suggestions
                 if s['watchlist_id'] == watchlist_id and s['week_start_date'] == week_start_date),
                None
            )
            if existing:
                existing.update(fields)
            else:
                self.suggestions.append({
                    'id': self._next_id, 'watchlist_id': watchlist_id, 'week_start_date': week_start_date,
                    **fields,
                })
                self._next_id += 1
            return FakeCursor([])

        if normalized.startswith('SELECT id FROM stock_starters_suggestions WHERE watchlist_id=? AND week_start_date=?'):
            watchlist_id, week_start_date = params
            matches = [
                s for s in self.suggestions
                if s['watchlist_id'] == watchlist_id and s['week_start_date'] == week_start_date
            ]
            return FakeCursor(matches[-1:])

        raise AssertionError(f'Unexpected SQL in test: {sql}')

    def commit(self):
        pass


def test_caps_at_two_picks_per_week_even_with_many_golden_candidates():
    assert TOP_N_STARTERS == 2
    candidates = [_golden_candidate(i, f'SYM{i}') for i in range(5)]
    db = FakeStartersDB(candidates)

    summary = generate_weekly_starters_pick(db)

    assert len(summary['created']) == 2
    # The two highest-scored golden candidates, not an arbitrary two.
    assert summary['created'][0]['symbol'] == 'SYM0'
    assert summary['created'][1]['symbol'] == 'SYM1'


def test_fewer_than_two_golden_candidates_never_pads_the_pick_count():
    # Only one candidate clears the golden bar -- creates just that one,
    # never pads out to TOP_N_STARTERS with a weaker pick.
    candidates = [_golden_candidate(1, 'GREATCO'), _candidate(2, 'OKAYCO')]
    db = FakeStartersDB(candidates)

    summary = generate_weekly_starters_pick(db)

    assert len(summary['created']) == 1
    assert summary['created'][0]['symbol'] == 'GREATCO'


def test_silver_tier_pool_produces_zero_picks():
    # The plain _candidate()/GOOD_FUNDAMENTALS fixture only clears NNS
    # silver (~6.9), well below NNS_GOLDEN_MIN (8.0) -- unlike the daily
    # engine, there's no silver/bronze fallback here at all.
    candidates = [_candidate(1, 'OKAYCO')]
    db = FakeStartersDB(candidates)

    summary = generate_weekly_starters_pick(db)

    assert summary['created'] == []


def test_mixed_pool_only_the_golden_candidate_is_picked():
    candidates = [_candidate(1, 'OKAYCO'), _golden_candidate(2, 'GREATCO')]
    db = FakeStartersDB(candidates)

    summary = generate_weekly_starters_pick(db)

    assert len(summary['created']) == 1
    assert summary['created'][0]['symbol'] == 'GREATCO'


def test_same_week_rerun_upserts_the_same_row_not_a_duplicate():
    candidates = [_golden_candidate(1, 'STABLE')]
    db = FakeStartersDB(candidates)

    first = generate_weekly_starters_pick(db)
    assert len(first['created']) == 1

    second = generate_weekly_starters_pick(db)
    assert len(second['created']) == 1
    assert len(db.suggestions) == 1


def test_unchanged_pick_within_the_repeat_window_is_skipped():
    candidate = _golden_candidate(1, 'STABLE')
    baseline = FakeStartersDB([candidate])
    generate_weekly_starters_pick(baseline)
    seeded_score = baseline.suggestions[0]['score']
    seeded_target = baseline.suggestions[0]['target_sell_price']

    later_week = date.today().toordinal()
    from datetime import timedelta as _td
    later_week_date = date.fromordinal(later_week) + _td(days=STARTERS_REPEAT_WINDOW_DAYS - 1)

    db = FakeStartersDB(
        [candidate],
        existing_suggestions=[{
            'id': 1, 'watchlist_id': 1, 'week_start_date': date.today().isoformat(),
            'score': seeded_score, 'target_sell_price': seeded_target, 'pattern_name': None,
        }],
    )
    summary = generate_weekly_starters_pick(db, week_start_date=later_week_date)
    assert summary['created'] == []
    assert len(summary['skipped_duplicates']) == 1


def test_pick_outside_the_repeat_window_is_resent_even_if_unchanged():
    candidate = _golden_candidate(1, 'STABLE')
    baseline = FakeStartersDB([candidate])
    generate_weekly_starters_pick(baseline)
    seeded_score = baseline.suggestions[0]['score']
    seeded_target = baseline.suggestions[0]['target_sell_price']

    from datetime import timedelta as _td
    later_week_date = date.today() + _td(days=STARTERS_REPEAT_WINDOW_DAYS + 1)

    db = FakeStartersDB(
        [candidate],
        existing_suggestions=[{
            'id': 1, 'watchlist_id': 1, 'week_start_date': date.today().isoformat(),
            'score': seeded_score, 'target_sell_price': seeded_target, 'pattern_name': None,
        }],
    )
    summary = generate_weekly_starters_pick(db, week_start_date=later_week_date)
    assert len(summary['created']) == 1


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeByIdDB:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, sql, params=None):
        normalized = ' '.join(sql.split())
        if normalized.startswith('SELECT s.id AS suggestion_id, w.id AS watchlist_id, w.symbol, w.exchange, w.name AS company_name,'):
            suggestion_id, = params
            matches = [r for r in self.rows if r['suggestion_id'] == suggestion_id]
            return _FakeCursor(matches)
        raise AssertionError(f'Unexpected SQL in test: {sql}')


def test_get_starters_suggestion_by_id_returns_the_matching_row():
    db = _FakeByIdDB([
        {'suggestion_id': 1, 'symbol': 'GOODCO', 'watchlist_id': 10, 'buy_price': 412},
        {'suggestion_id': 2, 'symbol': 'OTHERCO', 'watchlist_id': 11, 'buy_price': 200},
    ])
    row = get_starters_suggestion_by_id(db, 2)
    assert row['symbol'] == 'OTHERCO'
    assert row['watchlist_id'] == 11


def test_get_starters_suggestion_by_id_returns_none_when_not_found():
    db = _FakeByIdDB([{'suggestion_id': 1, 'symbol': 'GOODCO', 'watchlist_id': 10, 'buy_price': 412}])
    assert get_starters_suggestion_by_id(db, 999) is None
