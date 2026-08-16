"""NNS Score -- a single 0-10 (one decimal place) composite ranking number
for Nari Nakhre Stocks suggestions, quantifying every fundamental and
technical parameter this app already screens on into one comparable
number, instead of a plain pass/fail per criterion. Pure logic only, no DB
access -- see stock_shortlist.py for the same DB-free/DB-orchestration
split used throughout this codebase.

Ten equally-weighted (1.0 point each) sub-scores, each 0-1:
  PE fit, PEG, OPM, ROCE, ROA, quarterly profit growth, quarterly revenue
  growth, price-to-book fit, RSI position, promoter/FII holding trend.
Equal weighting is the simplest, most transparent scheme absent specific
guidance on relative importance -- same reasoning suggestion_engine.py's
own (now-superseded) SCORE_WEIGHTS already used. Summing ten 0-1 scores at
equal weight naturally lands the total in [0, 10].

PE and price-to-book fit reuse the exact same industry-relative bands
fundamental_screen.py's PASS/FAIL check uses (see
utils.fundamental_screen.PE_FLOOR_MULTIPLIER etc.) -- scoring how close a
company sits to the CENTER of the band it's already being screened
against, not a separate standard.

The remaining metrics (ROCE, ROA, growth rates) have no natural pass/fail
ceiling the way PE/PB do -- fundamental_screen.py only enforces a FLOOR for
these (must be positive, or above the 10% growth floor). A ceiling has to
be picked somewhere to turn "higher is better, unbounded" into a 0-1
score; the values below are a general "what counts as an excellent number
for an Indian-listed company" assumption, not derived from a published
study the way the pattern-pricing reliability figures are -- flagged here
so it's easy to find and adjust, not hidden inside the math."""
from utils.fundamental_screen import (
    MIN_INDUSTRY_SAMPLE_SIZE,
    OPM_SILVER_MIN_PCT,
    PE_CEILING_MULTIPLIER,
    PE_FALLBACK_MAX,
    PE_FALLBACK_MIN,
    PE_FLOOR_MULTIPLIER,
    PEG_MAX,
    PRICE_TO_BOOK_CEILING_MULTIPLIER,
    PRICE_TO_BOOK_FALLBACK_MAX,
    QUARTERLY_GROWTH_MIN,
)

# "Excellent" ceilings for the otherwise-unbounded higher-is-better metrics
# -- see the module docstring's note on where these come from (a general
# assumption, not a cited study). A value at or above the ceiling scores
# the full 1.0 point for that metric; the floor (already fundamental_screen.py's
# own pass/fail floor, reused here) scores 0.
ROCE_EXCELLENT_PCT = 25
ROA_EXCELLENT_PCT = 15
GROWTH_EXCELLENT_PCT = 30

# RSI sub-score peaks at the center of fundamental_screen's own neutral
# window (RSI_MIN..RSI_MAX in suggestion_engine.py, 40-65 -- center 52.5),
# tapering to 0 at either edge of that window and beyond.
RSI_CENTER = 52.5
RSI_HALF_WIDTH = 12.5  # (65 - 40) / 2

# Tier thresholds -- the NNS Score's own golden/silver/bronze categories,
# separate from (and more granular than) fundamental_screen.py's
# golden/silver watchlist-membership tiers. A score below NNS_BRONZE_MIN
# doesn't get a tier at all (None) -- "made the suggestion-eligible
# candidate pool" (passed the existing hard filters) is not the same as
# "good enough to actually recommend."
NNS_GOLDEN_MIN = 8.0
NNS_SILVER_MIN = 6.0
NNS_BRONZE_MIN = 4.0


def _score_fit_to_band(value, lo, hi):
    """0-1 score peaking at 1.0 at the CENTER of [lo, hi], tapering
    linearly to 0 at either edge and beyond (or if value is missing) --
    used for PE, where being at the ideal middle of the accepted range is
    better than sitting near either edge of it."""
    if value is None or value < lo or value > hi:
        return 0.0
    center = (lo + hi) / 2
    if value <= center:
        return (value - lo) / (center - lo) if center > lo else 1.0
    return (hi - value) / (hi - center) if hi > center else 1.0


def _score_higher_is_better(value, floor, ceiling):
    """0-1 score: 0 at or below floor, 1 at or above ceiling, linear
    between. None always scores 0 -- missing data doesn't get the benefit
    of the doubt here either, same rule fundamental_screen.py applies."""
    if value is None or value <= floor:
        return 0.0
    if value >= ceiling:
        return 1.0
    return (value - floor) / (ceiling - floor)


def _score_lower_is_better(value, ceiling_bad, floor_good=0):
    """0-1 score: 0 at or above ceiling_bad, 1 at or below floor_good,
    linear between."""
    if value is None or value >= ceiling_bad:
        return 0.0
    if value <= floor_good:
        return 1.0
    return (ceiling_bad - value) / (ceiling_bad - floor_good)


