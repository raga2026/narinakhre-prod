from utils.indicator_engine import (
    CONSOLIDATION_BREAKOUT_DAYS,
    CONSOLIDATION_RANGE_MAX_PCT,
    calculate_moving_averages,
    calculate_rsi,
    detect_consolidation,
    detect_cross_status,
    detect_rounding_bottom,
    detect_volume_trend,
)


def test_moving_averages_return_none_for_insufficient_history_not_a_wrong_number():
    # Only 40 days of prices -- nowhere near enough for a 200-day MA, and
    # not quite enough for 50 either. Must be None, not a number computed
    # off partial data.
    prices = [float(i) for i in range(1, 41)]

    mas = calculate_moving_averages(prices, windows=(21, 50, 200))

    assert mas[50] is None
    assert mas[200] is None
    assert mas[21] is not None  # 40 days is enough for a 21-day window


def test_moving_average_21_day_value_is_correct_once_enough_history_exists():
    # 21 identical values of 10 -> MA21 must be exactly 10, no rounding drift.
    prices = [10.0] * 21

    mas = calculate_moving_averages(prices, windows=(21,))

    assert mas[21] == 10.0


def test_rsi_returns_none_below_period_plus_one_days_of_history():
    prices = [10.0] * 10  # period=14 needs at least 15 prices

    assert calculate_rsi(prices, period=14) is None


def test_rsi_matches_hand_computed_reference_with_period_5():
    # prices (oldest first): 10, 11, 12, 11, 13, 14
    # deltas: +1, +1, -1, +2, +1
    # gains:   1,  1,  0,  2,  1  -> avg_gain = 5/5 = 1.0
    # losses:  0,  0,  1,  0,  0  -> avg_loss = 1/5 = 0.2
    # (exactly period=5 deltas, so no further Wilder smoothing iterations)
    # rs = 1.0 / 0.2 = 5.0
    # rsi = 100 - (100 / (1 + 5)) = 100 - 16.6667 = 83.33
    prices = [10, 11, 12, 11, 13, 14]

    rsi = calculate_rsi(prices, period=5)

    assert rsi == 83.33


def test_rsi_is_100_when_every_change_is_a_gain():
    prices = [10, 11, 12, 13, 14, 15]  # monotonically increasing, period=5

    assert calculate_rsi(prices, period=5) == 100.0


def test_rsi_is_0_when_every_change_is_a_loss():
    prices = [15, 14, 13, 12, 11, 10]  # monotonically decreasing, period=5

    assert calculate_rsi(prices, period=5) == 0.0


def test_cross_status_golden_cross():
    assert detect_cross_status(ma21=110, ma50=100, ma200=90) == 'golden_cross'


def test_cross_status_death_cross():
    assert detect_cross_status(ma21=90, ma50=100, ma200=110) == 'death_cross'


def test_cross_status_no_clear_trend():
    # ma50 is not between the other two -- neither a clean golden nor death alignment.
    assert detect_cross_status(ma21=100, ma50=90, ma200=95) == 'no_clear_trend'


def test_cross_status_none_when_any_ma_missing():
    assert detect_cross_status(ma21=110, ma50=100, ma200=None) is None
    assert detect_cross_status(ma21=None, ma50=100, ma200=90) is None


def test_volume_trend_confirming_when_volume_and_price_both_rising():
    recent_volumes = [100, 100, 100, 100, 100, 200, 200, 200, 200, 200]

    assert detect_volume_trend(recent_volumes, price_trend_direction='up') == 'confirming'


def test_volume_trend_diverging_when_price_rises_without_volume_support():
    recent_volumes = [200, 200, 200, 200, 200, 100, 100, 100, 100, 100]  # volume falling

    assert detect_volume_trend(recent_volumes, price_trend_direction='up') == 'diverging'


def test_volume_trend_insufficient_data_below_ten_readings():
    assert detect_volume_trend([100, 200, 150], price_trend_direction='up') == 'insufficient_data'


def test_volume_trend_insufficient_data_when_price_direction_unclear():
    recent_volumes = [100, 100, 100, 100, 100, 200, 200, 200, 200, 200]

    assert detect_volume_trend(recent_volumes, price_trend_direction='flat') == 'insufficient_data'
    assert detect_volume_trend(recent_volumes, price_trend_direction=None) == 'insufficient_data'


# --- detect_rounding_bottom --------------------------------------------------

def _u_shaped_series():
    """~900-day hand-constructed rounding bottom: a clear decline (200 ->
    ~155), a flattening base (mild oscillation around 155), and a clear
    recovery (155 -> ~215) -- each phase 300 days, comfortably over
    min_days=750 in total."""
    decline = [200 - i * 0.15 for i in range(300)]
    base = [155 + (i % 3) * 0.2 for i in range(300)]
    recovery = [155 + i * 0.2 for i in range(300)]
    return decline + base + recovery


def test_rounding_bottom_returns_zero_below_min_days():
    assert detect_rounding_bottom([100.0] * 749, min_days=750) == 0


def test_rounding_bottom_returns_zero_at_exactly_min_days_minus_one():
    # Boundary check -- min_days itself must be sufficient, one less must not.
    series = _u_shaped_series()
    assert detect_rounding_bottom(series[:749], min_days=750) == 0


def test_genuine_u_shape_scores_high():
    score = detect_rounding_bottom(_u_shaped_series())
    assert score >= 70


