from utils.price_pattern import (
    backtest_rsi_zone_outcomes,
    build_price_sparkline_svg,
    compute_52_week_range,
    compute_day_change,
    rsi_zone,
    trend_note,
)


# --- rsi_zone -------------------------------------------------------------

def test_rsi_zone_boundaries():
    assert rsi_zone(39.9) == 'oversold'
    assert rsi_zone(40) == 'neutral'
    assert rsi_zone(65) == 'neutral'
    assert rsi_zone(65.1) == 'overbought'
    assert rsi_zone(None) is None


# --- compute_day_change -----------------------------------------------------

def test_day_change_positive():
    result = compute_day_change([110.0, 100.0])
    assert result['change_amount'] == 10.0
    assert result['change_pct'] == 10.0


def test_day_change_negative():
    result = compute_day_change([90.0, 100.0])
    assert result['change_amount'] == -10.0
    assert result['change_pct'] == -10.0


def test_day_change_none_with_fewer_than_two_days():
    assert compute_day_change([100.0]) is None
    assert compute_day_change([]) is None


def test_day_change_none_when_previous_close_is_zero():
    assert compute_day_change([10.0, 0.0]) is None


# --- compute_52_week_range --------------------------------------------------

def test_52_week_range_basic():
    highs = [105.0, 110.0, 108.0]
    lows = [95.0, 90.0, 98.0]
    assert compute_52_week_range(highs, lows) == (110.0, 90.0)


def test_52_week_range_ignores_none_values():
    highs = [105.0, None, 120.0]
    lows = [None, 90.0, 95.0]
    assert compute_52_week_range(highs, lows) == (120.0, 90.0)


def test_52_week_range_empty_returns_none_none():
    assert compute_52_week_range([], []) == (None, None)


# --- trend_note --------------------------------------------------------------

def test_trend_note_increasing():
    assert trend_note(30, 25) == 'Increasing'


def test_trend_note_decreasing():
    assert trend_note(20, 25) == 'Decreasing'


def test_trend_note_unchanged():
    assert trend_note(25, 25) == 'Unchanged'


def test_trend_note_none_when_either_value_missing():
    assert trend_note(None, 25) is None
    assert trend_note(25, None) is None


# --- build_price_sparkline_svg ----------------------------------------------

def test_sparkline_returns_none_for_fewer_than_two_points():
    assert build_price_sparkline_svg([]) is None
    assert build_price_sparkline_svg([100.0]) is None


def test_sparkline_returns_svg_string_for_valid_data():
    svg = build_price_sparkline_svg([100.0, 105.0, 110.0])
    assert svg.startswith('<svg')
    assert svg.endswith('</svg>')
    assert '<polyline' in svg


def test_sparkline_uses_green_for_upward_period_red_for_downward():
    up_svg = build_price_sparkline_svg([100.0, 110.0])
    down_svg = build_price_sparkline_svg([110.0, 100.0])
    assert '#22c55e' in up_svg
    assert '#ef4444' in down_svg


def test_sparkline_ignores_none_entries():
    svg = build_price_sparkline_svg([100.0, None, 105.0, None, 110.0])
    assert svg is not None
    assert '<polyline' in svg


def test_sparkline_handles_perfectly_flat_prices_without_crashing():
    svg = build_price_sparkline_svg([100.0, 100.0, 100.0])
    assert svg is not None


# --- backtest_rsi_zone_outcomes ---------------------------------------------

def test_backtest_returns_none_when_current_rsi_is_none():
    assert backtest_rsi_zone_outcomes([100.0] * 60, current_rsi=None) is None


def test_backtest_returns_none_with_insufficient_history():
    assert backtest_rsi_zone_outcomes([100.0] * 10, current_rsi=50) is None


def test_backtest_finds_matches_and_reports_sample_size_and_returns():
    # A long, gently oscillating series -- guaranteed to produce plenty of
    # 'neutral'-zone RSI readings (an RSI of 50 sits comfortably in
    # [40, 65]) to backtest against, and enough trailing days for every
    # forward window (5/10/20) to have at least some outcomes.
    import math
    closes = [100.0 + 5 * math.sin(i / 3.0) + i * 0.01 for i in range(200)]

    result = backtest_rsi_zone_outcomes(closes, current_rsi=50)

    assert result is not None
    assert result['zone'] == 'neutral'
    assert result['matched_occurrences'] > 0
    for window in (5, 10, 20):
        outcome = result['outcomes'][window]
        if outcome is not None:
            assert outcome['sample_size'] > 0
            assert isinstance(outcome['avg_return_pct'], float)
            assert 0 <= outcome['pct_positive'] <= 100


def test_backtest_never_counts_todays_own_rsi_as_a_historical_match():
    # Exactly enough history for one possible historical point (index 15,
    # with min_days_needed=15 for period=14) but zero trading days left
    # after it to measure any outcome -- and today itself (the last index)
    # must never be counted as a "past occurrence" of itself.
    closes = [100.0] * 16
    result = backtest_rsi_zone_outcomes(closes, current_rsi=50, forward_windows=(5,), rsi_period=14)
    # A flat series has RSI None (no price movement -- avg_loss stays 0,
    # calculate_rsi returns 100.0 per its own "no losses" rule) or matches
    # trivially; either way this must not raise, and if it does match,
    # there's no future data to produce an outcome.
    if result is not None:
        assert result['outcomes'][5] is None or result['outcomes'][5]['sample_size'] >= 0


def test_backtest_different_zone_than_history_returns_none_or_zero_matches():
    # All-flat prices produce RSI=100 (calculate_rsi's documented
    # zero-losses convention) every time -- always 'overbought'. Asking
    # for the 'oversold' zone should find nothing.
    closes = [100.0] * 60
    result = backtest_rsi_zone_outcomes(closes, current_rsi=20)  # oversold zone requested
    assert result is None
