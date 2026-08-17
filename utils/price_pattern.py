"""Pure price-history analysis for the company detail page -- day change,
52-week high/low, per-parameter trend notes, an inline price sparkline, and
a transparent historical-pattern backtest. No DB access here; app.py's
stocks_company_detail route fetches the raw rows and passes them in.

The backtest is explicitly NOT a prediction of future price -- it reports
what actually happened, historically, after similar RSI conditions for
THIS stock specifically, with the sample size always shown. A pattern seen
twice means far less than one seen twenty times; this never hides that."""
import math

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

    vertex_price = a * vertex_x * vertex_x + b * vertex_x + c

    return {
        'shape': shape,
        'fit_quality': round(r2, 2),
        'days_analyzed': len(points),
        'phase': phase,
        'neckline_price': neckline_price,
        'vertex_price': round(vertex_price, 2),
        'current_price': current_price,
        'above_neckline': above_neckline,
    }


# --- Head-and-shoulders / reverse head-and-shoulders -------------------------
#
# A head-and-shoulders TOP is three peaks -- a lower left shoulder, a higher
# head, a lower right shoulder roughly matching the left one -- with a
# "neckline" connecting the two troughs between them; a break below the
# neckline is the classic bearish reversal signal. A head-and-shoulders
# BOTTOM (colloquially "reverse" or "inverse" head-and-shoulders) is the
# mirror image built from troughs instead of peaks, bullish once price
# breaks above the neckline.
#
# The MEASURED-MOVE price target below is a standard, mechanical
# technical-analysis formula (project the head-to-neckline distance an
# equal distance beyond the neckline breakout) -- not something invented
# for this app. See PATTERN_RESEARCH_CONTEXT further down for how often
# that target has actually been reached historically, and how long it took,
# per Thomas Bulkowski's published pattern research (thepatternsite.com) --
# always shown as general historical context for the pattern TYPE, never as
# a claim about this specific stock.

# A point counts as a swing high/low only if it's the most extreme value
# within this many trading days on EACH side -- filters out single-day
# noise while still catching multi-week swings. ~3 weeks each side.
HS_EXTREMA_WINDOW_DAYS = 15

# The two shoulders must be within this fraction of each other (relative to
# the head's distance from the neckline) to count as "roughly symmetric" --
# real shoulders are rarely identical, but wildly mismatched ones aren't a
# head-and-shoulders, just two unrelated peaks either side of a bigger one.
HS_SHOULDER_SYMMETRY_TOLERANCE = 0.5

# The head must clear the higher of the two shoulders by at least this
# fraction of the head-to-neckline distance -- guards against calling three
# nearly-equal peaks a "head and shoulders" just because the middle one is
# a fraction higher.
HS_MIN_HEAD_PROMINENCE = 0.15

HS_MIN_DAYS = 60


def _find_local_extrema(points, window, kind):
    """points: [(index, value), ...]. kind: 'max' finds swing highs, 'min'
    finds swing lows. A point qualifies if it's the strict max/min among
    every point within `window` positions on both sides (using array
    position, not calendar days, but callers pass daily closes so the two
    coincide). Returns the qualifying points in original order."""
    extrema = []
    n = len(points)
    for i in range(n):
        lo = max(0, i - window)
        hi = min(n, i + window + 1)
        neighborhood = [points[j][1] for j in range(lo, hi) if j != i]
        if not neighborhood:
            continue
        value = points[i][1]
        if kind == 'max' and value > max(neighborhood):
            extrema.append(points[i])
        elif kind == 'min' and value < min(neighborhood):
            extrema.append(points[i])
    return extrema


def _neckline_value_at(index, point_a, point_b):
    """Linear interpolation/extrapolation of the line through point_a and
    point_b (each (index, value)), evaluated at `index` -- necklines are
    often sloped, not flat, so this projects the actual line rather than
    averaging the two endpoints."""
    (x1, y1), (x2, y2) = point_a, point_b
    if x2 == x1:
        return (y1 + y2) / 2
    slope = (y2 - y1) / (x2 - x1)
    return y1 + slope * (index - x1)