def test_straight_uptrend_scores_low():
    # A monotonic uptrend trivially satisfies "late average above mid
    # average" everywhere along its length -- the whole point of gating
    # the recovery/basing components behind a genuine prior decline (see
    # the function's own docstring) is that this must NOT score high.
    uptrend = [100 + i * 0.1 for i in range(900)]
    assert detect_rounding_bottom(uptrend) < 20


def test_straight_downtrend_scores_low():
    downtrend = [300 - i * 0.2 for i in range(900)]
    assert detect_rounding_bottom(downtrend) < 40


def test_straight_uptrend_and_downtrend_score_well_below_a_genuine_u_shape():
    u_score = detect_rounding_bottom(_u_shaped_series())
    uptrend_score = detect_rounding_bottom([100 + i * 0.1 for i in range(900)])
    downtrend_score = detect_rounding_bottom([300 - i * 0.2 for i in range(900)])

    assert uptrend_score < u_score - 40
    assert downtrend_score < u_score - 40


def test_flat_line_scores_zero():
    assert detect_rounding_bottom([150.0] * 900) == 0


def test_rounding_bottom_score_is_bounded_0_to_100():
    for series in (_u_shaped_series(), [100 + i * 0.1 for i in range(900)], [150.0] * 900):
        score = detect_rounding_bottom(series)
        assert 0 <= score <= 100


# --- detect_consolidation ----------------------------------------------------

def _tight_base(days, low=100.0, spread=1.5, volume=10000):
    """A tight, quiet base -- prices oscillate within `spread` and volume
    stays flat, both comfortably inside CONSOLIDATION_RANGE_MAX_PCT."""
    prices = [low + (i % 4) * (spread / 4) for i in range(days)]
    volumes = [volume] * days
    return prices, volumes


def test_consolidation_returns_zero_below_window_days():
    prices, volumes = _tight_base(74)
    assert detect_consolidation(prices, volumes, window_days=75) == 0


def test_tight_base_with_confirmed_breakout_scores_high():
    base_days = 75 - CONSOLIDATION_BREAKOUT_DAYS
    base_prices, base_volumes = _tight_base(base_days)
    breakout_prices = [max(base_prices) + 2 + i * 0.5 for i in range(CONSOLIDATION_BREAKOUT_DAYS)]
    breakout_volumes = [30000] * CONSOLIDATION_BREAKOUT_DAYS  # well above base's 10000 average

    score = detect_consolidation(base_prices + breakout_prices, base_volumes + breakout_volumes)

    assert score >= 70


def test_tight_base_with_no_breakout_scores_meaningfully_lower_than_with_one():
    base_days = 75 - CONSOLIDATION_BREAKOUT_DAYS
    base_prices, base_volumes = _tight_base(base_days)

    # No breakout: stays inside the base's own range, ordinary volume.
    no_breakout_prices = base_prices + [base_prices[-1]] * CONSOLIDATION_BREAKOUT_DAYS
    no_breakout_volumes = base_volumes + [9000] * CONSOLIDATION_BREAKOUT_DAYS
    no_breakout_score = detect_consolidation(no_breakout_prices, no_breakout_volumes)

    # Confirmed breakout: same base, clears the high with a volume spike.
    breakout_prices = base_prices + [max(base_prices) + 2 + i * 0.5 for i in range(CONSOLIDATION_BREAKOUT_DAYS)]
    breakout_volumes = base_volumes + [30000] * CONSOLIDATION_BREAKOUT_DAYS
    breakout_score = detect_consolidation(breakout_prices, breakout_volumes)

    assert breakout_score - no_breakout_score >= 30
    assert no_breakout_score < breakout_score


def test_wide_range_base_scores_lower_than_a_tight_one_even_with_the_same_breakout():
    base_days = 75 - CONSOLIDATION_BREAKOUT_DAYS
    tight_prices, tight_volumes = _tight_base(base_days, spread=1.0)
    # A base whose range is right at CONSOLIDATION_RANGE_MAX_PCT (10%) of
    # its average price -- earns ~0 tightness credit, unlike the tight one.
    wide_prices = [100.0 + (i % 2) * 10.0 for i in range(base_days)]
    wide_volumes = [10000] * base_days

    breakout_tail_prices = lambda base: [max(base) + 2 + i * 0.5 for i in range(CONSOLIDATION_BREAKOUT_DAYS)]
    breakout_tail_volumes = [30000] * CONSOLIDATION_BREAKOUT_DAYS

    tight_score = detect_consolidation(
        tight_prices + breakout_tail_prices(tight_prices), tight_volumes + breakout_tail_volumes
    )
    wide_score = detect_consolidation(
        wide_prices + breakout_tail_prices(wide_prices), wide_volumes + breakout_tail_volumes
    )

    assert wide_score < tight_score


def test_consolidation_score_is_bounded_0_to_100():
    base_days = 75 - CONSOLIDATION_BREAKOUT_DAYS
    base_prices, base_volumes = _tight_base(base_days)
    breakout_prices = [max(base_prices) + 50 + i for i in range(CONSOLIDATION_BREAKOUT_DAYS)]
    breakout_volumes = [1000000] * CONSOLIDATION_BREAKOUT_DAYS

    score = detect_consolidation(base_prices + breakout_prices, base_volumes + breakout_volumes)

    assert 0 <= score <= 100
