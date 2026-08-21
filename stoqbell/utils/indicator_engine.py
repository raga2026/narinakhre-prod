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


# ---------------------------------------------------------------------------
# Pattern-detection scores (rounding bottom, consolidation-with-breakout) --
# entirely new, additive to this module. Nothing above this line is
# modified, called, or depended on differently by anything below it; MA/RSI/
# cross-status/volume-trend keep computing exactly as they already did.
# Both scores are 0-100, and both are deliberately NOT wired into any
# overall/final scoring anywhere yet -- see utils/stock_indicators.py's
# run_indicator_calculation for where these get computed and stored, still
# unused beyond that.
# ---------------------------------------------------------------------------

def _mean(values):
    return sum(values) / len(values)


def _population_stddev(values, mean_value=None):
    """Population standard deviation (divide by n, not n-1) -- used here
    purely to compare which of three segments is "calmest" relative to the
    others, not for any inferential-statistics purpose, so the population/
    sample distinction doesn't matter beyond picking one consistently."""
    if not values:
        return 0.0
    m = mean_value if mean_value is not None else _mean(values)
    variance = sum((v - m) ** 2 for v in values) / len(values)
    return variance ** 0.5


def _fit_quadratic(values):
    """Least-squares fit of y = a*x^2 + b*x + c across values (x normalized
    to [0, 1] across the series, oldest first) -- the standard
    normal-equations approach for a degree-2 polynomial, solved directly
    via Cramer's rule over the resulting 3x3 system (no numpy/scipy
    dependency in this codebase, and this is a fixed-size,
    always-solvable-by-hand system). Returns (a, b, c); a > 0 means the
    fitted parabola opens upward across the WHOLE series (a genuine
    U-shape), a <= 0 means it's flat, opens downward, or is a straight
    line (b dominates, a ~ 0) -- see detect_rounding_bottom, the only
    caller.

    x is deliberately normalized to [0, 1] rather than left as raw 0-based
    indices (0..n-1): with min_days=750+ typical series lengths, raw
    indices make the power sums below (s3, s4) astronomically large, and
    the floating-point cancellation inherent to Cramer's rule then leaves
    `a` with tiny spurious noise (confirmed empirically while building
    this: ~1e-18 for an EXACTLY straight 900-point line) -- enough to
    wrongly trip a naive `a > 0` check on data that isn't curved at all.
    Normalizing keeps every power sum small and well-conditioned regardless
    of series length, which fixes that at the source rather than papering
    over it with an arbitrary epsilon in the caller.

    Returns (0.0, 0.0, 0.0) in the degenerate case where the system has no
    unique solution (fewer than 3 points; not otherwise possible here since
    x always spans the same normalized [0, 1] range)."""
    n = len(values)
    if n < 3:
        return 0.0, 0.0, 0.0

    xs = [i / (n - 1) for i in range(n)]
    s0, s1 = float(n), sum(xs)
    s2 = sum(x * x for x in xs)
    s3 = sum(x ** 3 for x in xs)
    s4 = sum(x ** 4 for x in xs)
    t0 = sum(values)
    t1 = sum(x * y for x, y in zip(xs, values))
    t2 = sum(x * x * y for x, y in zip(xs, values))

    # | s4 s3 s2 | |a|   |t2|
    # | s3 s2 s1 | |b| = |t1|
    # | s2 s1 s0 | |c|   |t0|
    det = s4 * (s2 * s0 - s1 * s1) - s3 * (s3 * s0 - s1 * s2) + s2 * (s3 * s1 - s2 * s2)
    if det == 0:
        return 0.0, 0.0, 0.0

    det_a = t2 * (s2 * s0 - s1 * s1) - s3 * (t1 * s0 - s1 * t0) + s2 * (t1 * s1 - s2 * t0)
    det_b = s4 * (t1 * s0 - t0 * s1) - t2 * (s3 * s0 - s1 * s2) + s2 * (s3 * t0 - t1 * s2)
    det_c = s4 * (s2 * t0 - s1 * t1) - s3 * (s3 * t0 - s2 * t1) + t2 * (s3 * s1 - s2 * s2)

    return det_a / det, det_b / det, det_c / det


# A floating-point-noise guard for _fit_quadratic's `a` coefficient, NOT a
# meaningful curvature threshold -- confirmed empirically that an EXACTLY
# straight or flat series can come back with |a| on the order of 1e-12
# (Cramer's rule cancellation noise), on either side of zero depending on
# the exact data, which a bare `a > 0` check would inconsistently read as
# "curved." Real curvature for an actual price series (see _fit_quadratic's
# x normalized to [0, 1]) lands many orders of magnitude above this.
_CURVATURE_NOISE_EPSILON = 1e-6