def detect_head_and_shoulders(closes_oldest_first, kind='top'):
    """closes_oldest_first: closing prices, oldest first, ideally ~900 days
    so a pattern that took months to form is actually visible. kind='top'
    looks for a bearish head-and-shoulders (three peaks); kind='bottom'
    looks for a bullish reverse head-and-shoulders (three troughs).

    Finds swing highs/lows (see _find_local_extrema), takes the LAST three
    (most recent candidate shoulder-head-shoulder), and requires: the head
    is the most extreme of the three, by at least HS_MIN_HEAD_PROMINENCE of
    the head-to-neckline distance; the two shoulders are within
    HS_SHOULDER_SYMMETRY_TOLERANCE of each other. Returns None if there
    isn't enough history, fewer than 3 swing points exist yet, or the most
    recent 3 don't satisfy those checks -- this is a real (if imperfect)
    geometric test, not a guarantee a human chartist would agree, and
    doesn't claim to catch every valid pattern or reject every invalid one.

    neckline is the line through the two troughs (kind='top') or peaks
    (kind='bottom') BETWEEN the shoulders and head, projected to the most
    recent day via _neckline_value_at -- a sloped neckline, not a flat
    average. measured_move_target is the standard mechanical technical-
    analysis formula: project the head-to-neckline distance an equal
    distance beyond the neckline. See PATTERN_RESEARCH_CONTEXT for
    published historical context on how often that target is actually
    reached, and how long it has taken -- general research on the pattern
    TYPE, not a claim about this stock.

    Returns {'kind', 'left_shoulder', 'head', 'right_shoulder',
    'neckline_at_head', 'neckline_at_breakout', 'measured_move_target',
    'current_price', 'breakout_confirmed', 'days_since_head'} -- each of
    left_shoulder/head/right_shoulder is {'index', 'price'}."""
    points = [(i, c) for i, c in enumerate(closes_oldest_first) if c is not None]
    if len(points) < HS_MIN_DAYS:
        return None

    extrema_kind = 'max' if kind == 'top' else 'min'
    extrema = _find_local_extrema(points, HS_EXTREMA_WINDOW_DAYS, extrema_kind)
    if len(extrema) < 3:
        return None

    left_shoulder, head, right_shoulder = extrema[-3], extrema[-2], extrema[-1]
    ls_idx, ls_price = left_shoulder
    h_idx, h_price = head
    rs_idx, rs_price = right_shoulder

    is_head_extreme = (h_price > ls_price and h_price > rs_price) if kind == 'top' \
        else (h_price < ls_price and h_price < rs_price)
    if not is_head_extreme:
        return None

    # Neckline points: the lowest trough (kind='top') / highest peak
    # (kind='bottom') strictly between left_shoulder..head and head..right_shoulder.
    between_ls_head = [p for p in points if ls_idx < p[0] < h_idx]
    between_head_rs = [p for p in points if h_idx < p[0] < rs_idx]
    if not between_ls_head or not between_head_rs:
        return None
    neckline_point_1 = min(between_ls_head, key=lambda p: p[1]) if kind == 'top' \
        else max(between_ls_head, key=lambda p: p[1])
    neckline_point_2 = min(between_head_rs, key=lambda p: p[1]) if kind == 'top' \
        else max(between_head_rs, key=lambda p: p[1])

    neckline_at_head = _neckline_value_at(h_idx, neckline_point_1, neckline_point_2)
    head_to_neckline = abs(h_price - neckline_at_head)
    if head_to_neckline == 0:
        return None

    shoulder_diff = abs(ls_price - rs_price)
    if shoulder_diff > HS_SHOULDER_SYMMETRY_TOLERANCE * head_to_neckline:
        return None

    nearer_shoulder = max(ls_price, rs_price) if kind == 'top' else min(ls_price, rs_price)
    prominence = abs(h_price - nearer_shoulder)
    if prominence < HS_MIN_HEAD_PROMINENCE * head_to_neckline:
        return None

    last_idx, current_price = points[-1]
    neckline_at_breakout = _neckline_value_at(last_idx, neckline_point_1, neckline_point_2)

    if kind == 'top':
        measured_move_target = neckline_at_breakout - head_to_neckline
        breakout_confirmed = current_price < neckline_at_breakout
    else:
        measured_move_target = neckline_at_breakout + head_to_neckline
        breakout_confirmed = current_price > neckline_at_breakout

    return {
        'kind': kind,
        'left_shoulder': {'index': ls_idx, 'price': ls_price},
        'head': {'index': h_idx, 'price': h_price},
        'right_shoulder': {'index': rs_idx, 'price': rs_price},
        'neckline_at_head': round(neckline_at_head, 2),
        'neckline_at_breakout': round(neckline_at_breakout, 2),
        'measured_move_target': round(measured_move_target, 2),
        'current_price': current_price,
        'breakout_confirmed': breakout_confirmed,
        'days_since_head': last_idx - h_idx,
    }


