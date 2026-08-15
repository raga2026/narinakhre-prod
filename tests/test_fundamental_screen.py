from utils.fundamental_screen import classify_fundamental_tier, evaluate_fundamentals, get_metric_note

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


def test_quarterly_growth_above_old_15pct_cap_now_passes():
    # Growth is a floor, not a range -- a company growing profit 30% and
    # revenue 40% should never fail for growing "too fast".
    row = {**PASSING_ROW, 'quarterly_profit_growth_pct': 30, 'quarterly_revenue_growth_pct': 40}

    passes, failed = evaluate_fundamentals(row, previous_fundamentals_row=None)

    assert passes is True
    assert failed == []


def test_quarterly_growth_below_10pct_floor_still_fails():
    row = {**PASSING_ROW, 'quarterly_profit_growth_pct': 5, 'quarterly_revenue_growth_pct': 5}

    passes, failed = evaluate_fundamentals(row, previous_fundamentals_row=None)

    assert passes is False
    assert 'quarterly profit growth' in failed
    assert 'quarterly revenue growth' in failed


def test_negative_quarterly_growth_fails_same_as_any_other_sub_floor_value():
    row = {**PASSING_ROW, 'quarterly_profit_growth_pct': -5, 'quarterly_revenue_growth_pct': -5}

    passes, failed = evaluate_fundamentals(row, previous_fundamentals_row=None)

    assert passes is False
    assert 'quarterly profit growth' in failed
    assert 'quarterly revenue growth' in failed


# --- get_metric_note ---------------------------------------------------

def test_get_metric_note_pe_within_ideal_range_returns_none():
    assert get_metric_note('pe_ratio', 20) is None
    assert get_metric_note('pe_ratio', 15) is None  # boundary, inclusive
    assert get_metric_note('pe_ratio', 25) is None  # boundary, inclusive


def test_get_metric_note_pe_outside_ideal_range_returns_note():
    note = get_metric_note('pe_ratio', 30)
    assert note == 'outside ideal 15-25 range — scored on a sliding scale, not hard-filtered'
    assert get_metric_note('pe_ratio', 5) == note  # same note regardless of direction


def test_get_metric_note_pe_missing_returns_none():
    assert get_metric_note('pe_ratio', None) is None


def test_get_metric_note_opm_at_or_above_ideal_returns_none():
    assert get_metric_note('opm_pct', 25) is None  # boundary, inclusive
    assert get_metric_note('opm_pct', 40) is None


def test_get_metric_note_opm_below_ideal_but_above_silver_floor_returns_note():
    note = get_metric_note('opm_pct', 18)
    assert note == 'below ideal 25% threshold — partial credit given, not disqualifying'
    assert get_metric_note('opm_pct', 15) == note  # boundary, inclusive


def test_get_metric_note_opm_below_silver_floor_returns_none():
    # Below 15% isn't "partial credit" territory -- it's excluded outright,
    # same as before this tiering existed, so no note is shown.
    assert get_metric_note('opm_pct', 14.9) is None
    assert get_metric_note('opm_pct', 0) is None


def test_get_metric_note_opm_missing_returns_none():
    assert get_metric_note('opm_pct', None) is None


def test_get_metric_note_unknown_metric_returns_none():
    assert get_metric_note('roce_pct', 5) is None


# --- classify_fundamental_tier ------------------------------------------

def test_classify_all_passing_row_is_golden():
    tier, failed = classify_fundamental_tier(PASSING_ROW, PASSING_PREVIOUS_ROW)

    assert tier == 'golden'
    assert failed == []


def test_classify_pe_only_failure_is_silver():
    row = {**PASSING_ROW, 'pe_ratio': 45}

    tier, failed = classify_fundamental_tier(row, previous_fundamentals_row=None)

    assert tier == 'silver'
    assert failed == ['PE range']


def test_classify_opm_failure_above_silver_floor_is_silver():
    row = {**PASSING_ROW, 'opm_pct': 18}  # below ideal 25, at/above silver floor 15

    tier, failed = classify_fundamental_tier(row, previous_fundamentals_row=None)

    assert tier == 'silver'
    assert failed == ['OPM']


def test_classify_opm_failure_below_silver_floor_is_excluded():
    row = {**PASSING_ROW, 'opm_pct': 10}  # below the silver floor entirely

    tier, failed = classify_fundamental_tier(row, previous_fundamentals_row=None)

    assert tier is None
    assert failed == ['OPM']


def test_classify_pe_and_opm_both_failing_is_still_silver():
    row = {**PASSING_ROW, 'pe_ratio': 45, 'opm_pct': 18}

    tier, failed = classify_fundamental_tier(row, previous_fundamentals_row=None)

    assert tier == 'silver'
    assert set(failed) == {'PE range', 'OPM'}


def test_classify_missing_pe_never_earns_silver():
    # Missing data doesn't get the benefit of the doubt -- same rule
    # evaluate_fundamentals() already applies to every other field.
    row = {**PASSING_ROW, 'pe_ratio': None}

    tier, failed = classify_fundamental_tier(row, previous_fundamentals_row=None)

    assert tier is None
    assert failed == ['PE range']


def test_classify_failure_on_unrelated_criterion_is_excluded_regardless_of_pe_opm():
    row = {**PASSING_ROW, 'pe_ratio': 45, 'roce_pct': -1}

    tier, failed = classify_fundamental_tier(row, previous_fundamentals_row=None)

    assert tier is None
    assert set(failed) == {'PE range', 'ROCE'}
