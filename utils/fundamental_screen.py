"""Domain-expert fundamental screening criteria for Nari Nakhre Stocks.
Pure evaluation logic only -- no DB access here (see
stock_shortlist.run_fundamental_shortlist for how this gets applied over
stock_universe/stock_fundamentals and synced into stock_watchlist)."""

PE_MIN, PE_MAX = 15, 25
PEG_MAX = 1
QUARTERLY_GROWTH_MIN, QUARTERLY_GROWTH_MAX = 10, 15
OPM_MIN_PCT = 25
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
      - Quarterly profit growth AND revenue growth in [10, 15]%, never
        negative (a value outside that range, including negative, fails --
        _in_range already excludes anything below 10)
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

    if not _in_range(fundamentals_row.get('quarterly_profit_growth_pct'), QUARTERLY_GROWTH_MIN, QUARTERLY_GROWTH_MAX):
        failed.append('quarterly profit growth')

    if not _in_range(fundamentals_row.get('quarterly_revenue_growth_pct'), QUARTERLY_GROWTH_MIN, QUARTERLY_GROWTH_MAX):
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
