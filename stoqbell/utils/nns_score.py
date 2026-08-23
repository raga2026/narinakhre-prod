"""NNS Score -- a single 0-10 (one decimal place) composite ranking number
for Nari Nakhre Stocks suggestions, quantifying every fundamental and
technical parameter this app already screens on into one comparable
number, instead of a plain pass/fail per criterion. Pure logic only, no DB
access -- see stock_shortlist.py for the same DB-free/DB-orchestration
split used throughout this codebase.

Twelve WEIGHTED sub-scores, each computed 0-1 raw (see WEIGHTS below for
the full table and exactly how a raw 0-1 sub-score becomes its actual
point contribution): PE fit, PEG, OPM, ROCE, ROA, quarterly profit growth,
quarterly revenue growth, price-to-book fit, reserves-to-debt, free cash
flow, RSI position, promoter/FII holding trend. This used to be ten
components at flat equal weight (1.0 point each, no PEG re-check anywhere
past watchlist admission) -- see the "PEG diagnosis" note below for why
that changed.

PE and price-to-book fit reuse the exact same industry-relative bands
fundamental_screen.py's PASS/FAIL check uses (see
utils.fundamental_screen.PE_FLOOR_MULTIPLIER etc.) -- scoring how close a
company sits to the CENTER of the band it's already being screened
against, not a separate standard.

The remaining metrics (ROCE, ROA, growth rates, reserves-to-debt) have no
natural pass/fail ceiling the way PE/PB do -- fundamental_screen.py only
enforces a FLOOR for the first three (must be positive, or above the 10%
growth floor), and reserves-to-debt has no floor/ceiling anywhere else at
all. A ceiling has to be picked somewhere to turn "higher is better,
unbounded" into a 0-1 score; the values below are a general "what counts
as an excellent number for an Indian-listed company" assumption, not
derived from a published study the way the pattern-pricing reliability
figures are -- flagged here so it's easy to find and adjust, not hidden
inside the math. RESERVES_TO_DEBT_FLOOR/CEILING in particular are a
first-pass default (full points at ratio >= 2, tapering to 0 at ratio <=
0.5, same shape as every other higher-is-better metric here) -- worth
reviewing against this app's own real fundamentals data once there's
enough of it synced to see the actual distribution, same as the
FII-holding percentile work in fundamental_screen.py's own
compute_holding_percentiles was built to eventually inform ITS bands.

--- PEG diagnosis (companies with PEG > 1 reaching suggestion output) ---
Confirmed root cause: fundamental_screen.PEG_MAX (1) already hard-excludes
PEG >= 1 at WATCHLIST ADMISSION (evaluate_fundamentals, never forgiven at
any tier) -- but that's a point-in-time check. A company's PEG can drift
upward after admission (slower earnings growth, or a rising price) without
being removed from the watchlist for it, since re-shortlisting only
happens on its own periodic cadence, not continuously. Suggestion ranking
(suggestion_engine.is_suggestion_eligible -> score_candidates ->
compute_nns_score, the SAME single scoring path every suggestion source
uses -- daily Pick of the Day, Starters, the large-cap bonus pick, Special
Recommendations, and the staff watchlist's own StoqBell Score column, no
separate/duplicated scoring logic anywhere) reads whatever PEG is
CURRENTLY on record, and until this fix, never re-checked it -- PEG was
just one of ten EQUALLY weighted components (worth 1 of 10 points), so a
company that drifted to PEG=2 or PEG=5 still only lost 1 point, easily
recovered by scoring well on the other nine and still clearing
NNS_GOLDEN_MIN (8.0) or NNS_SILVER_MIN (6.0). Fixed two ways: (1)
is_suggestion_eligible now re-excludes PEG >= PEG_HARD_EXCLUSION_MAX (1.5)
outright, same as failing golden-cross -- see that constant's own
docstring for why 1.5, not the admission-time 1.0, was chosen (avoids
collapsing the eligible pool to zero); (2) PEG's weight below is raised
from the old equal 10 to 15 (out of 100), so a PEG in the still-tolerated
[1.0, 1.5) band -- which still scores 0 for this component, same curve as
before -- now costs meaningfully more of the total than it used to."""
from stoqbell.utils.fundamental_screen import (
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

# Reserves-to-debt (Reserves / Borrowings, see
# screener_client._compute_reserves_to_debt_ratio) -- full points at ratio
# >= 2 (reserves comfortably more than double outstanding borrowings),
# tapering to 0 at ratio <= 0.5. A debt-free company scores the sentinel
# RESERVES_TO_DEBT_DEBT_FREE_SENTINEL (999, see that module), which is
# comfortably above this ceiling, so it always reads as "maximally
# healthy" -- exactly the same "value at/above ceiling scores 1.0" rule
# every other higher-is-better metric here already follows, no special
# case needed in the scoring code itself. First-pass default, same
# "flagged here, not derived from a published study" caveat as
# ROCE/ROA/GROWTH_EXCELLENT_PCT above -- worth reviewing against this
# app's own real fundamentals data once there's enough of it synced.
RESERVES_TO_DEBT_FLOOR = 0.5
RESERVES_TO_DEBT_CEILING = 2

# RSI sub-score peaks at the center of fundamental_screen's own neutral
# window (RSI_MIN..RSI_MAX in suggestion_engine.py, 40-65 -- center 52.5),
# tapering to 0 at either edge of that window and beyond.
RSI_CENTER = 52.5
RSI_HALF_WIDTH = 12.5  # (65 - 40) / 2

# Added on top of the twelve weighted fundamental/technical sub-scores
# below (then the total is capped back to 10.0), rather than as another
# weighted component -- that would push the natural maximum past 10 and
# quietly change what every existing NNS_GOLDEN_MIN/SILVER_MIN/BRONZE_MIN
# threshold (and every "out of 10" badge in the UI) actually means. This
# way a golden-cross stock always scores strictly higher than an otherwise
# identical one without it, without moving the scale anyone already reads
# this score against. Own judgment call on the size (same as the
# ROCE/ROA/growth ceilings above), not derived from anything published --
# meaningful enough to matter at a tier boundary, small enough that a weak
# golden-cross stock still can't outscore a strong non-crossed one on
# fundamentals alone.
GOLDEN_CROSS_BONUS = 0.5

# ---------------------------------------------------------------------------
# Weight table -- every component compute_nns_score scores, read top to
# bottom for the complete picture at a glance. Weights are "out of 100" for
# readability (a domain expert can read this table directly and see it sums
# to 100), even though the score itself still lands on the same 0-10 scale
# every NNS_GOLDEN_MIN/SILVER_MIN/BRONZE_MIN threshold, UI badge, and email
# already uses -- nothing outside this file has to change meaning. Each
# component's raw 0-1 sub-score (exactly what compute_nns_score's own
# breakdown dict returns -- unchanged shape, still directly testable/
# readable on its own 0-1 terms) becomes its actual point contribution as
# raw_sub_score * (weight / 10) -- e.g. a perfect PEG (raw 1.0) contributes
# 15/10 = 1.5 points; a raw 0.0 contributes 0 regardless of weight.
#
#   Component            Weight   Notes
#   ------------------- --------  ---------------------------------------
#   peg                     15    Raised from the old equal 10 -- see the
#                                 "PEG diagnosis" note in this module's
#                                 own docstring above for exactly why.
#   opm                     12    Deliberately, meaningfully above 'fcf'
#                                 (6) below -- OPM is a margin QUALITY
#                                 signal proven at fundamental_screen.py's
#                                 admission gate (OPM_MIN_PCT); free cash
#                                 flow is a newer, cruder positive/negative
#                                 signal (see 'fcf' below) with no
#                                 per-company-size normalization yet.
#   pe_fit                   9    Unchanged in relative importance to
#   roce                     9    roa/profit_growth/revenue_growth --
#   roa                      9    all four trimmed slightly (from the old
#   profit_growth             8    equal 10) to make room for the two new
#   revenue_growth             8    components without moving any of them
#                                 out of proportion to each other.
#   reserves_to_debt          8    New -- see RESERVES_TO_DEBT_FLOOR/CEILING
#                                 above and _score_higher_is_better below.
#   price_to_book_fit         6    Trimmed further than pe_fit/roce/roa/
#   rsi_position              5    growth/holding_trend -- these five carry
#   holding_trend             5    the most room to give up without any one
#                                 of them losing significance on its own.
#   fcf                       6    New -- see 'opm' above for why this is
#                                 deliberately half of OPM's weight, and
#                                 _score_fcf below for the scoring itself
#                                 (binary positive/negative -- see its own
#                                 docstring for why, not a ceiling/floor
#                                 curve like the others).
#   ------------------- --------
#   TOTAL                   100
WEIGHTS = {
    'peg': 15,
    'opm': 12,
    'pe_fit': 9,
    'roce': 9,
    'roa': 9,
    'profit_growth': 8,
    'revenue_growth': 8,
    'reserves_to_debt': 8,
    'price_to_book_fit': 6,
    'fcf': 6,
    'rsi_position': 5,
    'holding_trend': 5,
}
assert sum(WEIGHTS.values()) == 100, 'NNS Score component weights must sum to 100 -- see the table above'

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


def _score_fcf(free_cash_flow):
    """0.0 or 1.0 -- binary, unlike the ceiling/floor curves every other
    metric here uses. Free cash flow is an absolute rupee figure with no
    per-company-size normalization available yet (no revenue- or
    market-cap-relative FCF column exists in stock_fundamentals) -- a flat
    rupee ceiling would be meaningless across a mid-cap and a large-cap
    company that differ 10-100x in scale, so instead of guessing at one,
    this scores the one thing that IS comparable regardless of size:
    whether the company generates free cash at all. None/zero/negative
    scores 0, same "missing data doesn't get the benefit of the doubt"
    rule as everywhere else in this module. Worth revisiting (e.g. a
    revenue- or market-cap-relative FCF ratio) once that normalization
    data exists -- flagged here the same way RESERVES_TO_DEBT_FLOOR/
    CEILING and the ROCE/ROA/GROWTH ceilings above already are."""
    return 1.0 if free_cash_flow is not None and free_cash_flow > 0 else 0.0


def compute_nns_score(candidate, previous_fundamentals_row=None, industry_benchmarks=None):
    """candidate: dict-like with pe_ratio, peg_ratio, opm_pct, roce_pct,
    roa_pct, quarterly_profit_growth_pct, quarterly_revenue_growth_pct,
    price_to_book, reserves_to_debt_ratio, free_cash_flow, rsi_14,
    promoter_holding_pct, fii_holding_pct, cross_status. industry_benchmarks:
    this candidate's own industry's {'pe_ratio', 'price_to_book'} benchmark
    dict -- see stock_shortlist._compute_industry_benchmarks; omit/None
    falls back to the flat bands, same as fundamental_screen.py's own
    screening.

    Returns (score: float 0-10 with one decimal, breakdown: dict of the
    twelve individual RAW 0-1 sub-scores plus 'golden_cross_bonus') -- raw,
    not weighted, so each component stays directly readable/testable on
    its own 0-1 terms regardless of weight changes; see WEIGHTS above for
    how a raw sub-score becomes its actual point contribution to the final
    score. The breakdown is returned so a caller (or a future "why this
    score" display) can show which specific parameters helped or hurt, not
    just the final number."""
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
        'reserves_to_debt': _score_higher_is_better(
            candidate.get('reserves_to_debt_ratio'), floor=RESERVES_TO_DEBT_FLOOR, ceiling=RESERVES_TO_DEBT_CEILING
        ),
        'fcf': _score_fcf(candidate.get('free_cash_flow')),
        'rsi_position': rsi_position,
        'holding_trend': _holding_trend_score(candidate, previous_fundamentals_row),
    }
    breakdown['golden_cross_bonus'] = GOLDEN_CROSS_BONUS if candidate.get('cross_status') == 'golden_cross' else 0.0

    weighted_total = sum(breakdown[name] * WEIGHTS[name] / 10 for name in WEIGHTS)
    score = round(min(10.0, weighted_total + breakdown['golden_cross_bonus']), 1)
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
