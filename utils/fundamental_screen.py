"""Domain-expert fundamental screening criteria for Nari Nakhre Stocks.
Pure evaluation logic only -- no DB access here (see
stock_shortlist.run_fundamental_shortlist for how this gets applied over
stock_universe/stock_fundamentals and synced into stock_watchlist)."""

PEG_MAX = 1
QUARTERLY_GROWTH_MIN = 10
OPM_MIN_PCT = 25
# Floor for OPM to still qualify for the 'silver' tier (see
# classify_fundamental_tier below) -- below this, OPM fails outright same as
# before this tiering existed.
OPM_SILVER_MIN_PCT = 15

# PE and price-to-book are screened against that company's OWN INDUSTRY
# average (see utils/screener_client.py's _parse_industry_classification,
# which scrapes Screener.in's sector/industry breadcrumb alongside every
# other fundamental, and stock_shortlist._compute_industry_benchmarks,
# which turns that into a per-industry average+count once per screening
# run) rather than one flat number for every company on the exchange. A
# single global PE or P/B band either rejects most of the market or lets
# everything through -- what's "reasonable" for an IT services company
# (routinely PE 25-40) is very different from a PSU bank (often PE under
# 10), and the same is true of price-to-book across sectors.
#
# A company passes if its PE/price-to-book sits within
# [floor_multiplier, ceiling_multiplier] times its industry's average.
# Price-to-book has no floor multiplier (0 = no lower bound) -- trading
# cheaper than industry peers is a value signal for this kind of screen,
# not a red flag. PE keeps a floor: an unusually low PE relative to peers
# can also reflect a problem the market has already priced in, not just a
# bargain.
PE_FLOOR_MULTIPLIER, PE_CEILING_MULTIPLIER = 0.5, 1.5
PRICE_TO_BOOK_CEILING_MULTIPLIER = 1.5

# An industry benchmark is only trusted once at least this many companies
# in that same industry have fresh PE/price-to-book data this run --
# below that, a single outlier could swing the "average" wildly, so
# screening falls back to these flat bands instead. This is the ORIGINAL
# global assumption, kept only as a safety net now (e.g. for a
# newly-added or rarely-classified industry), not the primary rule.
MIN_INDUSTRY_SAMPLE_SIZE = 3
PE_FALLBACK_MIN, PE_FALLBACK_MAX = 15, 25
PRICE_TO_BOOK_FALLBACK_MAX = 10


def _positive(value):
    return value is not None and value > 0


def _passes_industry_band(value, benchmark, floor_multiplier, ceiling_multiplier, fallback_min, fallback_max):
    """value: the company's own PE or price-to-book (may be None -- fails
    outright, missing data never gets the benefit of the doubt). benchmark:
    {'avg': float, 'count': int} for this company's industry, or None/{}
    when there isn't one (no industry scraped yet) or it's None -- see
    MIN_INDUSTRY_SAMPLE_SIZE. floor_multiplier=0 means no effective lower
    bound (avg * 0 == 0, and these values are always positive when
    present)."""
    if value is None:
        return False
    if benchmark and benchmark.get('count', 0) >= MIN_INDUSTRY_SAMPLE_SIZE and benchmark.get('avg'):
        avg = benchmark['avg']
        return avg * floor_multiplier <= value <= avg * ceiling_multiplier
    return fallback_min <= value <= fallback_max


