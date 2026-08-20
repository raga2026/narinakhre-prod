from datetime import date, timedelta

from utils.suggestion_engine import (
    HOLDING_PERIOD_DAYS,
    NNS_SCORE_CHANGE_THRESHOLD,
    SUGGESTION_REPEAT_WINDOW_DAYS,
    TARGET_PRICE_CHANGE_THRESHOLD_PCT,
    TOP_N_SUGGESTIONS,
    _build_rationale,
    _fetch_candidates,
    _is_genuine_change,
    find_pending_target_hit_suggestions,
    generate_daily_suggestions,
    get_suggestion_by_id,
    get_top_stocks,
    is_suggestion_eligible,
    mark_suggestions_target_hit,
    passes_hard_filters,
    score_candidates,
    select_top_suggestions,
)

GOOD_INDICATORS = {'cross_status': 'golden_cross', 'volume_trend': 'confirming', 'rsi_14': 50}

# Comfortably clears NNS_BRONZE_MIN (4.0) on every sub-score by default --
# individual tests override specific fields to compare candidates against
# each other or push one below the bronze floor.
GOOD_FUNDAMENTALS = {
    'pe_ratio': 20, 'peg_ratio': 0.3, 'opm_pct': 32, 'roce_pct': 20, 'roa_pct': 12,
    'quarterly_profit_growth_pct': 20, 'quarterly_revenue_growth_pct': 20,
    'price_to_book': 4,
}


def _candidate(watchlist_id, symbol, **overrides):
    row = {
        'watchlist_id': watchlist_id, 'symbol': symbol, 'exchange': 'NSE',
        'latest_close': 100.0, 'fundamental_tier': 'golden',
        **GOOD_FUNDAMENTALS,
        **GOOD_INDICATORS,
    }
    row.update(overrides)
    return row


class _SqlCapturingDB:
    """Records every (sql, params) passed to execute() without simulating
    any real query result -- used only to verify _fetch_candidates builds
    the right SQL/params for its optional market_cap_tier filter, not to
    exercise any business logic (see FakeSuggestionDB below for that)."""

    def __init__(self):
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((' '.join(sql.split()), params or ()))
        return _EmptyCursor()


class _EmptyCursor:
    def fetchall(self):
        return []

    def fetchone(self):
        return None


def test_fetch_candidates_with_no_tier_filter_unchanged_from_before():
    # market_cap_tier=None (every caller before this param existed) must
    # produce the exact same WHERE clause -- no "AND w.market_cap_tier ="
    # filter fragment at all (market_cap_tier is still SELECTed as a plain
    # column, that's expected) -- so generate_daily_suggestions' candidate
    # pool is untouched by this addition.
    db = _SqlCapturingDB()
    _fetch_candidates(db)
    sql, params = db.calls[0]
    assert 'AND w.market_cap_tier = ?' not in sql
    assert len(params) == 2  # today, fundamentals_cutoff -- no tier param appended


def test_fetch_candidates_with_large_cap_tier_filters_in_sql():
    db = _SqlCapturingDB()
    _fetch_candidates(db, market_cap_tier='large_cap')
    sql, params = db.calls[0]
    assert 'AND w.market_cap_tier = ?' in sql
    assert params[-1] == 'large_cap'


def test_scoring_picks_the_objectively_better_candidate():
    # BETTER is strictly better on every metric: lower PEG, higher growth,
    # higher OPM, higher ROCE. Must score higher regardless of order. WORSE
    # still clears the NNS_BRONZE_MIN floor -- weaker, not disqualified.
    worse = _candidate(1, 'WORSE', peg_ratio=0.9, quarterly_profit_growth_pct=11, opm_pct=26, roce_pct=10, roa_pct=5)
    better = _candidate(2, 'BETTER', peg_ratio=0.1, quarterly_profit_growth_pct=30, opm_pct=40, roce_pct=25, roa_pct=15)

    scored = score_candidates([worse, better])

    assert scored[0][0]['symbol'] == 'BETTER'
    assert scored[0][1] > scored[1][1]