# Published aggregate statistics on how these pattern TYPES have performed
# historically across hundreds of real, past instances at OTHER companies --
# general research context for the pattern, never a claim about this
# specific stock's own future. Primarily Thomas Bulkowski's "Encyclopedia of
# Chart Patterns" (thepatternsite.com), the most widely cited empirical
# study of chart-pattern outcomes in technical-analysis literature; figures
# below are secondary-sourced (aggregated from published summaries of that
# research, not a direct read of the primary tables), and different study
# vintages report somewhat different numbers (e.g. target-hit-rate for
# head-and-shoulders has been reported anywhere from 51% to 83% depending on
# sample/year) -- the more conservative, more recently reported figure is
# used here. Rounding-bottom statistics vary more across sources than
# head-and-shoulders, reflecting a smaller published sample. Always
# presented alongside its source and hit-rate, never as a bare number, so
# it can't be mistaken for a guarantee.
PATTERN_RESEARCH_CONTEXT = {
    'head_and_shoulders_top': {
        'target_hit_rate_pct': 51,
        'directional_hit_rate_pct': 81,
        'typical_move_duration_days': (60, 90),
        'source': "Thomas Bulkowski's Encyclopedia of Chart Patterns (thepatternsite.com) "
                  "-- 431 patterns across 500 stocks, 1991-1996, with later updates",
    },
    'head_and_shoulders_bottom': {
        'target_hit_rate_pct': 51,
        'directional_hit_rate_pct': 95,
        'typical_move_duration_days': (60, 90),
        'source': "Thomas Bulkowski's Encyclopedia of Chart Patterns (thepatternsite.com)",
    },
    'rounding_bottom': {
        # No separately published measured-move/target-hit-rate study for
        # "rounding bottom" by that exact name -- the figures below are
        # Bulkowski's cup-and-handle statistics instead (a rounding bottom
        # is essentially a cup without the handle, and shares the same
        # "rim-to-bottom depth projected from the breakout" measured-move
        # logic; cup-and-handle is the far more heavily studied of the two
        # in published TA research), used here as the closest defensible
        # analogue rather than an invented number.
        'target_hit_rate_pct': 61,
        'directional_hit_rate_pct': 95,
        'typical_move_duration_days': (90, 365),
        'source': "Thomas Bulkowski's Encyclopedia of Chart Patterns, cup-and-handle "
                  "statistics (bull-market patterns) used as the closest published "
                  "analogue for rounding bottoms -- see the note above",
    },
    'rounding_top': {
        'target_hit_rate_pct': None,
        'directional_hit_rate_pct': None,
        'typical_move_duration_days': (90, 365),
        'source': "No separately published reliability figures found for rounding tops "
                  "specifically -- shown for shape/phase context only, never used for a "
                  "price target.",
    },
}