def evaluate_fundamentals(fundamentals_row, previous_fundamentals_row=None, industry_benchmarks=None):
    """Runs one stock_fundamentals snapshot against the domain expert's
    screening criteria:
      - PE ratio within [0.5x, 1.5x] of its industry's average PE (falls
        back to the flat [15, 25] band without a trusted industry
        benchmark -- see PE_FALLBACK_MIN/MAX above)
      - PEG ratio < 1
      - Quarterly profit growth AND revenue growth >= 10%, no upper bound --
        growing faster than 10% should never be a reason to fail. Never
        negative either, but that's already implied by the >=10 floor.
      - OPM >= 25%
      - ROCE and ROA both positive
      - EPS positive
      - Price-to-book no more than 1.5x its industry's average (falls back
        to a flat <=10 ceiling without a trusted industry benchmark -- see
        PRICE_TO_BOOK_FALLBACK_MAX above)
      - Promoter holding stable or increasing, and FII holding increasing
        -- both compare fundamentals_row against previous_fundamentals_row
        (that same company's prior snapshot). Pass previous_fundamentals_row=
        None when there isn't one yet (a brand new company with only one
        snapshot so far) -- both trend checks are skipped entirely in that
        case, not failed, since "no trend data yet" isn't the same as
        "trend is bad." Also skipped (not failed) if either snapshot is
        missing the holding percentage itself.

    Any other numeric field that's None (Screener didn't show it, or the
    scraper couldn't derive it) fails that specific check -- missing data
    doesn't get the benefit of the doubt.

    industry_benchmarks: optional {'pe_ratio': {'avg','count'}|None,
    'price_to_book': {'avg','count'}|None} for this company's own industry
    -- see stock_shortlist._compute_industry_benchmarks. Omitting it (or
    passing None) falls back to the flat bands for both PE and
    price-to-book, same as passing {}.

    Both row arguments are dict-like (support .get(key)) -- real
    stock_fundamentals rows and test fixtures alike.

    Returns (passes: bool, failed_criteria: list[str]). passes is True only
    when failed_criteria is empty."""
    industry_benchmarks = industry_benchmarks or {}
    failed = []

    pe_ratio = fundamentals_row.get('pe_ratio')
    if not _passes_industry_band(
        pe_ratio, industry_benchmarks.get('pe_ratio'),
        PE_FLOOR_MULTIPLIER, PE_CEILING_MULTIPLIER, PE_FALLBACK_MIN, PE_FALLBACK_MAX
    ):
        failed.append('PE range')

    peg_ratio = fundamentals_row.get('peg_ratio')
    if peg_ratio is None or peg_ratio >= PEG_MAX:
        failed.append('PEG')

    profit_growth = fundamentals_row.get('quarterly_profit_growth_pct')
    if profit_growth is None or profit_growth < QUARTERLY_GROWTH_MIN:
        failed.append('quarterly profit growth')

    revenue_growth = fundamentals_row.get('quarterly_revenue_growth_pct')
    if revenue_growth is None or revenue_growth < QUARTERLY_GROWTH_MIN:
        failed.append('quarterly revenue growth')

    opm_pct = fundamentals_row.get('opm_pct')
    if opm_pct is None or opm_pct < OPM_MIN_PCT:
        failed.append('OPM')

    if not _positive(fundamentals_row.get('roce_pct')):
        failed.append('ROCE')

    if not _positive(fundamentals_row.get('roa_pct')):
        failed.append('ROA')

    if not _positive(fundamentals_row.get('eps')):
        failed.append('EPS')

    price_to_book = fundamentals_row.get('price_to_book')
    if not _passes_industry_band(
        price_to_book, industry_benchmarks.get('price_to_book'),
        0, PRICE_TO_BOOK_CEILING_MULTIPLIER, 0, PRICE_TO_BOOK_FALLBACK_MAX
    ):
        failed.append('price-to-book range')

    if previous_fundamentals_row is not None:
        promoter_now = fundamentals_row.get('promoter_holding_pct')
        promoter_before = previous_fundamentals_row.get('promoter_holding_pct')
        if promoter_now is not None and promoter_before is not None and promoter_now < promoter_before:
            failed.append('promoter holding trend')

        fii_now = fundamentals_row.get('fii_holding_pct')
        fii_before = previous_fundamentals_row.get('fii_holding_pct')
        if fii_now is not None and fii_before is not None and not (fii_now > fii_before):
            failed.append('FII holding trend')

    return (len(failed) == 0, failed)


# Criteria that classify_fundamental_tier() will forgive (demote to
# 'silver' rather than exclude outright) when they're the only thing a
# company fails. Every other failed criterion still means outright
# exclusion, unchanged from evaluate_fundamentals()'s original behavior.
SILVER_ELIGIBLE_CRITERIA = {'PE range', 'OPM'}

# One tier more lenient than silver -- everything silver forgives, plus
# ROCE and/or ROA (profitability-quality, thematically the same family as
# OPM) failing too. A company still has to pass PEG, both growth checks,
# EPS, price-to-book, and both holding-trend checks to land here -- this
# is a deliberate, bounded judgment call (not derived from any published
# rule), same as the NNS Score's own ROCE/ROA thresholds elsewhere in this
# codebase: extend this set again if a future tier should forgive more.
BRONZE_ELIGIBLE_CRITERIA = SILVER_ELIGIBLE_CRITERIA | {'ROCE', 'ROA'}


