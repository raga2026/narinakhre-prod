"""Tests for utils/large_cap_engine.py -- same FakeSuggestionDB pattern as
tests/test_starters_engine.py (candidate rows pre-seeded, matching
normalized SQL text prefixes rather than hitting a real database), just
against stock_large_cap_bonus_suggestions/suggestion_date and the daily
engine's own bronze+/golden-cross bar (no golden-only restriction the way
Starters has)."""
from datetime import date, timedelta

from tests.test_suggestion_engine import _candidate
from tests.test_starters_engine import _golden_candidate
from stoqbell.utils.large_cap_engine import (
    LARGE_CAP_BONUS_REPEAT_WINDOW_DAYS,
    TOP_N_LARGE_CAP_BONUS,
    generate_large_cap_bonus_pick,
    get_large_cap_bonus_suggestion_by_id,
)


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeLargeCapDB:
    def __init__(self, candidate_rows, existing_suggestions=None, price_history=None):
        self.candidate_rows = candidate_rows
        self.suggestions = existing_suggestions or []  # list of dicts: watchlist_id, suggestion_date, ...
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

        if normalized.startswith('SELECT score, target_sell_price, pattern_name FROM stock_large_cap_bonus_suggestions'):
            watchlist_id, cutoff, pick_date = params
            matches = sorted(
                (s for s in self.suggestions
                 if s['watchlist_id'] == watchlist_id and cutoff <= s['suggestion_date'] < pick_date),
                key=lambda s: s['suggestion_date'], reverse=True
            )
            return FakeCursor(matches[:1])

        if normalized.startswith('INSERT INTO stock_large_cap_bonus_suggestions'):
            (watchlist_id, suggestion_date, buy_price, target_sell_price, stop_loss_price,
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
                 if s['watchlist_id'] == watchlist_id and s['suggestion_date'] == suggestion_date),
                None
            )
            if existing:
                existing.update(fields)
            else:
                self.suggestions.append({
                    'id': self._next_id, 'watchlist_id': watchlist_id, 'suggestion_date': suggestion_date,
                    **fields,
                })
                self._next_id += 1
            return FakeCursor([])

        if normalized.startswith('SELECT id FROM stock_large_cap_bonus_suggestions WHERE watchlist_id=? AND suggestion_date=?'):
            watchlist_id, suggestion_date = params
            matches = [
                s for s in self.suggestions
                if s['watchlist_id'] == watchlist_id and s['suggestion_date'] == suggestion_date
            ]
            return FakeCursor(matches[-1:])

        raise AssertionError(f'Unexpected SQL in test: {sql}')

    def commit(self):
        pass


def test_caps_at_one_pick_per_run_even_with_multiple_qualifying_candidates():
    assert TOP_N_LARGE_CAP_BONUS == 1
    candidates = [_candidate(1, 'SYM1', peg_ratio=0.5), _candidate(2, 'SYM2', peg_ratio=0.1)]
    db = FakeLargeCapDB(candidates)

    summary = generate_large_cap_bonus_pick(db)

    assert len(summary['created']) == 1
    # The higher-scored candidate (lower PEG, all else equal) wins.
    assert summary['created'][0]['symbol'] == 'SYM2'


def test_silver_tier_candidate_still_qualifies_no_golden_only_restriction():
    # Unlike Starters, the bonus large-cap pick uses the SAME bronze+ bar
    # as the daily engine -- a plain silver-scoring candidate (~6.9, well
    # below NNS_GOLDEN_MIN) should still be picked.
    candidates = [_candidate(1, 'OKAYCO')]
    db = FakeLargeCapDB(candidates)

    summary = generate_large_cap_bonus_pick(db)

    assert len(summary['created']) == 1
    assert summary['created'][0]['symbol'] == 'OKAYCO'


def test_zero_qualifying_candidates_produces_zero_picks():
    db = FakeLargeCapDB([])

    summary = generate_large_cap_bonus_pick(db)

    assert summary['created'] == []


def test_same_day_rerun_upserts_the_same_row_not_a_duplicate():
    candidates = [_golden_candidate(1, 'STABLE')]
    db = FakeLargeCapDB(candidates)

    first = generate_large_cap_bonus_pick(db)
    assert len(first['created']) == 1

    second = generate_large_cap_bonus_pick(db)
    assert len(second['created']) == 1
    assert len(db.suggestions) == 1


def test_unchanged_pick_within_the_repeat_window_is_skipped():
    candidate = _golden_candidate(1, 'STABLE')
    baseline = FakeLargeCapDB([candidate])
    generate_large_cap_bonus_pick(baseline)
    seeded_score = baseline.suggestions[0]['score']
    seeded_target = baseline.suggestions[0]['target_sell_price']

    later_pick_date = date.today() + timedelta(days=LARGE_CAP_BONUS_REPEAT_WINDOW_DAYS - 1)

    db = FakeLargeCapDB(
        [candidate],
        existing_suggestions=[{
            'id': 1, 'watchlist_id': 1, 'suggestion_date': date.today().isoformat(),
            'score': seeded_score, 'target_sell_price': seeded_target, 'pattern_name': None,
        }],
    )
    summary = generate_large_cap_bonus_pick(db, pick_date=later_pick_date)
    assert summary['created'] == []
    assert len(summary['skipped_duplicates']) == 1


def test_pick_outside_the_repeat_window_is_resent_even_if_unchanged():
    candidate = _golden_candidate(1, 'STABLE')
    baseline = FakeLargeCapDB([candidate])
    generate_large_cap_bonus_pick(baseline)
    seeded_score = baseline.suggestions[0]['score']
    seeded_target = baseline.suggestions[0]['target_sell_price']

    later_pick_date = date.today() + timedelta(days=LARGE_CAP_BONUS_REPEAT_WINDOW_DAYS + 1)

    db = FakeLargeCapDB(
        [candidate],
        existing_suggestions=[{
            'id': 1, 'watchlist_id': 1, 'suggestion_date': date.today().isoformat(),
            'score': seeded_score, 'target_sell_price': seeded_target, 'pattern_name': None,
        }],
    )
    summary = generate_large_cap_bonus_pick(db, pick_date=later_pick_date)
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


def test_get_large_cap_bonus_suggestion_by_id_returns_the_matching_row():
    db = _FakeByIdDB([
        {'suggestion_id': 1, 'symbol': 'GOODCO', 'watchlist_id': 10, 'buy_price': 412},
        {'suggestion_id': 2, 'symbol': 'OTHERCO', 'watchlist_id': 11, 'buy_price': 200},
    ])
    row = get_large_cap_bonus_suggestion_by_id(db, 2)
    assert row['symbol'] == 'OTHERCO'
    assert row['watchlist_id'] == 11


def test_get_large_cap_bonus_suggestion_by_id_returns_none_when_not_found():
    db = _FakeByIdDB([{'suggestion_id': 1, 'symbol': 'GOODCO', 'watchlist_id': 10, 'buy_price': 412}])
    assert get_large_cap_bonus_suggestion_by_id(db, 999) is None
