from datetime import date

from stoqbell.utils.suggestion_engine import compute_tracker_row_stats


def test_open_position_with_price_between_stop_and_target():
    stats = compute_tracker_row_stats(
        buy_price=100, target_sell_price=110, stop_loss_price=95, latest_price=104,
        suggestion_date=date(2026, 8, 1), today=date(2026, 8, 6),
    )
    assert stats == {'days_elapsed': 5, 'pct_change': 4.0, 'outcome': 'open'}


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
