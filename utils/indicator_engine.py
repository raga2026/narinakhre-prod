"""Pure technical-indicator calculation for Nari Nakhre Stocks. No DB access
here -- see utils/stock_indicators.py for how this gets applied over
stock_daily_data and stored in stock_indicators."""

DEFAULT_MA_WINDOWS = (5, 21, 50, 200)


def calculate_moving_averages(prices, windows=DEFAULT_MA_WINDOWS):
    """Standard simple moving average over each window. prices must be
    ordered oldest-first (prices[-1] is the most recent close), matching
    how the rest of this app reads stock_daily_data. Returns
    {window: value_or_None} -- a window with fewer than `window` days of
    history returns None rather than computing a misleading average off
    partial data (never fake a 200-day MA from 40 days of prices)."""
    result = {}
    for window in windows:
        if len(prices) < window:
            result[window] = None
        else:
            result[window] = round(sum(prices[-window:]) / window, 2)
    return result


def calculate_rsi(prices, period=14):
    """Standard RSI using Wilder's smoothing (the standard reference
    formula): seed the average gain/loss from the first `period` price
    changes, then smooth each subsequent change in with
    ((prev_avg * (period-1)) + current) / period. prices ordered
    oldest-first. Returns None if there isn't at least period+1 days of
    history (need `period` price changes to seed the first average)."""
    if len(prices) < period + 1:
        return None

    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains = [max(d, 0) for d in deltas]
    losses = [max(-d, 0) for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def detect_cross_status(ma21, ma50, ma200):
    """'golden_cross' if ma21 > ma50 > ma200 (short-term trend above
    medium above long -- bullish alignment); 'death_cross' if the reverse
    (ma200 > ma50 > ma21 -- bearish alignment); else 'no_clear_trend'.
    Returns None -- not the string 'insufficient_data', which is reserved
    for volume_trend's enum -- if any of the three is None, since
    stock_indicators.cross_status only allows the three strings above or
    NULL (see its CHECK constraint)."""
    if ma21 is None or ma50 is None or ma200 is None:
        return None
    if ma21 > ma50 > ma200:
        return 'golden_cross'
    if ma200 > ma50 > ma21:
        return 'death_cross'
    return 'no_clear_trend'


def detect_volume_trend(recent_volumes, price_trend_direction):
    """Compares recent trading volume's own trend against
    price_trend_direction ('up'/'down', typically derived by the caller
    from the ma21-vs-ma50 relationship). 'confirming' when volume moves the
    same direction as price -- both rising, or both falling; per the domain
    expert's note that "growth without volume is not sustainable," a price
    move without matching volume support is flagged 'diverging' instead.
    Volume's own direction is read by comparing the average of the more
    recent half of recent_volumes against the average of the earlier half.
    Returns 'insufficient_data' if there are fewer than 10 volume readings
    (need at least 5 vs 5 to compare), or if price_trend_direction isn't
    'up' or 'down' (nothing to confirm or diverge from when price itself
    has no clear direction yet)."""
    if not recent_volumes or len(recent_volumes) < 10:
        return 'insufficient_data'
    if price_trend_direction not in ('up', 'down'):
        return 'insufficient_data'

    half = len(recent_volumes) // 2
    earlier_avg = sum(recent_volumes[:half]) / half
    recent_avg = sum(recent_volumes[-half:]) / half

    if recent_avg > earlier_avg:
        volume_direction = 'up'
    elif recent_avg < earlier_avg:
        volume_direction = 'down'
    else:
        volume_direction = 'flat'

    return 'confirming' if volume_direction == price_trend_direction else 'diverging'