# --- Pattern-based suggestion pricing ----------------------------------------
#
# Ties the detectors above into buy/target/stop-loss pricing for the daily
# suggestion email (see suggestion_engine.py). Only ever applies a pattern
# that is BULLISH and has already CONFIRMED its breakout (price has moved
# through the neckline in the right direction) -- an unconfirmed pattern, a
# bearish one, or a target/stop that doesn't make directional sense (target
# at or below today's price, or stop at or above it) all fall through to
# the plain percentage-based fallback, same as "no pattern found" would.
#
# Deliberately never derives a specific number of days to hold -- see
# PATTERN_RESEARCH_CONTEXT's typical_move_duration_days for the general,
# cited historical range instead. That's shown as research context on the
# pattern TYPE, never as a per-stock forecast of how long THIS suggestion
# will take.

# How far below a bullish pattern's neckline the stop-loss sits -- a small
# buffer below the breakout level, standard TA practice: a "breakout" that
# falls back through its own neckline shortly after is generally treated as
# failed/invalidated, not just noise.
PATTERN_STOP_LOSS_BUFFER_PCT = 0.02


def _pattern_pricing_from_head_and_shoulders(closes_oldest_first, latest_close):
    hs = detect_head_and_shoulders(closes_oldest_first, kind='bottom')
    if not hs or not hs['breakout_confirmed']:
        return None
    target = hs['measured_move_target']
    stop_loss = round(hs['neckline_at_breakout'] * (1 - PATTERN_STOP_LOSS_BUFFER_PCT), 2)
    if target <= latest_close or stop_loss >= latest_close:
        return None
    return {
        'buy_price': latest_close, 'target_sell_price': target, 'stop_loss_price': stop_loss,
        'pattern_name': 'head_and_shoulders_bottom',
        'pattern_detail': hs,
        'pattern_research': PATTERN_RESEARCH_CONTEXT['head_and_shoulders_bottom'],
    }


def _pattern_pricing_from_rounding_bottom(closes_oldest_first, latest_close):
    rounding = detect_rounding_pattern(closes_oldest_first)
    if not rounding or rounding['shape'] != 'rounding_bottom' or not rounding['above_neckline']:
        return None
    cup_depth = rounding['neckline_price'] - rounding['vertex_price']
    if cup_depth <= 0:
        return None
    target = round(rounding['neckline_price'] + cup_depth, 2)
    stop_loss = round(rounding['neckline_price'] * (1 - PATTERN_STOP_LOSS_BUFFER_PCT), 2)
    if target <= latest_close or stop_loss >= latest_close:
        return None
    return {
        'buy_price': latest_close, 'target_sell_price': target, 'stop_loss_price': stop_loss,
        'pattern_name': 'rounding_bottom',
        'pattern_detail': rounding,
        'pattern_research': PATTERN_RESEARCH_CONTEXT['rounding_bottom'],
    }


def compute_suggestion_pricing(closes_oldest_first, latest_close, fallback_target_multiplier, fallback_stop_loss_multiplier):
    """Tries a confirmed reverse head-and-shoulders first, then a confirmed
    rounding bottom, falling back to the plain percentage method
    (fallback_target_multiplier/fallback_stop_loss_multiplier applied to
    latest_close) when neither is found, confirmed, or makes directional
    sense. buy_price is always latest_close either way -- a pattern informs
    the TARGET and STOP, not when to buy; the suggestion itself already
    means "buy now."

    Returns {'buy_price', 'target_sell_price', 'stop_loss_price',
    'pattern_name': None|str, 'pattern_detail': None|dict,
    'pattern_research': None|dict} -- pattern_research is the matching
    PATTERN_RESEARCH_CONTEXT entry when a pattern was used, for the email
    to cite (hit rate, typical duration, source) alongside the price."""
    for finder in (_pattern_pricing_from_head_and_shoulders, _pattern_pricing_from_rounding_bottom):
        result = finder(closes_oldest_first, latest_close)
        if result:
            return result

    return {
        'buy_price': latest_close,
        'target_sell_price': round(latest_close * fallback_target_multiplier, 2),
        'stop_loss_price': round(latest_close * fallback_stop_loss_multiplier, 2),
        'pattern_name': None,
        'pattern_detail': None,
        'pattern_research': None,
    }


