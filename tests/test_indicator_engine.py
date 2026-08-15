from utils.indicator_engine import (
    calculate_moving_averages,
    calculate_rsi,
    detect_cross_status,
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