def test_rsi_outside_range_lowers_score_but_no_longer_excludes_outright():
    # RSI moved from a hard pass/fail gate to just one of NNS Score's ten
    # sub-scores (rsi_position) -- a golden-cross candidate with terrible
    # RSI now ranks BEHIND a similar one with ideal RSI, instead of being
    # excluded from consideration entirely (see is_suggestion_eligible).
    overbought = _candidate(1, 'OVERBOUGHT', rsi_14=85)
    ideal_rsi = _candidate(2, 'IDEALRSI', rsi_14=52.5)

    assert passes_hard_filters(overbought) is False  # still fails the OLD, now-unused hard filter
    top = select_top_suggestions([overbought], top_n=5)
    assert [c['symbol'] for c, _ in top] == ['OVERBOUGHT']  # included on its own

    ranked = score_candidates([overbought, ideal_rsi])
    assert [c['symbol'] for c, _ in ranked] == ['IDEALRSI', 'OVERBOUGHT']  # still ranks worse


def test_non_golden_cross_is_excluded_regardless_of_nns_score():
    # cross_status is the one thing that's still a hard, non-negotiable
    # gate -- see is_suggestion_eligible.
    death_cross = _candidate(1, 'DEATHX', cross_status='death_cross')
    no_trend = _candidate(2, 'FLATX', cross_status='no_clear_trend')

    assert select_top_suggestions([death_cross]) == []
    assert select_top_suggestions([no_trend]) == []


def test_diverging_volume_no_longer_excludes_a_golden_cross_candidate():
    # volume_trend was part of the old three-way hard filter -- it's not
    # part of is_suggestion_eligible at all anymore (this is the exact
    # combination that was producing zero suggestions for days: golden
    # cross without 'confirming' volume used to be excluded outright).
    diverging_volume = _candidate(1, 'DIVERGE', volume_trend='diverging')
    top = select_top_suggestions([diverging_volume])
    assert [c['symbol'] for c, _ in top] == ['DIVERGE']


def test_silver_candidate_preferred_over_a_bronze_one_when_both_available():
    silver = _candidate(1, 'SILVERCO')  # GOOD_FUNDAMENTALS defaults alone already land in silver (6.0-8.0)
    bronze = _candidate(2, 'BRONZECO', roce_pct=-1, roa_pct=-1)  # fails only ROCE/ROA -- bronze-eligible

    top = select_top_suggestions([bronze, silver], top_n=1)

    assert len(top) == 1
    assert top[0][0]['symbol'] == 'SILVERCO'
    from utils.nns_score import nns_tier
    assert nns_tier(top[0][1]) == 'silver'


def test_bronze_candidate_used_when_no_silver_or_better_is_available():
    bronze_only = _candidate(1, 'BRONZECO', roce_pct=-1, roa_pct=-1)

    top = select_top_suggestions([bronze_only], top_n=1)

    assert len(top) == 1
    assert top[0][0]['symbol'] == 'BRONZECO'
    from utils.nns_score import nns_tier
    assert nns_tier(top[0][1]) == 'bronze'


def test_rationale_flags_bronze_tier_with_extra_caution():
    candidate = _candidate(1, 'BRONZECO')
    bronze_rationale = _build_rationale(candidate, tier='bronze')
    silver_rationale = _build_rationale(candidate, tier='silver')
    golden_rationale = _build_rationale(candidate, tier='golden')

    assert 'extra caution' in bronze_rationale.lower()
    assert 'extra caution' not in silver_rationale.lower()
    assert 'extra caution' not in golden_rationale.lower()


def test_rationale_still_mentions_golden_cross_without_tier():
    candidate = _candidate(1, 'GOODCO')
    assert 'Golden cross' in _build_rationale(candidate)


def test_hard_filters_check_cross_status_and_volume_trend_too():
    death_cross = _candidate(1, 'DEATHX', cross_status='death_cross')
    diverging_volume = _candidate(2, 'DIVERGE', volume_trend='diverging')

    assert passes_hard_filters(death_cross) is False
    assert passes_hard_filters(diverging_volume) is False


