"""Pure price-history analysis for the company detail page -- day change,
52-week high/low, per-parameter trend notes, an inline price sparkline, and
a transparent historical-pattern backtest. No DB access here; app.py's
stocks_company_detail route fetches the raw rows and passes them in.

The backtest is explicitly NOT a prediction of future price -- it reports
what actually happened, historically, after similar RSI conditions for
THIS stock specifically, with the sample size always shown. A pattern seen
twice means far less than one seen twenty times; this never hides that."""
from utils.indicator_engine import calculate_rsi

RSI_ZONES = (
    ('oversold', lambda rsi: rsi < 40),
    ('neutral', lambda rsi: 40 <= rsi <= 65),
    ('overbought', lambda rsi: rsi > 65),
)


def rsi_zone(rsi):
    """Same 40/65 boundary the suggestion engine's hard filter already
    uses (see suggestion_engine.RSI_MIN/RSI_MAX) -- keeps "neutral" here
    meaning the same thing it means everywhere else in this app."""
    if rsi is None:
        return None
    for name, predicate in RSI_ZONES:
        if predicate(rsi):
            return name
    return None


def compute_day_change(closes_desc):
    """closes_desc: closing prices ordered most-recent-first (as returned
    by ORDER BY trade_date DESC). Returns {'latest_close', 'previous_close',
    'change_amount', 'change_pct'}, or None if there's fewer than 2 days
    of history or the previous close was 0 (can't compute a % change)."""
    if len(closes_desc) < 2 or closes_desc[0] is None or closes_desc[1] is None:
        return None
    latest, previous = closes_desc[0], closes_desc[1]
    if previous == 0:
        return None
    return {
        'latest_close': latest,
        'previous_close': previous,
        'change_amount': round(latest - previous, 2),
        'change_pct': round((latest - previous) / previous * 100, 2),
    }


def compute_52_week_range(highs, lows):
    """highs/lows: daily high/low values, any order, None entries ignored.
    Returns (week52_high, week52_low) -- either side is None if no data
    was available for it."""
    valid_highs = [h for h in highs if h is not None]
    valid_lows = [l for l in lows if l is not None]
    return (
        max(valid_highs) if valid_highs else None,
        min(valid_lows) if valid_lows else None,
    )


def trend_note(current, previous):
    """'Increasing' / 'Decreasing' / 'Unchanged' comparing current against
    the prior available snapshot, or None if either is missing. Generic --
    used for every fundamental and technical parameter that has a prior
    value to compare against, not just holdings."""
    if current is None or previous is None:
        return None
    if current > previous:
        return 'Increasing'
    if current < previous:
        return 'Decreasing'
    return 'Unchanged'


def build_price_sparkline_svg(closes_oldest_first, width=560, height=120, padding=8):
    """A self-contained inline <svg> polyline for the given closing prices
    (oldest-first) -- no JS, no external charting library. Green if the
    period ended higher than it started, red otherwise. aria-hidden since
    this is a supplementary visual only -- the actual trend information
    (period return, high/low) belongs in real adjacent text, not encoded
    only in a shape a screen reader can't read. Returns None if there are
    fewer than 2 usable points (nothing to draw a line between)."""
    points = [c for c in closes_oldest_first if c is not None]
    if len(points) < 2:
        return None

    lo, hi = min(points), max(points)
    span = hi - lo or 1  # a perfectly flat line would otherwise divide by zero

    n = len(points)
    coords = []
    for i, price in enumerate(points):
        x = padding + (i / (n - 1)) * (width - 2 * padding)
        y = padding + (1 - (price - lo) / span) * (height - 2 * padding)
        coords.append(f'{x:.1f},{y:.1f}')

    line_color = '#22c55e' if points[-1] >= points[0] else '#ef4444'
    polyline = ' '.join(coords)

    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" '
        f'role="img" aria-hidden="true" xmlns="http://www.w3.org/2000/svg">'
        f'<polyline points="{polyline}" fill="none" stroke="{line_color}" stroke-width="2" '
        f'stroke-linejoin="round" stroke-linecap="round"/>'
        f'</svg>'
    )


def backtest_rsi_zone_outcomes(closes_oldest_first, current_rsi, forward_windows=(5, 10, 20), rsi_period=14):
    """Transparent historical backtest, NOT a forecast: for every past day
    in this stock's own closing-price history where its (retrospectively
    computed) RSI fell in the same zone current_rsi is in right now,
    records the actual subsequent price return over each of
    forward_windows trading days. Reports the average return and % of
    occurrences that were positive, per window -- always alongside the
    sample size, since a pattern seen twice means far less than one seen
    twenty times. This describes what already happened, historically,
    under similar conditions for this specific stock -- it is not a claim
    about what will happen next.

    closes_oldest_first: full available closing-price history, oldest
    first. Returns None if current_rsi can't be zoned, or there isn't
    enough history to compute even one historical comparison point."""
    zone = rsi_zone(current_rsi)
    if zone is None:
        return None

    min_days_needed = rsi_period + 1
    if len(closes_oldest_first) < min_days_needed + 1:
        return None

    outcomes = {window: [] for window in forward_windows}
    matched_occurrences = 0

    # i is the index of the "as of" day being evaluated retrospectively --
    # needs min_days_needed prior closes to compute that day's RSI, and up
    # to `window` future closes to measure each outcome. The final index
    # (today) is excluded -- comparing today's RSI zone to itself isn't a
    # historical occurrence.
    for i in range(min_days_needed, len(closes_oldest_first) - 1):
        window_prices = closes_oldest_first[i - min_days_needed:i + 1]
        historical_rsi = calculate_rsi(window_prices, period=rsi_period)
        if historical_rsi is None or rsi_zone(historical_rsi) != zone:
            continue

        matched_occurrences += 1
        base_price = closes_oldest_first[i]
        if not base_price:
            continue
        for window in forward_windows:
            future_index = i + window
            if future_index < len(closes_oldest_first):
                future_price = closes_oldest_first[future_index]
                if future_price is not None:
                    outcomes[window].append((future_price - base_price) / base_price * 100)

    if matched_occurrences == 0:
        return None

    summary = {}
    for window in forward_windows:
        returns = outcomes[window]
        if not returns:
            summary[window] = None
            continue
        summary[window] = {
            'sample_size': len(returns),
            'avg_return_pct': round(sum(returns) / len(returns), 2),
            'pct_positive': round(100 * sum(1 for r in returns if r > 0) / len(returns), 1),
        }

    return {'zone': zone, 'rsi': current_rsi, 'matched_occurrences': matched_occurrences, 'outcomes': summary}