def detect_rounding_bottom(prices, min_days=750):
    """Scores how much a ~3-year (min_days, default 750 trading days --
    roughly 252/year x 3) price series looks like a rounding-bottom
    ("saucer") pattern: a decline, a flattening base, and a recovery, with
    genuine upward curvature across the whole series. prices ordered
    oldest-first, same convention as calculate_moving_averages/calculate_rsi.

    Returns 0 immediately if there's fewer than min_days of history --
    this is a bonus/exploratory score, never a hard requirement elsewhere,
    so graceful degrade (0, not None/an exception) keeps every caller
    simple. Also 0 for an empty a/b/c fit (see _fit_quadratic).

    SCORING FORMULA (sums to at most 100; each component's own points are
    the deliberate weighting -- decline+basing+recovery = 85, curvature is
    a 15-point structural confirmation on top):

      1) Decline component, 0-30 points: splits the full series into three
         roughly equal segments (early/mid/late -- late absorbs any
         remainder when the length isn't divisible by 3) and compares
         early segment's average price against mid segment's. A decline of
         20%+ from early to mid earns full 30 points; smaller declines earn
         proportionally less (decline_pct / 20 * 30); no decline (mid >=
         early) earns 0 here.
      2) Basing component, 0-30 points, and 3) Recovery component, 0-25
         points: BOTH gated behind the decline component actually having
         scored above 0 -- i.e. a real early-to-mid decline has to have
         happened at all before "the middle is calm" or "the end recovered
         off the middle" mean anything as a rounding-BOTTOM specifically.
         Without that gate, a plain straight uptrend trivially satisfies
         "late average is above mid average" everywhere along its length
         (recovery with nothing to recover from), and a perfectly straight
         line of any slope has near-identical variance in every
         equal-length segment (a loose "mid is the lowest, ties included"
         comparison would count that as "basing" too) -- both would
         otherwise score misleadingly high despite never actually forming a
         down-flat-up shape. Confirmed against synthetic straight-line
         fixtures while building this (see tests/test_indicator_engine.py).
           - Basing: flat 30 points only if the mid segment's own stddev is
             at least 20% BELOW the smaller of the early/late stddevs
             (mid_std < 0.8 * min(early_std, late_std)) -- a meaningfully
             calmer middle, not just numerically the smallest of three
             near-equal values the way a straight line's segments are.
           - Recovery: same proportional scaling as the decline component,
             late-vs-mid in the opposite direction -- a 20%+ recovery off
             the mid average earns full 25 points.
      4) Curvature component, 0-15 points: flat 15 points if a
         least-squares quadratic fit (_fit_quadratic) across the ENTIRE
         price series has positive leading coefficient (a > 0, opens
         upward -- ruling out a V-shape, which still "recovers" per (3)
         but doesn't curve the same way a genuine saucer does, and ruling
         out a flat or still-declining line). 0 points otherwise."""
    if len(prices) < min_days:
        return 0

    n = len(prices)
    seg_len = n // 3
    early = prices[:seg_len]
    mid = prices[seg_len:2 * seg_len]
    late = prices[2 * seg_len:]

    early_avg, mid_avg, late_avg = _mean(early), _mean(mid), _mean(late)
    early_std = _population_stddev(early, early_avg)
    mid_std = _population_stddev(mid, mid_avg)
    late_std = _population_stddev(late, late_avg)

    score = 0.0

    decline_component = 0.0
    if early_avg > 0:
        decline_pct = (early_avg - mid_avg) / early_avg * 100
        decline_component = min(30.0, max(0.0, decline_pct / 20 * 30))
    score += decline_component

    if decline_component > 0:
        if mid_std < 0.8 * min(early_std, late_std):
            score += 30.0

        if mid_avg > 0:
            recovery_pct = (late_avg - mid_avg) / mid_avg * 100
            score += min(25.0, max(0.0, recovery_pct / 20 * 25))

    a, _b, _c = _fit_quadratic(prices)
    if a > _CURVATURE_NOISE_EPSILON:
        score += 15.0

    return round(min(100.0, max(0.0, score)), 2)


