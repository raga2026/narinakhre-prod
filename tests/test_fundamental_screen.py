from utils.fundamental_screen import evaluate_fundamentals

PASSING_ROW = {
    'pe_ratio': 20,
    'peg_ratio': 0.8,
    'eps': 5,
    'opm_pct': 30,
    'roce_pct': 18,
    'roa_pct': 10,
    'price_to_book': 4,
    'promoter_holding_pct': 55,
    'fii_holding_pct': 20,
    'quarterly_profit_growth_pct': 12,
    'quarterly_revenue_growth_pct': 11,
}

PASSING_PREVIOUS_ROW = {
    'promoter_holding_pct': 50,  # current (55) is stable-or-increasing vs this
    'fii_holding_pct': 15,       # current (20) is increasing vs this
}


def test_row_passing_every_criterion_passes_with_no_failures():
    passes, failed = evaluate_fundamentals(PASSING_ROW, PASSING_PREVIOUS_ROW)

    assert passes is True
    assert failed == []


def test_row_failing_only_pe_fails_with_just_that_criterion():
    row = {**PASSING_ROW, 'pe_ratio': 30}  # out of the 15-25 range, everything else still passes

    passes, failed = evaluate_fundamentals(row, previous_fundamentals_row=None)

    assert passes is False
    assert failed == ['PE range']


def test_insufficient_history_skips_trend_checks_instead_of_failing():
    # No previous snapshot at all -- a brand new company. Promoter/FII trend
    # checks must be skipped, not failed, so an otherwise-passing row still
    # passes overall.
    passes, failed = evaluate_fundamentals(PASSING_ROW, previous_fundamentals_row=None)

    assert passes is True
    assert 'promoter holding trend' not in failed
    assert 'FII holding trend' not in failed
    assert failed == []


def test_promoter_or_fii_missing_from_previous_snapshot_also_skips_that_check():
    # A previous snapshot exists, but it's missing the holding percentages
    # themselves (e.g. Screener didn't show them for that older fetch) --
    # should skip rather than fail, same as having no previous row at all.
    previous_missing_holdings = {'promoter_holding_pct': None, 'fii_holding_pct': None}

    passes, failed = evaluate_fundamentals(PASSING_ROW, previous_missing_holdings)

    assert passes is True
    assert failed == []


def test_decreasing_promoter_holding_fails_that_check_only():
    row = {**PASSING_ROW, 'promoter_holding_pct': 45}  # below previous (50)

    passes, failed = evaluate_fundamentals(row, PASSING_PREVIOUS_ROW)

    assert passes is False
    assert failed == ['promoter holding trend']


def test_flat_fii_holding_fails_since_it_must_strictly_increase():
    row = {**PASSING_ROW, 'fii_holding_pct': 15}  # equal to previous (15), not increasing

    passes, failed = evaluate_fundamentals(row, PASSING_PREVIOUS_ROW)

    assert passes is False
    assert failed == ['FII holding trend']


def test_premium_valuation_price_to_book_passes_without_failing():
    row = {**PASSING_ROW, 'price_to_book': 20}  # in the 15-25 "premium valuation" band

    passes, failed = evaluate_fundamentals(row, PASSING_PREVIOUS_ROW)

    assert passes is True
    assert failed == []


def test_price_to_book_between_pass_and_premium_bands_fails():
    row = {**PASSING_ROW, 'price_to_book': 10}  # neither 2-7 nor 15-25

    passes, failed = evaluate_fundamentals(row, previous_fundamentals_row=None)

    assert passes is False
    assert failed == ['price-to-book range']


def test_missing_required_field_fails_that_check():
    row = {**PASSING_ROW, 'roce_pct': None}

    passes, failed = evaluate_fundamentals(row, previous_fundamentals_row=None)

    assert passes is False
    assert 'ROCE' in failed