def get_metric_note(metric_name, value):
    """Short, factual context for a PE or OPM value that's outside its
    'ideal' band but still made the cut via the silver tier (see
    classify_fundamental_tier) -- for display next to the value, not for
    screening logic. Returns None when there's nothing worth saying: the
    value is within the fallback ideal band, or missing.

    Uses the flat fallback band (PE_FALLBACK_MIN/MAX), not any specific
    company's industry benchmark -- this is display-layer code with no DB
    access and no per-row industry context (see watchlist_view.py), so it
    can only describe the general case, not "outside ITS industry's
    average" precisely. metric_name is 'pe_ratio' or 'opm_pct'; any other
    name returns None."""
    if metric_name == 'pe_ratio':
        if value is None or PE_FALLBACK_MIN <= value <= PE_FALLBACK_MAX:
            return None
        return 'outside the typical range for its industry — scored on a sliding scale, not hard-filtered'

    if metric_name == 'opm_pct':
        if value is None or value >= OPM_MIN_PCT:
            return None
        if value >= OPM_SILVER_MIN_PCT:
            return f'below ideal {OPM_MIN_PCT}% threshold — partial credit given, not disqualifying'
        return None

    return None


def _fails_pe_or_opm_data_floor(fundamentals_row, failed_criteria):
    """Shared missing-data/floor guard for both silver and bronze -- a
    missing PE value, or an OPM below OPM_SILVER_MIN_PCT (or also missing),
    never gets the benefit of the doubt at ANY forgiving tier, only a full
    exclusion. Applies regardless of which forgiving tier is being
    evaluated, since both silver and bronze forgive PE range/OPM the same
    way -- bronze just additionally forgives ROCE/ROA on top."""
    if 'PE range' in failed_criteria and fundamentals_row.get('pe_ratio') is None:
        return True
    if 'OPM' in failed_criteria:
        opm_pct = fundamentals_row.get('opm_pct')
        if opm_pct is None or opm_pct < OPM_SILVER_MIN_PCT:
            return True
    return False


def classify_fundamental_tier(fundamentals_row, previous_fundamentals_row=None, industry_benchmarks=None):
    """Runs evaluate_fundamentals() and sorts the result into a graduated
    outcome for stock_shortlist.run_fundamental_shortlist() -- a company
    doesn't have to pass everything outright to stay tracked, it just drops
    a tier for each additional thing it's lost out on:
      - 'golden': passes every criterion outright.
      - 'silver': fails ONLY on PE range and/or OPM (every other criterion
        still passes) -- these two become a soft second-level filter instead
        of disqualifying outright. OPM only earns silver down to
        OPM_SILVER_MIN_PCT; below that it's excluded same as any other
        failure. A missing (None) PE or OPM value never earns silver --
        missing data still doesn't get the benefit of the doubt, same rule
        evaluate_fundamentals() already applies everywhere else.
      - 'bronze': fails on PE range and/or OPM (same floor/missing-data
        rule as silver above) AND/OR ROCE and/or ROA -- i.e. everything
        silver forgives, plus weak-but-not-fatal profitability quality on
        top. Every other criterion (PEG, growth, EPS, price-to-book,
        holding trends) must still pass -- this is still a company with a
        sound growth/valuation story, just a softer profitability profile
        than silver requires.
      - None: excluded -- fails on some criterion outside
        BRONZE_ELIGIBLE_CRITERIA, or hits the PE/OPM data floor above.

    industry_benchmarks: passed straight through to evaluate_fundamentals --
    see its docstring.

    Returns (tier, failed_criteria) -- failed_criteria is exactly what
    evaluate_fundamentals() returned, so run_fundamental_shortlist's
    failed_criteria_counts reporting (which only wants criteria that
    actually caused exclusion) keeps working: it should only be recorded
    for a None tier, never for 'silver'/'bronze'."""
    passes, failed_criteria = evaluate_fundamentals(fundamentals_row, previous_fundamentals_row, industry_benchmarks)
    if passes:
        return 'golden', failed_criteria

    failed_set = set(failed_criteria)

    if not failed_set - SILVER_ELIGIBLE_CRITERIA:
        if _fails_pe_or_opm_data_floor(fundamentals_row, failed_criteria):
            return None, failed_criteria
        return 'silver', failed_criteria

    if not failed_set - BRONZE_ELIGIBLE_CRITERIA:
        if _fails_pe_or_opm_data_floor(fundamentals_row, failed_criteria):
            return None, failed_criteria
        return 'bronze', failed_criteria

    return None, failed_criteria