# How many of the most recent days count as the "breakout zone" in
# detect_consolidation below, vs. the quiet "base" that precedes it --
# within the 5-10 day range specified for this pattern; 10 (the upper end)
# gives the breakout a slightly wider confirmation window.
CONSOLIDATION_BREAKOUT_DAYS = 10
# The base segment's own high-low range, as a percentage of its average
# price, at or above which the tightness component scores 0 -- i.e. a base
# has to trade within this band to earn any tightness credit at all. 10%
# per the requirement's documented "8-10%" guidance.
CONSOLIDATION_RANGE_MAX_PCT = 10.0


def detect_consolidation(prices, volumes, window_days=75):
    """Scores how much the most recent window_days looks like a quiet
    consolidation base that's now breaking out -- prices/volumes ordered
    oldest-first, same convention as the rest of this module. Splits the
    window into a "base" (the older window_days - CONSOLIDATION_BREAKOUT_DAYS
    days) and a "breakout zone" (the most recent CONSOLIDATION_BREAKOUT_DAYS
    days) -- the base is what's being broken out OF, so the base's own
    high/volume (not the whole window's) is what the breakout zone gets
    compared against; including the breakout days themselves in the base
    would let a strong breakout artificially widen its own "tightness"
    measurement.

    Returns 0 if there's fewer than window_days of price/volume history,
    or if window_days itself is too small to leave a non-empty base after
    reserving CONSOLIDATION_BREAKOUT_DAYS for the breakout zone.

    SCORING FORMULA (sums to at most 100 -- deliberately weighted so the
    breakout components (60 total) outweigh the quiet-base components (40
    total) combined, since the breakout is "the actual actionable signal,
    not the quiet period alone"):

      1) Tightness component, 0-25 points: the base's own high-low range as
         a % of its average price (see CONSOLIDATION_RANGE_MAX_PCT, 10%).
         Full 25 points at a 0% range, scaling down linearly to 0 points at
         a 10%+ range.
      2) Quiet-volume component, 0-15 points: flat 15 points if the base's
         OWN second half traded at or below its own first half's average
         volume (flat-or-declining, not accelerating) -- a structural
         yes/no, same reasoning as rounding-bottom's basing component.
      3) Breakout-price component, 0-40 points: the breakout zone's highest
         close clearing the base's own high. Scaled by how far above it,
         proportionally up to a 10% clearance for full 40 points
         (clearance_pct / 10 * 40); 0 if the breakout zone never actually
         clears the base high at all.
      4) Breakout-volume component, 0-20 points: the breakout zone's
         average volume exceeding the base's average volume -- a genuine
         spike, not just a price move on ordinary volume. Scaled up to a
         full 2x the base's average volume for the full 20 points
         ((ratio - 1) * 20); 0 if breakout-zone volume doesn't exceed the
         base's average at all."""
    if len(prices) < window_days or len(volumes) < window_days:
        return 0

    base_days = window_days - CONSOLIDATION_BREAKOUT_DAYS
    if base_days < 1:
        return 0

    window_prices = prices[-window_days:]
    window_volumes = volumes[-window_days:]
    base_prices, breakout_prices = window_prices[:base_days], window_prices[base_days:]
    base_volumes, breakout_volumes = window_volumes[:base_days], window_volumes[base_days:]

    base_high, base_low = max(base_prices), min(base_prices)
    base_avg_price = _mean(base_prices)
    base_avg_vol = _mean(base_volumes)
    breakout_high = max(breakout_prices) if breakout_prices else 0
    breakout_avg_vol = _mean(breakout_volumes) if breakout_volumes else 0

    score = 0.0

    if base_avg_price > 0:
        range_pct = (base_high - base_low) / base_avg_price * 100
        score += max(0.0, (1 - range_pct / CONSOLIDATION_RANGE_MAX_PCT) * 25)

    if len(base_volumes) >= 2:
        half = len(base_volumes) // 2
        first_half_avg_vol = _mean(base_volumes[:half])
        second_half_avg_vol = _mean(base_volumes[half:])
        if second_half_avg_vol <= first_half_avg_vol:
            score += 15.0

    if base_high > 0 and breakout_high > base_high:
        clearance_pct = (breakout_high - base_high) / base_high * 100
        score += min(40.0, clearance_pct / 10 * 40)

    if base_avg_vol > 0 and breakout_avg_vol > base_avg_vol:
        vol_ratio = breakout_avg_vol / base_avg_vol
        score += min(20.0, (vol_ratio - 1) * 20)

    return round(min(100.0, max(0.0, score)), 2)
