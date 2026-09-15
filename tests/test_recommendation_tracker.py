from datetime import date

from stoqbell.utils.suggestion_engine import (
    attach_volume_trend_at_suggestion,
    compute_outcome_stats,
    compute_tracker_row_stats,
)


def test_open_position_with_price_between_stop_and_target():
    stats = compute_tracker_row_stats(
        buy_price=100, target_sell_price=110, stop_loss_price=95, latest_price=104,
        suggestion_date=date(2026, 8, 1), today=date(2026, 8, 6),
    )
    assert stats == {
        'days_elapsed': 5, 'pct_change': 4.0, 'outcome': 'open',
        'target_hit_date': None, 'days_to_target_hit': None,
    }


def test_target_hit_when_latest_price_at_or_above_target():
    stats = compute_tracker_row_stats(
        buy_price=100, target_sell_price=110, stop_loss_price=95, latest_price=110,
        suggestion_date=date(2026, 8, 1), today=date(2026, 8, 10),
    )
    assert stats['outcome'] == 'target_hit'
    assert stats['pct_change'] == 10.0


def test_stop_loss_hit_when_latest_price_at_or_below_stop():
    stats = compute_tracker_row_stats(
        buy_price=100, target_sell_price=110, stop_loss_price=95, latest_price=93,
        suggestion_date=date(2026, 8, 1), today=date(2026, 8, 3),
    )
    assert stats['outcome'] == 'stop_loss_hit'
    assert stats['pct_change'] == -7.0


def test_target_hit_takes_priority_over_stop_loss_check():
    # A degenerate case (target <= stop) should never realistically happen,
    # but if it did, hitting target should still read as the good outcome,
    # not the bad one -- target is checked first.
    stats = compute_tracker_row_stats(
        buy_price=100, target_sell_price=90, stop_loss_price=95, latest_price=92,
        suggestion_date=date(2026, 8, 1), today=date(2026, 8, 3),
    )
    assert stats['outcome'] == 'target_hit'


def test_unknown_outcome_when_no_price_has_synced_yet():
    stats = compute_tracker_row_stats(
        buy_price=100, target_sell_price=110, stop_loss_price=95, latest_price=None,
        suggestion_date=date(2026, 8, 1), today=date(2026, 8, 3),
    )
    assert stats['outcome'] == 'unknown'
    assert stats['pct_change'] is None


def test_pct_change_none_when_buy_price_missing():
    stats = compute_tracker_row_stats(
        buy_price=None, target_sell_price=110, stop_loss_price=95, latest_price=104,
        suggestion_date=date(2026, 8, 1), today=date(2026, 8, 3),
    )
    assert stats['pct_change'] is None


def test_days_elapsed_never_negative():
    # today before suggestion_date shouldn't happen in practice, but must
    # not produce a nonsensical negative day count either.
    stats = compute_tracker_row_stats(
        buy_price=100, target_sell_price=110, stop_loss_price=95, latest_price=104,
        suggestion_date=date(2026, 8, 10), today=date(2026, 8, 1),
    )
    assert stats['days_elapsed'] == 0


def test_suggestion_date_accepts_iso_string():
    stats = compute_tracker_row_stats(
        buy_price=100, target_sell_price=110, stop_loss_price=95, latest_price=104,
        suggestion_date='2026-08-01', today=date(2026, 8, 6),
    )
    assert stats['days_elapsed'] == 5


def test_defaults_today_to_the_real_current_date():
    stats = compute_tracker_row_stats(
        buy_price=100, target_sell_price=110, stop_loss_price=95, latest_price=104,
        suggestion_date=date.today(),
    )
    assert stats['days_elapsed'] == 0


def test_days_to_target_hit_computed_from_target_hit_date():
    stats = compute_tracker_row_stats(
        buy_price=100, target_sell_price=110, stop_loss_price=95, latest_price=110,
        suggestion_date=date(2026, 8, 1), today=date(2026, 8, 20),
        target_hit_date=date(2026, 8, 9),
    )
    assert stats['target_hit_date'] == date(2026, 8, 9)
    assert stats['days_to_target_hit'] == 8


def test_target_hit_date_accepts_iso_string():
    stats = compute_tracker_row_stats(
        buy_price=100, target_sell_price=110, stop_loss_price=95, latest_price=110,
        suggestion_date=date(2026, 8, 1), today=date(2026, 8, 20),
        target_hit_date='2026-08-09',
    )
    assert stats['target_hit_date'] == date(2026, 8, 9)
    assert stats['days_to_target_hit'] == 8


# --- compute_outcome_stats ---

def _row(outcome, pct_change=None, nns_tier=None, volume_trend_at_suggestion=None):
    return {
        'outcome': outcome, 'pct_change': pct_change,
        'nns_tier': nns_tier, 'volume_trend_at_suggestion': volume_trend_at_suggestion,
    }


