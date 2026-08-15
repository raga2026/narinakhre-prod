"""Domain-expert fundamental screening criteria for Nari Nakhre Stocks.
Pure evaluation logic only -- no DB access here (see
stock_shortlist.run_fundamental_shortlist for how this gets applied over
stock_universe/stock_fundamentals and synced into stock_watchlist)."""

PE_MIN, PE_MAX = 15, 25
PEG_MAX = 1
QUARTERLY_GROWTH_MIN = 10
OPM_MIN_PCT = 25
# Floor for OPM to still qualify for the 'silver' tier (see
# classify_fundamental_tier below) -- below this, OPM fails outright same as
# before this tiering existed. PE has no equivalent floor/ceiling: any
# present (non-None) PE value outside [PE_MIN, PE_MAX] is silver-eligible.
OPM_SILVER_MIN_PCT = 15
PRICE_TO_BOOK_PASS_MIN, PRICE_TO_BOOK_PASS_MAX = 2, 7
# As given in the domain expert's notes. Worth flagging: this is an unusual
# range for a price-to-book multiple -- most listed companies run well
# under 10, and 15-25 mirrors the PE range above almost exactly, which
# smells like it could be a copy/paste of the PE thresholds rather than a
# deliberately chosen P/B range. Implemented literally as specified rather
# than "corrected" silently; double-check this was actually intended.
PRICE_TO_BOOK_PREMIUM_MIN, PRICE_TO_BOOK_PREMIUM_MAX = 15, 25


def _in_range(value, lo, hi):
    return value is not None and lo <= value <= hi


def _positive(value):
    return value is not None and value > 0


def evaluate_fundamentals(fundamentals_row, previous_fundamentals_row=None):
    """Runs one stock_fundamentals snapshot against the domain expert's
    screening criteria:
      - PE ratio in [15, 25]
      - PEG ratio < 1
      - Quarterly profit growth AND revenue growth >= 10%, no upper bound --
        growing faster than 10% should never be a reason to fail. Never
        negative either, but that's already implied by the >=10 floor.
      - OPM >= 25%
      - ROCE and ROA both positive
      - EPS positive
      - Price-to-book in [2, 7] passes; [15, 25] passes too but logs a
        "premium valuation" note rather than being rejected (see the
        PRICE_TO_BOOK_PREMIUM_* comment above); anything else fails
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

    Both row arguments are dict-like (support .get(key)) -- real
    stock_fundamentals rows and test fixtures alike.

    Returns (passes: bool, failed_criteria: list[str]). passes is True only
    when failed_criteria is empty."""
    failed = []

    if not _in_range(fundamentals_row.get('pe_ratio'), PE_MIN, PE_MAX):
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
    if _in_range(price_to_book, PRICE_TO_BOOK_PASS_MIN, PRICE_TO_BOOK_PASS_MAX):
        pass
    elif _in_range(price_to_book, PRICE_TO_BOOK_PREMIUM_MIN, PRICE_TO_BOOK_PREMIUM_MAX):
        print(f'Price-to-book {price_to_book} is in the premium-valuation range '
              f'({PRICE_TO_BOOK_PREMIUM_MIN}-{PRICE_TO_BOOK_PREMIUM_MAX}) -- not auto-rejected.')
    else:
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


def get_metric_note(metric_name, value):
    """Short, factual context for a PE or OPM value that's outside its
    'ideal' band but still made the cut via the silver tier (see
    classify_fundamental_tier) -- for display next to the value, not for
    screening logic. Returns None when there's nothing worth saying: the
    value is within the ideal band, or missing. metric_name is 'pe_ratio'
    or 'opm_pct'; any other name returns None."""
    if metric_name == 'pe_ratio':
        if value is None or PE_MIN <= value <= PE_MAX:
            return None
        return f'outside ideal {PE_MIN}-{PE_MAX} range — scored on a sliding scale, not hard-filtered'

    if metric_name == 'opm_pct':
        if value is None or value >= OPM_MIN_PCT:
            return None
        if value >= OPM_SILVER_MIN_PCT:
            return f'below ideal {OPM_MIN_PCT}% threshold — partial credit given, not disqualifying'
        return None

    return None


def classify_fundamental_tier(fundamentals_row, previous_fundamentals_row=None):
    """Runs evaluate_fundamentals() and sorts the result into a two-tier
    outcome for stock_shortlist.run_fundamental_shortlist():
      - 'golden': passes every criterion outright.
      - 'silver': fails ONLY on PE range and/or OPM (every other criterion
        still passes) -- these two become a soft second-level filter instead
        of disqualifying outright. OPM only earns silver down to
        OPM_SILVER_MIN_PCT; below that it's excluded same as any other
        failure. A missing (None) PE or OPM value never earns silver --
        missing data still doesn't get the benefit of the doubt, same rule
        evaluate_fundamentals() already applies everywhere else.
      - None: excluded -- fails on some other criterion, or PE/OPM data is
        missing, or OPM is below OPM_SILVER_MIN_PCT.

    Returns (tier, failed_criteria) -- failed_criteria is exactly what
    evaluate_fundamentals() returned, so run_fundamental_shortlist's
    failed_criteria_counts reporting (which only wants criteria that
    actually caused exclusion) keeps working: it should only be recorded
    for a None tier, never for 'silver'."""
    passes, failed_criteria = evaluate_fundamentals(fundamentals_row, previous_fundamentals_row)
    if passes:
        return 'golden', failed_criteria

    if set(failed_criteria) - SILVER_ELIGIBLE_CRITERIA:
        return None, failed_criteria

    if 'PE range' in failed_criteria and fundamentals_row.get('pe_ratio') is None:
        return None, failed_criteria

    if 'OPM' in failed_criteria:
        opm_pct = fundamentals_row.get('opm_pct')
        if opm_pct is None or opm_pct < OPM_SILVER_MIN_PCT:
            return None, failed_criteria

    return 'silver', failed_criteria