def _pe_band(industry_benchmarks):
    pe_benchmark = (industry_benchmarks or {}).get('pe_ratio')
    if pe_benchmark and pe_benchmark.get('count', 0) >= MIN_INDUSTRY_SAMPLE_SIZE and pe_benchmark.get('avg'):
        avg = pe_benchmark['avg']
        return avg * PE_FLOOR_MULTIPLIER, avg * PE_CEILING_MULTIPLIER
    return PE_FALLBACK_MIN, PE_FALLBACK_MAX


def _price_to_book_ceiling(industry_benchmarks):
    pb_benchmark = (industry_benchmarks or {}).get('price_to_book')
    if pb_benchmark and pb_benchmark.get('count', 0) >= MIN_INDUSTRY_SAMPLE_SIZE and pb_benchmark.get('avg'):
        return pb_benchmark['avg'] * PRICE_TO_BOOK_CEILING_MULTIPLIER
    return PRICE_TO_BOOK_FALLBACK_MAX


def _holding_trend_score(fundamentals_row, previous_fundamentals_row):
    """0, 0.5, or 1.0 -- half a point each for promoter holding stable-or-
    increasing and FII holding strictly increasing (the same two trend
    checks fundamental_screen.evaluate_fundamentals applies, just scored
    on a sliding scale here instead of pass/fail). 0 with no previous
    snapshot to compare against, or if both values are missing -- "no
    trend data yet" doesn't get credit any more than it gets blamed."""
    if previous_fundamentals_row is None:
        return 0.0
    promoter_now = fundamentals_row.get('promoter_holding_pct')
    promoter_before = previous_fundamentals_row.get('promoter_holding_pct')
    fii_now = fundamentals_row.get('fii_holding_pct')
    fii_before = previous_fundamentals_row.get('fii_holding_pct')

    score = 0.0
    if promoter_now is not None and promoter_before is not None and promoter_now >= promoter_before:
        score += 0.5
    if fii_now is not None and fii_before is not None and fii_now > fii_before:
        score += 0.5
    return score


def compute_nns_score(candidate, previous_fundamentals_row=None, industry_benchmarks=None):
    """candidate: dict-like with pe_ratio, peg_ratio, opm_pct, roce_pct,
    roa_pct, quarterly_profit_growth_pct, quarterly_revenue_growth_pct,
    price_to_book, rsi_14, promoter_holding_pct, fii_holding_pct.
    industry_benchmarks: this candidate's own industry's {'pe_ratio',
    'price_to_book'} benchmark dict -- see
    stock_shortlist._compute_industry_benchmarks; omit/None falls back to
    the flat bands, same as fundamental_screen.py's own screening.

    Returns (score: float 0-10 with one decimal, breakdown: dict of the
    ten individual 0-1 sub-scores) -- the breakdown is returned so a
    caller (or a future "why this score" display) can show which specific
    parameters helped or hurt, not just the final number."""
    pe_lo, pe_hi = _pe_band(industry_benchmarks)
    pb_ceiling = _price_to_book_ceiling(industry_benchmarks)

    rsi = candidate.get('rsi_14')
    rsi_position = max(0.0, 1 - abs(rsi - RSI_CENTER) / RSI_HALF_WIDTH) if rsi is not None else 0.0

    breakdown = {
        'pe_fit': _score_fit_to_band(candidate.get('pe_ratio'), pe_lo, pe_hi),
        'peg': _score_lower_is_better(candidate.get('peg_ratio'), ceiling_bad=PEG_MAX),
        'opm': _score_higher_is_better(candidate.get('opm_pct'), floor=OPM_SILVER_MIN_PCT, ceiling=40),
        'roce': _score_higher_is_better(candidate.get('roce_pct'), floor=0, ceiling=ROCE_EXCELLENT_PCT),
        'roa': _score_higher_is_better(candidate.get('roa_pct'), floor=0, ceiling=ROA_EXCELLENT_PCT),
        'profit_growth': _score_higher_is_better(
            candidate.get('quarterly_profit_growth_pct'), floor=QUARTERLY_GROWTH_MIN, ceiling=GROWTH_EXCELLENT_PCT
        ),
        'revenue_growth': _score_higher_is_better(
            candidate.get('quarterly_revenue_growth_pct'), floor=QUARTERLY_GROWTH_MIN, ceiling=GROWTH_EXCELLENT_PCT
        ),
        'price_to_book_fit': _score_lower_is_better(candidate.get('price_to_book'), ceiling_bad=pb_ceiling),
        'rsi_position': rsi_position,
        'holding_trend': _holding_trend_score(candidate, previous_fundamentals_row),
    }

    score = round(sum(breakdown.values()), 1)
    return score, breakdown


def nns_tier(score):
    """'golden' (>=8.0), 'silver' (>=6.0), 'bronze' (>=4.0), or None below
    that -- passing the existing hard filters (see
    suggestion_engine.passes_hard_filters) gets a candidate INTO the pool
    this is computed over; it doesn't guarantee a tier."""
    if score >= NNS_GOLDEN_MIN:
        return 'golden'
    if score >= NNS_SILVER_MIN:
        return 'silver'
    if score >= NNS_BRONZE_MIN:
        return 'bronze'
    return None