def test_overall_win_rate_only_counts_closed_trades():
    rows = [
        _row('target_hit', pct_change=5.0),
        _row('stop_loss_hit', pct_change=-3.0),
        _row('stop_loss_hit', pct_change=-3.0),
        _row('open', pct_change=1.5),  # not closed -- excluded from win_rate_pct
        _row('unknown'),  # no price synced yet -- excluded from win_rate_pct and avg_pct_change
    ]
    stats = compute_outcome_stats(rows)
    overall = stats['overall']
    assert overall['count'] == 5
    assert overall['closed_count'] == 3
    assert overall['open_count'] == 1
    assert overall['wins'] == 1
    assert overall['losses'] == 2
    assert overall['win_rate_pct'] == round(1 / 3 * 100, 1)
    # avg_pct_change includes every row WITH a pct_change, open positions too
    assert overall['avg_pct_change'] == round((5.0 - 3.0 - 3.0 + 1.5) / 4, 2)


def test_win_rate_none_when_nothing_has_closed_yet():
    rows = [_row('open', pct_change=2.0), _row('unknown')]
    stats = compute_outcome_stats(rows)
    assert stats['overall']['win_rate_pct'] is None
    assert stats['overall']['closed_count'] == 0


def test_avg_pct_change_none_when_no_row_has_a_price():
    rows = [_row('unknown'), _row('unknown')]
    stats = compute_outcome_stats(rows)
    assert stats['overall']['avg_pct_change'] is None


def test_by_nns_tier_breakdown_only_includes_tiers_present():
    rows = [
        _row('target_hit', pct_change=5.0, nns_tier='silver'),
        _row('stop_loss_hit', pct_change=-4.0, nns_tier='silver'),
        _row('stop_loss_hit', pct_change=-3.0, nns_tier='bronze'),
    ]
    stats = compute_outcome_stats(rows)
    assert set(stats['by_nns_tier'].keys()) == {'silver', 'bronze'}
    assert stats['by_nns_tier']['silver']['count'] == 2
    assert stats['by_nns_tier']['silver']['win_rate_pct'] == 50.0
    assert stats['by_nns_tier']['bronze']['win_rate_pct'] == 0.0
    assert 'golden' not in stats['by_nns_tier']  # no golden-tier rows in this sample


def test_by_volume_trend_breakdown_matches_the_actual_backtest_shape():
    # Same directional finding as the real backtest that led to restoring
    # volume-confirmation in is_suggestion_eligible: confirming-volume
    # picks should show a materially better win rate than diverging ones.
    rows = [
        _row('target_hit', pct_change=3.7, volume_trend_at_suggestion='confirming'),
        _row('stop_loss_hit', pct_change=-3.4, volume_trend_at_suggestion='confirming'),
        _row('stop_loss_hit', pct_change=-8.2, volume_trend_at_suggestion='confirming'),
        _row('target_hit', pct_change=9.9, volume_trend_at_suggestion='diverging'),
        _row('stop_loss_hit', pct_change=-3.1, volume_trend_at_suggestion='diverging'),
        _row('stop_loss_hit', pct_change=-6.2, volume_trend_at_suggestion='diverging'),
        _row('stop_loss_hit', pct_change=-4.1, volume_trend_at_suggestion='diverging'),
    ]
    stats = compute_outcome_stats(rows)
    confirming = stats['by_volume_trend']['confirming']
    diverging = stats['by_volume_trend']['diverging']
    assert confirming['win_rate_pct'] > diverging['win_rate_pct']


# --- attach_volume_trend_at_suggestion ---

class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeIndicatorDB:
    """Stands in for SupabaseDB across attach_volume_trend_at_suggestion's
    one batched stock_indicators lookup."""

    def __init__(self, indicator_rows):
        self.indicator_rows = indicator_rows

    def execute(self, sql, params=None):
        assert sql.strip().startswith('SELECT watchlist_id, calc_date, volume_trend FROM stock_indicators')
        return _FakeCursor(self.indicator_rows)


def test_attach_volume_trend_matches_on_watchlist_id_and_exact_date():
    db = _FakeIndicatorDB([
        {'watchlist_id': 1, 'calc_date': '2026-09-01', 'volume_trend': 'confirming'},
        {'watchlist_id': 1, 'calc_date': '2026-09-02', 'volume_trend': 'diverging'},
        {'watchlist_id': 2, 'calc_date': '2026-09-01', 'volume_trend': 'diverging'},
    ])
    rows = [
        {'watchlist_id': 1, 'suggestion_date': '2026-09-01'},
        {'watchlist_id': 1, 'suggestion_date': '2026-09-02'},
        {'watchlist_id': 2, 'suggestion_date': '2026-09-01'},
    ]
    result = attach_volume_trend_at_suggestion(db, rows)
    assert result[0]['volume_trend_at_suggestion'] == 'confirming'
    assert result[1]['volume_trend_at_suggestion'] == 'diverging'
    assert result[2]['volume_trend_at_suggestion'] == 'diverging'


def test_attach_volume_trend_none_when_no_indicator_snapshot_for_that_date():
    db = _FakeIndicatorDB([{'watchlist_id': 1, 'calc_date': '2026-09-01', 'volume_trend': 'confirming'}])
    rows = [{'watchlist_id': 1, 'suggestion_date': '2026-08-15'}]  # no snapshot that far back
    result = attach_volume_trend_at_suggestion(db, rows)
    assert result[0]['volume_trend_at_suggestion'] is None


def test_attach_volume_trend_no_query_when_no_rows_have_a_watchlist_id():
    db = _FakeIndicatorDB([])  # would raise via the execute() assert if queried
    result = attach_volume_trend_at_suggestion(db, [])
    assert result == []