# --- Multi-horizon projected price (mid-period / long-term) -----------------
#
# Shown alongside every suggestion (see suggestion_email.py and the viewer
# pages, /stocks/my/suggestions and /stocks/my/history) as a longer-range
# companion to the near-term buy/target/stop-loss. Deliberately NOT a fixed
# calendar grid applied identically to every stock -- each stock gets its
# OWN mid-period/long-term checkpoints, taken from the SAME confirmed chart
# pattern (head-and-shoulders bottom or rounding bottom) that already drove
# its own target_sell_price whenever there is one, since different pattern
# types have genuinely different published typical durations (a
# head-and-shoulders move plays out over ~2-3 months; a rounding bottom over
# anywhere from ~3 months to a year) -- collapsing both onto the same fixed
# checkpoints would misrepresent one or the other. Only stocks with no
# confirmed pattern (the flat-percentage fallback case) fall back to a
# generic ~6-month/~1-year pair, for lack of any stock-specific duration to
# use instead.

# Generic mid-period/long-term checkpoints for a suggestion with no
# confirmed pattern to ground a stock-specific duration in (the flat
# +5%/-3% fallback -- see compute_suggestion_pricing). ~6 months / ~1 year,
# deliberately NOT derived from any per-stock research.
_FALLBACK_MID_PERIOD_DAYS = 182
_FALLBACK_LONG_TERM_DAYS = 365

# The sqrt-of-time scale for the 'extrapolated' (no-pattern) case is
# anchored to this many days -- matches suggestion_engine.HOLDING_PERIOD_DAYS
# (the holding period the flat fallback target is nominally based on). Not
# imported from there directly: this module is lower-level (suggestion_engine
# imports FROM price_pattern, not the other way), so the value is simply
# kept in sync by convention/comment.
_FALLBACK_PROJECTION_BASELINE_DAYS = 10


def _humanize_days(days):
    """'~75 days' -> '~2.5 months', '~365 days' -> '~1 year' -- a duration
    genuinely specific to one stock's own pattern shouldn't be forced into
    a generic 'Month 1 / Month 6' label; this renders whatever the actual
    day count is, in whichever unit reads most naturally at that scale."""
    days = round(days)
    if days < 45:
        return f'~{days} days'
    if days >= 330:
        unit_days, unit_name = 365, 'year'
    else:
        unit_days, unit_name = 30, 'month'
    value = days / unit_days
    rounded = round(value)
    value_str = str(rounded) if abs(value - rounded) < 0.1 else f'{value:.1f}'
    plural = '' if value_str == '1' else 's'
    return f'~{value_str} {unit_name}{plural}'


