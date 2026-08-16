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


# --- Rounding pattern (rounding bottom / rounding top) ----------------------
#
# A rounding bottom (aka saucer) is a classic technical-analysis pattern: a
# gradual, smooth U-shaped decline-then-recovery in price over an extended
# period, considered a bullish reversal signal once price breaks back above
# where the decline started (the "neckline"). A rounding top is the bearish
# mirror image -- a smooth, gradual rise-then-decline. Both are well
# documented as developing over WEEKS TO MONTHS with no fixed schedule --
# there is no reliable way to say in advance how long one takes to
# complete, and this deliberately does not invent a number. What can be
# described honestly is the CURRENT shape and phase of the price series.

ROUNDING_MIN_DAYS = 40
# How well a quadratic curve must fit the price series (R^2, 0-1) before
# it's called a genuine rounding shape rather than just noise that happens
# to have a slight curve.
ROUNDING_FIT_THRESHOLD = 0.5


def _det3(m):
    return (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
            - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
            + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))


def _fit_quadratic(xs, ys):
    """Least-squares fit of y = a*x^2 + b*x + c via the normal equations,
    solved directly with Cramer's rule on the resulting 3x3 system -- no
    numpy/scipy dependency, consistent with the rest of this app's
    hand-rolled indicator math (see indicator_engine.py). Returns
    (a, b, c), or None if the system is singular (degenerate input, e.g.
    fewer than 3 distinct x values)."""
    n = len(xs)
    s0, s1 = n, sum(xs)
    s2 = sum(x * x for x in xs)
    s3 = sum(x ** 3 for x in xs)
    s4 = sum(x ** 4 for x in xs)
    t0 = sum(ys)
    t1 = sum(x * y for x, y in zip(xs, ys))
    t2 = sum(x * x * y for x, y in zip(xs, ys))

    coefficient_matrix = [[s4, s3, s2], [s3, s2, s1], [s2, s1, s0]]
    det = _det3(coefficient_matrix)
    if abs(det) < 1e-9:
        return None

    a = _det3([[t2, s3, s2], [t1, s2, s1], [t0, s1, s0]]) / det
    b = _det3([[s4, t2, s2], [s3, t1, s1], [s2, t0, s0]]) / det
    c = _det3([[s4, s3, t2], [s3, s2, t1], [s2, s1, t0]]) / det
    return a, b, c


def _r_squared(xs, ys, a, b, c):
    y_mean = sum(ys) / len(ys)
    ss_tot = sum((y - y_mean) ** 2 for y in ys)
    if ss_tot == 0:
        return 1.0
    ss_res = sum((y - (a * x * x + b * x + c)) ** 2 for x, y in zip(xs, ys))
    return max(0.0, 1 - ss_res / ss_tot)


def detect_rounding_pattern(closes_oldest_first):
    """Fits a quadratic curve to the available closing-price history and
    checks whether its shape resembles a rounding bottom (bullish, U
    shaped) or rounding top (bearish, inverted-U). Describes the CURRENT
    shape and phase only -- not a prediction of what happens next or when.

    Needs at least ROUNDING_MIN_DAYS of history (a rounding pattern is
    inherently a long-term formation; anything shorter is too noisy to
    read this way). Returns None below that, if the fit is too poor
    (R^2 below ROUNDING_FIT_THRESHOLD) to call it a genuine curve rather
    than noise, or if the curve is degenerate (no fit / no meaningful
    curvature).

    Returns {'shape': 'rounding_bottom'|'rounding_top', 'fit_quality'
    (R^2, 0-1), 'days_analyzed', 'phase', 'neckline_price' (price at the
    start of the analyzed window), 'current_price', 'above_neckline'}."""
    points = [c for c in closes_oldest_first if c is not None]
    if len(points) < ROUNDING_MIN_DAYS:
        return None

    xs = list(range(len(points)))
    fit = _fit_quadratic(xs, points)
    if fit is None:
        return None
    a, b, c = fit

    # Guards against floating-point noise on genuinely (near-)linear data
    # producing a spuriously tiny nonzero 'a' -- R^2 alone doesn't catch
    # this, since a near-zero quadratic term still fits a line just as
    # well as the line itself. Requires the curvature to actually account
    # for a meaningful share (>=1%) of the observed price range, not just
    # be technically nonzero.
    vertex_x = -b / (2 * a) if a else 0
    price_range = max(points) - min(points)
    quadratic_contribution = abs(a) * max((xs[0] - vertex_x) ** 2, (xs[-1] - vertex_x) ** 2)
    if price_range == 0 or quadratic_contribution < 0.01 * price_range:
        return None

    r2 = _r_squared(xs, points, a, b, c)
    if r2 < ROUNDING_FIT_THRESHOLD:
        return None

    shape = 'rounding_bottom' if a > 0 else 'rounding_top'
    last_x = xs[-1]
    neckline_price = points[0]
    current_price = points[-1]

    # Where "now" sits relative to the fitted vertex (the base of a bottom,
    # or the peak of a top), in day-units rather than a ratio -- a ratio
    # breaks down when the vertex falls before day 0 (a negative x).
    tolerance_days = max(5, round(0.15 * len(points)))
    if last_x < vertex_x - tolerance_days:
        stage = 'not yet reached'
    elif last_x > vertex_x + tolerance_days:
        stage = 'past'
    else:
        stage = 'at'

    if shape == 'rounding_bottom':
        phase = {
            'not yet reached': 'still declining -- the base has not formed yet within this window',
            'at': 'flattening out near the base',
            'past': 'recovering off the base',
        }[stage]
        above_neckline = current_price >= neckline_price
    else:
        phase = {
            'not yet reached': 'still rising -- the top has not formed yet within this window',
            'at': 'flattening out near the top',
            'past': 'declining off the top',
        }[stage]
        above_neckline = current_price <= neckline_price  # "broken down" below the neckline, for a top

    return {
        'shape': shape,
        'fit_quality': round(r2, 2),
        'days_analyzed': len(points),
        'phase': phase,
        'neckline_price': neckline_price,
        'current_price': current_price,
        'above_neckline': above_neckline,
    }