def test_below_bronze_floor_is_excluded_even_if_hard_filters_pass():
    # Passes cross_status/volume_trend/RSI, but every fundamental is at
    # rock bottom -- shouldn't be suggested just to fill a quota.
    weak = _candidate(
        1, 'WEAK', pe_ratio=None, peg_ratio=5.0, opm_pct=0, roce_pct=0, roa_pct=0,
        quarterly_profit_growth_pct=0, quarterly_revenue_growth_pct=0, price_to_book=999,
    )
    assert passes_hard_filters(weak) is True

    assert select_top_suggestions([weak]) == []
    assert score_candidates([weak]) == []


def test_top_n_selection_respects_top_n_suggestions_with_more_candidates():
    candidates = [
        _candidate(i, f'SYM{i}', peg_ratio=max(0.05, 0.5 - i * 0.05), quarterly_profit_growth_pct=15 + i,
                   opm_pct=25 + i, roce_pct=15 + i, roa_pct=8 + i)
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
    replicate the real multi-table JOIN in Python. price_history is
    {watchlist_id: [closes, oldest-first]} -- defaults to empty per
    watchlist_id, which is far too short for any pattern to be detected
    (see compute_suggestion_pricing's fallback), so existing tests that
    don't care about pattern pricing keep working unchanged."""

    def __init__(self, candidate_rows, existing_suggestions=None, price_history=None):
        self.candidate_rows = candidate_rows
        self.suggestions = existing_suggestions or []  # list of dicts: watchlist_id, status, suggestion_date
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
            return FakeCursor([{'close': c} for c in reversed(closes)])  # DESC, like the real query

        if normalized.startswith('SELECT universe_id, promoter_holding_pct, fii_holding_pct, snapshot_date'):
            return FakeCursor([])  # no candidate in these tests sets universe_id

        if normalized.startswith('SELECT score, target_sell_price, pattern_name FROM stock_suggestions'):
            watchlist_id, cutoff, today = params
            matches = sorted(
                (s for s in self.suggestions
                 if s['watchlist_id'] == watchlist_id and cutoff <= s['suggestion_date'] < today),
                key=lambda s: s['suggestion_date'], reverse=True
            )
            return FakeCursor(matches[:1])

        if normalized.startswith('INSERT INTO stock_suggestions'):
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
            # Mirrors the real ON CONFLICT (watchlist_id, suggestion_date) DO
            # UPDATE -- a second call for the same stock on the same day
            # updates that row in place rather than duplicating it.
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

        if normalized.startswith('SELECT id FROM stock_suggestions WHERE watchlist_id=? AND suggestion_date=?'):
            watchlist_id, suggestion_date = params
            matches = [s for s in self.suggestions if s['watchlist_id'] == watchlist_id and s['suggestion_date'] == suggestion_date]
            return FakeCursor(matches[-1:])

        raise AssertionError(f'Unexpected SQL in test: {sql}')

    def commit(self):
        pass


def test_only_one_suggestion_created_per_day_even_with_many_qualifying_candidates():
    # Exactly one Pick of the Day, never a full digest of every candidate
    # that clears the bar.
    candidates = [_candidate(i, f'SYM{i}') for i in range(5)]
    db = FakeSuggestionDB(candidates)

    summary = generate_daily_suggestions(db)

    assert len(summary['created']) == TOP_N_SUGGESTIONS == 1


def test_same_day_rerun_upserts_the_same_row_not_a_duplicate():
    candidates = [_candidate(1, 'STABLE')]
    db = FakeSuggestionDB(candidates)

    first = generate_daily_suggestions(db)
    assert len(first['created']) == 1

    second = generate_daily_suggestions(db)
    assert len(second['created']) == 1
    assert second['created'][0]['symbol'] == 'STABLE'
    assert len(db.suggestions) == 1  # same day -- upserts the same row, doesn't duplicate it


def test_unchanged_pick_within_the_repeat_window_is_skipped():
    candidate = _candidate(1, 'STABLE')

    # Capture what this candidate would deterministically score/price
    # today, so the "existing" row seeded below matches it exactly (no
    # genuine change).
    baseline = FakeSuggestionDB([candidate])
    generate_daily_suggestions(baseline)
    seeded_score = baseline.suggestions[0]['score']
    seeded_target = baseline.suggestions[0]['target_sell_price']

    within_window = (date.today() - timedelta(days=SUGGESTION_REPEAT_WINDOW_DAYS - 1)).isoformat()
    db = FakeSuggestionDB([candidate], existing_suggestions=[{
        'id': 99, 'watchlist_id': 1, 'suggestion_date': within_window,
        'score': seeded_score, 'target_sell_price': seeded_target, 'pattern_name': None,
        'status': 'pending',
    }])

    summary = generate_daily_suggestions(db)

    assert summary['created'] == []
    assert len(summary['skipped_duplicates']) == 1
    assert summary['skipped_duplicates'][0]['symbol'] == 'STABLE'
    assert len(db.suggestions) == 1  # only the seeded row, nothing new added


def test_genuinely_changed_score_is_resent_within_the_repeat_window():
    candidate = _candidate(1, 'MOVER')
    baseline = FakeSuggestionDB([candidate])
    generate_daily_suggestions(baseline)
    seeded_score = baseline.suggestions[0]['score']
    seeded_target = baseline.suggestions[0]['target_sell_price']

    within_window = (date.today() - timedelta(days=5)).isoformat()
    db = FakeSuggestionDB([candidate], existing_suggestions=[{
        'id': 99, 'watchlist_id': 1, 'suggestion_date': within_window,
        # Old score far enough from today's to count as a genuine change.
        'score': seeded_score - NNS_SCORE_CHANGE_THRESHOLD - 0.5, 'target_sell_price': seeded_target,
        'pattern_name': None, 'status': 'pending',
    }])

    summary = generate_daily_suggestions(db)

    assert len(summary['created']) == 1
    assert summary['created'][0]['symbol'] == 'MOVER'


def test_top_candidate_on_cooldown_falls_through_to_the_next_best():
    top = _candidate(1, 'TOPCO')
    second = _candidate(2, 'SECONDCO', peg_ratio=0.9, quarterly_profit_growth_pct=11, opm_pct=26, roce_pct=10, roa_pct=5)

    baseline = FakeSuggestionDB([top])
    generate_daily_suggestions(baseline)
    seeded_score = baseline.suggestions[0]['score']
    seeded_target = baseline.suggestions[0]['target_sell_price']

    within_window = (date.today() - timedelta(days=2)).isoformat()
    db = FakeSuggestionDB([top, second], existing_suggestions=[{
        'id': 99, 'watchlist_id': 1, 'suggestion_date': within_window,
        'score': seeded_score, 'target_sell_price': seeded_target, 'pattern_name': None, 'status': 'pending',
    }])

    summary = generate_daily_suggestions(db)

    assert len(summary['created']) == 1
    assert summary['created'][0]['symbol'] == 'SECONDCO'
    assert any(s['symbol'] == 'TOPCO' for s in summary['skipped_duplicates'])


def test_is_genuine_change_pure_function():
    existing = {'score': 7.0, 'target_sell_price': 100.0, 'pattern_name': None}
    assert _is_genuine_change(existing, 7.0, 100.0) is False
    assert _is_genuine_change(existing, 7.0 + NNS_SCORE_CHANGE_THRESHOLD, 100.0) is True
    moved_target = 100.0 * (1 + TARGET_PRICE_CHANGE_THRESHOLD_PCT / 100 + 0.01)
    assert _is_genuine_change(existing, 7.0, moved_target) is True


def test_new_suggestion_created_when_no_open_one_exists():
    candidates = [_candidate(1, 'FRESH')]
    db = FakeSuggestionDB(candidates)

    summary = generate_daily_suggestions(db)

    assert len(summary['created']) == 1
    assert summary['created'][0]['symbol'] == 'FRESH'
    assert len(db.suggestions) == 1


def test_nns_score_and_tier_are_stored_on_the_suggestion():
    candidates = [_candidate(1, 'SCORED')]
    db = FakeSuggestionDB(candidates)

    generate_daily_suggestions(db)

    suggestion = db.suggestions[0]
    assert suggestion['nns_score'] is not None
    assert suggestion['nns_score'] >= 4.0  # cleared the bronze floor, or it wouldn't have been suggested
    assert suggestion['nns_tier'] in ('golden', 'silver', 'bronze')


def _ramp_series(waypoints):
    series = []
    for (d1, p1), (d2, p2) in zip(waypoints, waypoints[1:]):
        for day in range(d1, d2):
            frac = (day - d1) / (d2 - d1)
            series.append(p1 + frac * (p2 - p1))
    series.append(waypoints[-1][1])
    return series


CONFIRMED_HS_BOTTOM_SERIES = _ramp_series([
    (0, 100), (20, 70), (35, 90), (55, 50), (70, 92), (90, 68), (110, 110),
])


def test_no_pattern_in_short_history_falls_back_to_flat_percentages():
    candidates = [_candidate(1, 'FLATPCT', latest_close=100.0)]
    db = FakeSuggestionDB(candidates)  # no price_history entry -- too short for any pattern

    generate_daily_suggestions(db)

    suggestion = db.suggestions[0]
    assert suggestion['pattern_name'] is None
    assert suggestion['pattern_note'] is None
    assert suggestion['target_sell_price'] == 105.0
    assert suggestion['stop_loss_price'] == 97.0


def test_confirmed_chart_pattern_drives_pricing_and_note_instead_of_flat_percentages():
    latest_close = CONFIRMED_HS_BOTTOM_SERIES[-1]
    candidates = [_candidate(1, 'PATTERNED', latest_close=latest_close)]
    db = FakeSuggestionDB(candidates, price_history={1: CONFIRMED_HS_BOTTOM_SERIES})

    summary = generate_daily_suggestions(db)

    suggestion = db.suggestions[0]
    assert suggestion['pattern_name'] == 'head_and_shoulders_bottom'
    assert suggestion['pattern_note'] is not None
    assert 'no reliable way to predict exact timing' in suggestion['pattern_note']
    # Target came from the pattern's measured-move formula, not the flat +5%.
    assert suggestion['target_sell_price'] != round(latest_close * 1.05, 2)
    assert summary['created'][0]['pattern_name'] == 'head_and_shoulders_bottom'


def test_watchlist_fundamental_tier_and_opm_carry_through_to_the_suggestion():
    # A silver-tier watchlist stock should insert as a silver suggestion,
    # with opm_at_suggestion snapshotted alongside it -- same reasoning as
    # pe_at_suggestion/peg_at_suggestion already snapshotting their values.
    candidates = [_candidate(1, 'SILVERSTOCK', fundamental_tier='silver')]
    db = FakeSuggestionDB(candidates)

    generate_daily_suggestions(db)

    assert len(db.suggestions) == 1
    assert db.suggestions[0]['fundamental_tier'] == 'silver'
    assert db.suggestions[0]['opm_at_suggestion'] == 32

# Multi-horizon projected price (1 month/6 months/1 year) is tested in
# tests/test_price_pattern.py (utils.price_pattern.compute_projection_targets),
# since it lives in that module now -- see that function's docstring.


# --- get_suggestion_by_id ----------------------------------------------------

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


def test_get_suggestion_by_id_returns_the_matching_row():
    db = _FakeByIdDB([
        {'suggestion_id': 1, 'symbol': 'GOODCO', 'watchlist_id': 10, 'buy_price': 412},
        {'suggestion_id': 2, 'symbol': 'OTHERCO', 'watchlist_id': 11, 'buy_price': 200},
    ])
    row = get_suggestion_by_id(db, 2)
    assert row['symbol'] == 'OTHERCO'
    assert row['watchlist_id'] == 11


def test_get_suggestion_by_id_returns_none_when_not_found():
    db = _FakeByIdDB([{'suggestion_id': 1, 'symbol': 'GOODCO', 'watchlist_id': 10, 'buy_price': 412}])
    assert get_suggestion_by_id(db, 999) is None


# --- find_pending_target_hit_suggestions / mark_suggestions_target_hit -----

class _FakeMultiCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeTargetHitDB:
    """rows: list of dicts with id, status, suggestion_date, target_sell_price,
    latest_price (plus whatever else the caller wants on the row -- the
    fake's own SELECT filtering only looks at those four)."""

    def __init__(self, rows):
        self.rows = rows

    def execute(self, sql, params=None):
        params = params or ()
        normalized = ' '.join(sql.split())

        if normalized.startswith('SELECT s.id, w.id AS watchlist_id, w.symbol, w.exchange, w.name AS company_name,'):
            matches = [
                r for r in self.rows
                if r['status'] == 'pending'
                and r.get('target_sell_price') is not None
                and r.get('latest_price') is not None
                and r['latest_price'] >= r['target_sell_price']
            ]
            matches.sort(key=lambda r: (r['suggestion_date'], r['id']))
            return _FakeMultiCursor(matches)

        if normalized.startswith('UPDATE stock_suggestions SET'):
            ids = set(params)
            for r in self.rows:
                if r['id'] in ids:
                    r['status'] = 'target_hit'
            return _FakeMultiCursor([])

        raise AssertionError(f'Unexpected SQL in test: {sql}')

    def commit(self):
        pass


def _target_hit_row(**overrides):
    row = {
        'id': 1, 'symbol': 'GOODCO', 'exchange': 'NSE', 'company_name': 'Good Co Ltd',
        'watchlist_id': 10, 'status': 'pending', 'suggestion_date': '2026-08-01',
        'buy_price': 100, 'target_sell_price': 110,
        'latest_price': 112, 'latest_price_date': '2026-08-10',
    }
    row.update(overrides)
    return row


def test_finds_a_pending_suggestion_whose_target_was_reached():
    db = _FakeTargetHitDB([_target_hit_row()])
    hits = find_pending_target_hit_suggestions(db)
    assert len(hits) == 1
    assert hits[0]['symbol'] == 'GOODCO'


def test_does_not_find_a_pending_suggestion_still_below_target():
    db = _FakeTargetHitDB([_target_hit_row(latest_price=105, target_sell_price=110)])
    assert find_pending_target_hit_suggestions(db) == []


def test_does_not_re_find_a_suggestion_already_marked_target_hit():
    # The whole point of checking status='pending' -- a suggestion already
    # notified about (or otherwise no longer pending) must never show up
    # again even if price stays above target indefinitely.
    db = _FakeTargetHitDB([_target_hit_row(status='target_hit')])
    assert find_pending_target_hit_suggestions(db) == []


def test_suggestion_with_no_target_price_is_never_a_hit():
    db = _FakeTargetHitDB([_target_hit_row(target_sell_price=None)])
    assert find_pending_target_hit_suggestions(db) == []


def test_suggestion_with_no_synced_price_yet_is_never_a_hit():
    db = _FakeTargetHitDB([_target_hit_row(latest_price=None)])
    assert find_pending_target_hit_suggestions(db) == []


def test_multiple_hits_are_sorted_oldest_suggestion_first():
    db = _FakeTargetHitDB([
        _target_hit_row(id=1, symbol='NEWER', suggestion_date='2026-08-10'),
        _target_hit_row(id=2, symbol='OLDER', suggestion_date='2026-07-01'),
    ])
    hits = find_pending_target_hit_suggestions(db)
    assert [h['symbol'] for h in hits] == ['OLDER', 'NEWER']


def test_mark_suggestions_target_hit_updates_status():
    db = _FakeTargetHitDB([_target_hit_row(id=1), _target_hit_row(id=2, symbol='OTHERCO')])
    mark_suggestions_target_hit(db, [1])
    assert db.rows[0]['status'] == 'target_hit'
    assert db.rows[1]['status'] == 'pending'  # untouched


def test_mark_suggestions_target_hit_prevents_rediscovery():
    db = _FakeTargetHitDB([_target_hit_row(id=1)])
    hits = find_pending_target_hit_suggestions(db)
    assert len(hits) == 1

    mark_suggestions_target_hit(db, [h['id'] for h in hits])

    assert find_pending_target_hit_suggestions(db) == []


def test_mark_suggestions_target_hit_is_a_no_op_on_empty_list():
    db = _FakeTargetHitDB([_target_hit_row()])
    mark_suggestions_target_hit(db, [])  # must not raise or touch the DB
    assert db.rows[0]['status'] == 'pending'


def _golden_scoring_candidate(watchlist_id, symbol, **overrides):
    # Same fixture shape test_starters_engine.py's _golden_candidate uses
    # (clears NNS_GOLDEN_MIN, 8.0+) -- duplicated here rather than imported
    # to avoid a test-module-to-test-module import for one small fixture.
    return _candidate(
        watchlist_id, symbol,
        peg_ratio=0.1, quarterly_profit_growth_pct=35, quarterly_revenue_growth_pct=30,
        opm_pct=45, roce_pct=30, roa_pct=20, rsi_14=52.5, pe_ratio=12, price_to_book=2,
        **overrides
    )


def test_get_top_stocks_returns_only_golden_and_silver_tier_highest_first():
    golden = _golden_scoring_candidate(1, 'GOLDCO', company_name='Gold Company Ltd')
    silver = _candidate(2, 'SILVERCO', company_name='Silver Company Ltd')  # plain fixture scores ~6.9 (silver)
    bronze = _candidate(3, 'BRONZECO', peg_ratio=1.5, quarterly_profit_growth_pct=1,
                         quarterly_revenue_growth_pct=1, opm_pct=5, roce_pct=2, roa_pct=1)
    db = FakeSuggestionDB([bronze, silver, golden])

    top = get_top_stocks(db)

    symbols = [t['symbol'] for t in top]
    assert 'GOLDCO' in symbols
    assert 'SILVERCO' in symbols
    assert 'BRONZECO' not in symbols
    assert symbols[0] == 'GOLDCO'  # highest-scored first
    assert top[0]['nns_tier'] == 'golden'
    assert top[0]['company_name'] == 'Gold Company Ltd'
    # The raw NNS Score number itself must never be exposed here -- only
    # the tier badge, same policy as the rest of the viewer-facing surface.
    assert 'nns_score' not in top[0]


def test_get_top_stocks_falls_back_to_symbol_when_no_company_name():
    golden = _golden_scoring_candidate(1, 'GOLDCO')  # no company_name key at all
    db = FakeSuggestionDB([golden])

    top = get_top_stocks(db)

    assert top[0]['company_name'] == 'GOLDCO'


def test_get_top_stocks_respects_the_limit_param():
    candidates = [_golden_scoring_candidate(i, f'SYM{i}') for i in range(8)]
    db = FakeSuggestionDB(candidates)

    top = get_top_stocks(db, limit=3)

    assert len(top) == 3


def test_get_top_stocks_limit_none_returns_the_full_uncapped_list():
    candidates = [_golden_scoring_candidate(i, f'SYM{i}') for i in range(8)]
    db = FakeSuggestionDB(candidates)

    top = get_top_stocks(db, limit=None)

    assert len(top) == 8


def test_get_top_stocks_empty_when_nothing_clears_silver():
    bronze_only = _candidate(1, 'WEAKCO', peg_ratio=1.5, quarterly_profit_growth_pct=1,
                              quarterly_revenue_growth_pct=1, opm_pct=5, roce_pct=2, roa_pct=1)
    db = FakeSuggestionDB([bronze_only])

    assert get_top_stocks(db) == []