def compute_projection_targets(buy_price, target_sell_price, pattern_name):
    """Projects price at two checkpoints -- a mid-period one and a
    longer-term one -- each labeled with the ACTUAL duration it represents
    for this specific stock (see _humanize_days), not a fixed calendar
    point shared by every suggestion.

    When pattern_name is a confirmed pattern compute_suggestion_pricing can
    actually produce ('head_and_shoulders_bottom' or 'rounding_bottom'):
    target_sell_price IS that pattern's own measured-move target (see
    _pattern_pricing_from_head_and_shoulders/_pattern_pricing_from_rounding_bottom,
    which set target_sell_price to exactly this). PATTERN_RESEARCH_CONTEXT's
    published typical_move_duration_days=(lo, hi) for that pattern TYPE
    (Bulkowski's research, already cited elsewhere in this module) becomes
    THIS stock's own two checkpoints directly: the midpoint (lo+hi)/2 is
    'mid_period', and hi itself is 'long_term' -- the point by which the
    full move has typically played out historically, so 'long_term' shows
    the target itself; before that, price is assumed to move toward it
    proportionally (scaled by sqrt-of-time, the way an expected price move
    typically scales -- not linearly, and never by compounding a
    short-term rate, which would overshoot wildly at a long horizon).
    Genuinely different pattern types get genuinely different checkpoints
    this way (a head-and-shoulders' ~2-3 months vs a rounding bottom's
    ~3-12 months) rather than being squeezed onto one shared timeline.
    method='pattern' in the result, and 'source'/'directional_hit_rate_pct'
    are carried through from PATTERN_RESEARCH_CONTEXT so callers can cite
    them; a stock's own pattern occasionally implies a duration well past a
    year for a slow-forming rounding bottom -- that's expected, not capped
    to fit within any particular calendar horizon.

    When pattern_name is None or unrecognized (the flat-percentage
    fallback case -- by far the more common one, since a confirmed bullish
    pattern is relatively rare): there is no stock-specific duration
    research to ground this in, so it falls back to a generic ~6-month
    mid-period / ~1-year long-term pair (_FALLBACK_MID_PERIOD_DAYS/
    _FALLBACK_LONG_TERM_DAYS), scaled by sqrt-of-time from the near-term
    target with no cap (unlike the pattern case, there's no researched
    point at which to say the move is "typically" complete, so this keeps
    growing across both checkpoints rather than flattening). method=
    'extrapolated' in the result -- callers should visibly label this
    differently from the pattern-grounded case (see suggestion_email.py),
    since it's a plain mathematical projection, not backed by this stock's
    own detected chart pattern.

    Returns {'mid_period': {'days', 'label', 'price'}, 'long_term':
    {'days', 'label', 'price'}, 'method': 'pattern'|'extrapolated',
    'pattern_name', 'source', 'directional_hit_rate_pct'} --
    source/directional_hit_rate_pct are None under 'extrapolated'. Returns
    {} if buy_price/target_sell_price is missing or non-positive (nothing
    to project from)."""
    if not buy_price or not target_sell_price or buy_price <= 0:
        return {}

    research = PATTERN_RESEARCH_CONTEXT.get(pattern_name) if pattern_name else None
    if research and research.get('typical_move_duration_days'):
        lo_days, hi_days = research['typical_move_duration_days']
        mid_days = (lo_days + hi_days) / 2
        long_days = hi_days
        method = 'pattern'
        source = research.get('source')
        directional_hit_rate_pct = research.get('directional_hit_rate_pct')
    else:
        mid_days = _FALLBACK_MID_PERIOD_DAYS
        long_days = _FALLBACK_LONG_TERM_DAYS
        method = 'extrapolated'
        source = None
        directional_hit_rate_pct = None

    move = target_sell_price - buy_price

    def price_at(days):
        if method == 'pattern' and days >= long_days:
            return round(target_sell_price, 2)
        base_days = long_days if method == 'pattern' else _FALLBACK_PROJECTION_BASELINE_DAYS
        fraction = math.sqrt(days / base_days)
        return round(buy_price + move * fraction, 2)

    return {
        'mid_period': {'days': round(mid_days), 'label': _humanize_days(mid_days), 'price': price_at(mid_days)},
        'long_term': {'days': round(long_days), 'label': _humanize_days(long_days), 'price': price_at(long_days)},
        'method': method,
        'pattern_name': pattern_name if method == 'pattern' else None,
        'source': source,
        'directional_hit_rate_pct': directional_hit_rate_pct,
    }
