from stoqbell.utils.fundamental_screen import (
    MINIMUM_GROWTH_PCT_LARGE_CAP,
    PROMOTER_PLEDGE_MAX_PCT,
    QUARTERLY_GROWTH_MIN,
    classify_fundamental_tier,
    compute_holding_percentiles,
    evaluate_fundamentals,
    evaluate_fundamentals_large_cap,
    get_metric_note,
    score_fundamentals_large_cap,
    score_institutional_holding,
)

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
    row = {**PASSING_ROW, 'pe_ratio': 30}  # out of the fallback 15-25 range, everything else still passes

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


def test_flat_fii_holding_passes_same_as_flat_promoter_holding():
    # FII holding is a quarterly-disclosed figure -- two snapshots taken
    # within the same quarter (the common case once a company has been
    # rescraped) will show the exact same value, with no real trend to
    # observe yet. That must pass, same as stable promoter holding does,
    # not be punished as if it were a decline.
    row = {**PASSING_ROW, 'fii_holding_pct': 15}  # equal to previous (15)

    passes, failed = evaluate_fundamentals(row, PASSING_PREVIOUS_ROW)

    assert passes is True
    assert failed == []


def test_decreasing_fii_holding_fails_that_check_only():
    row = {**PASSING_ROW, 'fii_holding_pct': 10}  # below previous (15)

    passes, failed = evaluate_fundamentals(row, PASSING_PREVIOUS_ROW)

    assert passes is False
    assert failed == ['FII holding trend']


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


# --- price-to-book / PE: fallback bands (no trusted industry benchmark) ----
# These apply whenever industry_benchmarks is omitted, None, or the
# specific metric's benchmark is missing/has too small a sample -- see
# MIN_INDUSTRY_SAMPLE_SIZE. Fallback price-to-book ceiling is 10, no floor;
# fallback PE range is the original flat 15-25 (see fundamental_screen.py).

def test_price_to_book_at_fallback_ceiling_passes():
    row = {**PASSING_ROW, 'price_to_book': 10}

    passes, failed = evaluate_fundamentals(row, previous_fundamentals_row=None)

    assert passes is True
    assert failed == []


def test_price_to_book_above_fallback_ceiling_fails():
    row = {**PASSING_ROW, 'price_to_book': 10.01}

    passes, failed = evaluate_fundamentals(row, previous_fundamentals_row=None)

    assert passes is False
    assert failed == ['price-to-book range']


def test_price_to_book_very_low_passes_fallback_since_there_is_no_floor():
    # Cheap relative to nothing in particular (no industry data here) is
    # still not penalized -- no floor on the fallback band either.
    row = {**PASSING_ROW, 'price_to_book': 0.1}

    passes, failed = evaluate_fundamentals(row, previous_fundamentals_row=None)

    assert passes is True
    assert failed == []


def test_price_to_book_missing_fails_outright():
    row = {**PASSING_ROW, 'price_to_book': None}

    passes, failed = evaluate_fundamentals(row, previous_fundamentals_row=None)

    assert passes is False
    assert failed == ['price-to-book range']


# --- price-to-book / PE: industry-relative bands ---------------------------

def test_price_to_book_within_industry_ceiling_passes_even_above_fallback():
    # 12 would fail the flat fallback ceiling (10), but this industry's
    # companies average 10 -- 1.5x that is 15, so 12 passes when a trusted
    # benchmark is available.
    row = {**PASSING_ROW, 'price_to_book': 12}
    benchmark = {'price_to_book': {'avg': 10, 'count': 5}, 'pe_ratio': None}

    passes, failed = evaluate_fundamentals(row, previous_fundamentals_row=None, industry_benchmarks=benchmark)

    assert passes is True
    assert failed == []


def test_price_to_book_above_industry_ceiling_fails():
    row = {**PASSING_ROW, 'price_to_book': 16}  # > 10 * 1.5
    benchmark = {'price_to_book': {'avg': 10, 'count': 5}, 'pe_ratio': None}

    passes, failed = evaluate_fundamentals(row, previous_fundamentals_row=None, industry_benchmarks=benchmark)

    assert passes is False
    assert failed == ['price-to-book range']


def test_price_to_book_far_below_industry_average_still_passes():
    # No floor multiplier -- trading well under the industry average is a
    # value signal for this screen, not a red flag.
    row = {**PASSING_ROW, 'price_to_book': 0.5}
    benchmark = {'price_to_book': {'avg': 10, 'count': 5}, 'pe_ratio': None}

    passes, failed = evaluate_fundamentals(row, previous_fundamentals_row=None, industry_benchmarks=benchmark)

    assert passes is True
    assert failed == []


def test_industry_benchmark_below_min_sample_size_falls_back_to_flat_band():
    # Only 2 companies in this industry have price_to_book data -- below
    # MIN_INDUSTRY_SAMPLE_SIZE (3), so the average isn't trusted and this
    # falls back to the flat <=10 ceiling despite the (small-sample)
    # industry average being high enough to otherwise pass 12.
    row = {**PASSING_ROW, 'price_to_book': 12}
    benchmark = {'price_to_book': {'avg': 10, 'count': 2}, 'pe_ratio': None}

    passes, failed = evaluate_fundamentals(row, previous_fundamentals_row=None, industry_benchmarks=benchmark)

    assert passes is False
    assert failed == ['price-to-book range']


def test_industry_benchmark_none_for_this_metric_falls_back_to_flat_band():
    # This company's industry IS known and has a trusted PE benchmark, but
    # no company in it has price_to_book data at all (benchmark is None for
    # that specific metric) -- price-to-book still falls back independently.
    row = {**PASSING_ROW, 'price_to_book': 12, 'pe_ratio': 20}
    benchmark = {'price_to_book': None, 'pe_ratio': {'avg': 20, 'count': 5}}

    passes, failed = evaluate_fundamentals(row, previous_fundamentals_row=None, industry_benchmarks=benchmark)

    assert passes is False
    assert failed == ['price-to-book range']


def test_pe_within_industry_band_passes_even_outside_flat_fallback():
    # PE 35 would fail the flat fallback (15-25), but this industry
    # averages 30 -- 0.5x-1.5x that is 15-45, so 35 passes with a trusted
    # benchmark (e.g. an IT-services-like industry that typically trades
    # richer than the flat assumption).
    row = {**PASSING_ROW, 'pe_ratio': 35}
    benchmark = {'pe_ratio': {'avg': 30, 'count': 5}, 'price_to_book': None}

    passes, failed = evaluate_fundamentals(row, previous_fundamentals_row=None, industry_benchmarks=benchmark)

    assert passes is True
    assert failed == []


def test_pe_below_industry_floor_fails():
    row = {**PASSING_ROW, 'pe_ratio': 14}  # < 30 * 0.5
    benchmark = {'pe_ratio': {'avg': 30, 'count': 5}, 'price_to_book': None}

    passes, failed = evaluate_fundamentals(row, previous_fundamentals_row=None, industry_benchmarks=benchmark)

    assert passes is False
    assert failed == ['PE range']


def test_pe_above_industry_ceiling_fails():
    row = {**PASSING_ROW, 'pe_ratio': 46}  # > 30 * 1.5
    benchmark = {'pe_ratio': {'avg': 30, 'count': 5}, 'price_to_book': None}

    passes, failed = evaluate_fundamentals(row, previous_fundamentals_row=None, industry_benchmarks=benchmark)

    assert passes is False
    assert failed == ['PE range']


def test_missing_pe_fails_regardless_of_industry_benchmark():
    row = {**PASSING_ROW, 'pe_ratio': None}
    benchmark = {'pe_ratio': {'avg': 30, 'count': 5}, 'price_to_book': None}

    passes, failed = evaluate_fundamentals(row, previous_fundamentals_row=None, industry_benchmarks=benchmark)

    assert passes is False
    assert failed == ['PE range']


def test_no_industry_benchmarks_argument_at_all_behaves_like_empty_dict():
    # Omitting the argument entirely (the common case for most existing
    # call sites) must behave exactly like passing {} -- pure fallback
    # bands for both metrics.
    row = {**PASSING_ROW, 'pe_ratio': 20, 'price_to_book': 4}

    passes, failed = evaluate_fundamentals(row, previous_fundamentals_row=None)

    assert passes is True
    assert failed == []


# --- get_metric_note ---------------------------------------------------

def test_get_metric_note_pe_within_ideal_range_returns_none():
    assert get_metric_note('pe_ratio', 20) is None
    assert get_metric_note('pe_ratio', 15) is None  # boundary, inclusive
    assert get_metric_note('pe_ratio', 25) is None  # boundary, inclusive


def test_get_metric_note_pe_outside_ideal_range_returns_note():
    note = get_metric_note('pe_ratio', 30)
    assert note == 'outside the typical range for its industry — scored on a sliding scale, not hard-filtered'
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
    # PEG is never forgiven at any tier -- unlike ROCE/ROA (bronze-eligible,
    # see the tests below), failing it always means outright exclusion.
    row = {**PASSING_ROW, 'pe_ratio': 45, 'peg_ratio': 2.0}

    tier, failed = classify_fundamental_tier(row, previous_fundamentals_row=None)

    assert tier is None
    assert set(failed) == {'PE range', 'PEG'}


def test_classify_roce_only_failure_is_bronze():
    row = {**PASSING_ROW, 'roce_pct': -1}

    tier, failed = classify_fundamental_tier(row, previous_fundamentals_row=None)

    assert tier == 'bronze'
    assert failed == ['ROCE']


def test_classify_roa_only_failure_is_bronze():
    row = {**PASSING_ROW, 'roa_pct': -1}

    tier, failed = classify_fundamental_tier(row, previous_fundamentals_row=None)

    assert tier == 'bronze'
    assert failed == ['ROA']


def test_classify_pe_opm_roce_roa_all_failing_together_is_still_bronze():
    row = {**PASSING_ROW, 'pe_ratio': 45, 'opm_pct': 18, 'roce_pct': -1, 'roa_pct': -1}

    tier, failed = classify_fundamental_tier(row, previous_fundamentals_row=None)

    assert tier == 'bronze'
    assert set(failed) == {'PE range', 'OPM', 'ROCE', 'ROA'}


def test_classify_roce_failure_plus_unrelated_criterion_is_excluded():
    row = {**PASSING_ROW, 'roce_pct': -1, 'peg_ratio': 2.0}

    tier, failed = classify_fundamental_tier(row, previous_fundamentals_row=None)

    assert tier is None
    assert set(failed) == {'ROCE', 'PEG'}


def test_classify_bronze_still_respects_the_missing_pe_data_floor():
    row = {**PASSING_ROW, 'pe_ratio': None, 'roce_pct': -1}

    tier, failed = classify_fundamental_tier(row, previous_fundamentals_row=None)

    assert tier is None
    assert set(failed) == {'PE range', 'ROCE'}


def test_classify_bronze_still_respects_the_opm_silver_floor():
    row = {**PASSING_ROW, 'opm_pct': 10, 'roce_pct': -1}  # OPM below the silver/bronze floor entirely

    tier, failed = classify_fundamental_tier(row, previous_fundamentals_row=None)

    assert tier is None
    assert set(failed) == {'OPM', 'ROCE'}


def test_classify_uses_industry_benchmark_when_given():
    # PE 35 fails the flat fallback but passes within this industry's band
    # -- classify_fundamental_tier must pass industry_benchmarks through to
    # evaluate_fundamentals, not just accept it and ignore it.
    row = {**PASSING_ROW, 'pe_ratio': 35}
    benchmark = {'pe_ratio': {'avg': 30, 'count': 5}, 'price_to_book': None}

    tier, failed = classify_fundamental_tier(row, previous_fundamentals_row=None, industry_benchmarks=benchmark)

    assert tier == 'golden'


# --- Large-cap tier (entirely parallel; evaluate_fundamentals/
# classify_fundamental_tier above are never called by any of this) --------

def test_growth_floor_constants_are_independent():
    # The whole point of this tier -- a separate, lower constant, with the
    # original left completely alone.
    assert QUARTERLY_GROWTH_MIN == 10
    assert MINIMUM_GROWTH_PCT_LARGE_CAP == 5.0


def test_six_percent_growth_passes_large_cap_evaluation():
    # Below QUARTERLY_GROWTH_MIN (10%) but above MINIMUM_GROWTH_PCT_LARGE_CAP
    # (5%) -- the central requirement for this tier.
    row = {**PASSING_ROW, 'quarterly_profit_growth_pct': 6, 'quarterly_revenue_growth_pct': 6}

    passes, failed = evaluate_fundamentals_large_cap(row, PASSING_PREVIOUS_ROW)

    assert passes is True
    assert failed == []


def test_six_percent_growth_fails_the_original_mid_cap_evaluation():
    # The exact same row, run through the UNCHANGED original function --
    # must still fail on both growth checks, proving evaluate_fundamentals
    # itself was never touched.
    row = {**PASSING_ROW, 'quarterly_profit_growth_pct': 6, 'quarterly_revenue_growth_pct': 6}

    passes, failed = evaluate_fundamentals(row, PASSING_PREVIOUS_ROW)

    assert passes is False
    assert 'quarterly profit growth' in failed
    assert 'quarterly revenue growth' in failed


def test_four_percent_growth_still_fails_large_cap_floor():
    # Below MINIMUM_GROWTH_PCT_LARGE_CAP too -- the large-cap tier isn't a
    # blank check, it just has a lower bar, not no bar.
    row = {**PASSING_ROW, 'quarterly_profit_growth_pct': 4, 'quarterly_revenue_growth_pct': 4}

    passes, failed = evaluate_fundamentals_large_cap(row, PASSING_PREVIOUS_ROW)

    assert passes is False
    assert 'quarterly profit growth' in failed
    assert 'quarterly revenue growth' in failed


def test_large_cap_evaluation_still_enforces_every_other_criterion():
    # Growth floor is lower, but PEG/OPM/ROCE/etc. are all identical to the
    # mid-cap version -- a row failing PEG must still fail here.
    row = {
        **PASSING_ROW, 'quarterly_profit_growth_pct': 6, 'quarterly_revenue_growth_pct': 6,
        'peg_ratio': 2.0,
    }

    passes, failed = evaluate_fundamentals_large_cap(row, PASSING_PREVIOUS_ROW)

    assert passes is False
    assert failed == ['PEG']


def test_score_fundamentals_large_cap_all_passing_is_golden():
    row = {**PASSING_ROW, 'quarterly_profit_growth_pct': 6, 'quarterly_revenue_growth_pct': 6}

    tier, failed = score_fundamentals_large_cap(row, PASSING_PREVIOUS_ROW)

    assert tier == 'golden'
    assert failed == []


def test_score_fundamentals_large_cap_pe_only_failure_is_silver():
    row = {
        **PASSING_ROW, 'quarterly_profit_growth_pct': 6, 'quarterly_revenue_growth_pct': 6,
        'pe_ratio': 30,
    }

    tier, failed = score_fundamentals_large_cap(row, previous_fundamentals_row=None)

    assert tier == 'silver'
    assert failed == ['PE range']


def test_score_fundamentals_large_cap_roce_only_failure_is_bronze():
    row = {
        **PASSING_ROW, 'quarterly_profit_growth_pct': 6, 'quarterly_revenue_growth_pct': 6,
        'roce_pct': -1,
    }

    tier, failed = score_fundamentals_large_cap(row, previous_fundamentals_row=None)

    assert tier == 'bronze'
    assert failed == ['ROCE']


def test_score_fundamentals_large_cap_growth_failure_is_excluded_outright():
    # Growth isn't in SILVER_ELIGIBLE_CRITERIA/BRONZE_ELIGIBLE_CRITERIA --
    # failing it (even under the lower large-cap floor) still means outright
    # exclusion, same as every other non-forgiven criterion.
    row = {**PASSING_ROW, 'quarterly_profit_growth_pct': 2, 'quarterly_revenue_growth_pct': 2}

    tier, failed = score_fundamentals_large_cap(row, previous_fundamentals_row=None)

    assert tier is None
    assert 'quarterly profit growth' in failed


def test_score_fundamentals_large_cap_uses_industry_benchmark_when_given():
    row = {**PASSING_ROW, 'quarterly_profit_growth_pct': 6, 'quarterly_revenue_growth_pct': 6, 'pe_ratio': 35}
    benchmark = {'pe_ratio': {'avg': 30, 'count': 5}, 'price_to_book': None}

    tier, failed = score_fundamentals_large_cap(row, previous_fundamentals_row=None, industry_benchmarks=benchmark)

    assert tier == 'golden'
    assert failed == []


# --- Promoter pledge hard disqualifier (mid-cap and large-cap) -------------

def test_promoter_pledge_max_is_ten_percent():
    assert PROMOTER_PLEDGE_MAX_PCT == 10


def test_twelve_percent_pledge_disqualifies_mid_cap_despite_every_other_strength():
    row = {**PASSING_ROW, 'promoter_pledge_pct': 12}

    passes, failed = evaluate_fundamentals(row, PASSING_PREVIOUS_ROW)

    assert passes is False
    assert 'promoter pledge' in failed

    tier, failed = classify_fundamental_tier(row, PASSING_PREVIOUS_ROW)
    assert tier is None  # never forgiven at silver or bronze, unlike ROCE/ROA


def test_twelve_percent_pledge_disqualifies_large_cap_despite_every_other_strength():
    row = {
        **PASSING_ROW, 'quarterly_profit_growth_pct': 6, 'quarterly_revenue_growth_pct': 6,
        'promoter_pledge_pct': 12,
    }

    passes, failed = evaluate_fundamentals_large_cap(row, PASSING_PREVIOUS_ROW)

    assert passes is False
    assert 'promoter pledge' in failed

    tier, failed = score_fundamentals_large_cap(row, PASSING_PREVIOUS_ROW)
    assert tier is None


def test_pledge_exactly_at_the_threshold_disqualifies():
    row = {**PASSING_ROW, 'promoter_pledge_pct': 10}

    passes, failed = evaluate_fundamentals(row, PASSING_PREVIOUS_ROW)

    assert passes is False
    assert 'promoter pledge' in failed


def test_pledge_just_below_the_threshold_does_not_disqualify():
    row = {**PASSING_ROW, 'promoter_pledge_pct': 9.9}

    passes, failed = evaluate_fundamentals(row, PASSING_PREVIOUS_ROW)

    assert passes is True
    assert failed == []


def test_null_pledge_is_not_disqualifying_mid_cap():
    row = {**PASSING_ROW, 'promoter_pledge_pct': None}

    passes, failed = evaluate_fundamentals(row, PASSING_PREVIOUS_ROW)

    assert passes is True
    assert 'promoter pledge' not in failed


def test_missing_pledge_key_entirely_is_not_disqualifying_mid_cap():
    # PASSING_ROW itself never sets promoter_pledge_pct at all -- .get()
    # returns None the same as an explicit None, must not be disqualifying.
    assert 'promoter_pledge_pct' not in PASSING_ROW

    passes, failed = evaluate_fundamentals(PASSING_ROW, PASSING_PREVIOUS_ROW)

    assert passes is True
    assert failed == []


def test_null_pledge_is_not_disqualifying_large_cap():
    row = {**PASSING_ROW, 'quarterly_profit_growth_pct': 6, 'quarterly_revenue_growth_pct': 6,
           'promoter_pledge_pct': None}

    passes, failed = evaluate_fundamentals_large_cap(row, PASSING_PREVIOUS_ROW)

    assert passes is True
    assert failed == []


def test_pledge_failure_combined_with_a_forgivable_criterion_is_still_excluded():
    # Pledge is not in SILVER_ELIGIBLE_CRITERIA/BRONZE_ELIGIBLE_CRITERIA --
    # failing it alongside something silver would otherwise forgive (PE)
    # must still mean outright exclusion, not silver.
    row = {**PASSING_ROW, 'pe_ratio': 30, 'promoter_pledge_pct': 15}

    tier, failed = classify_fundamental_tier(row, previous_fundamentals_row=None)

    assert tier is None
    assert set(failed) == {'PE range', 'promoter pledge'}


# --- score_institutional_holding stub (unwired) -----------------------------

def test_score_institutional_holding_stub_always_returns_zero():
    assert score_institutional_holding({'fii_holding_pct': 25, 'dii_holding_pct': 15}) == 0
    assert score_institutional_holding({}) == 0
    assert score_institutional_holding({'fii_holding_pct': None}) == 0


def test_score_institutional_holding_is_not_referenced_by_the_main_evaluators():
    # Confirms the stub is genuinely unwired -- a row with terrible FII/DII
    # holding must not be penalized by evaluate_fundamentals/
    # evaluate_fundamentals_large_cap in this phase. previous_fundamentals_row=
    # None so this only isolates the (nonexistent) level check, not the
    # separate, pre-existing FII holding TREND check (which would otherwise
    # itself fail here since 0 isn't an increase over PASSING_PREVIOUS_ROW's
    # 15 -- unrelated to score_institutional_holding).
    row = {**PASSING_ROW, 'fii_holding_pct': 0, 'dii_holding_pct': 0}
    passes, failed = evaluate_fundamentals(row, previous_fundamentals_row=None)
    assert passes is True

    row_large_cap = {**row, 'quarterly_profit_growth_pct': 6, 'quarterly_revenue_growth_pct': 6}
    passes, failed = evaluate_fundamentals_large_cap(row_large_cap, previous_fundamentals_row=None)
    assert passes is True


# --- compute_holding_percentiles diagnostic ---------------------------------

def test_compute_holding_percentiles_on_synthetic_data():
    # 1..100 -> well-known percentile positions, easy to verify by hand.
    rows = [{'fii_holding_pct': v} for v in range(1, 101)]

    result = compute_holding_percentiles(rows, 'fii_holding_pct')

    assert result['count'] == 100
    assert result['p50'] == 50.5   # linear interpolation between 50 and 51
    assert result['p10'] < result['p25'] < result['p50'] < result['p75'] < result['p90']


def test_compute_holding_percentiles_excludes_none_values_not_zero():
    rows = [
        {'fii_holding_pct': 10}, {'fii_holding_pct': None}, {'fii_holding_pct': 20},
        {'fii_holding_pct': None}, {'fii_holding_pct': 30},
    ]

    result = compute_holding_percentiles(rows, 'fii_holding_pct')

    assert result['count'] == 3  # the two Nones are excluded, not counted as 0
    assert result['p50'] == 20


def test_compute_holding_percentiles_empty_input_returns_none_percentiles():
    result = compute_holding_percentiles([], 'fii_holding_pct')
    assert result == {'count': 0, 'p10': None, 'p25': None, 'p50': None, 'p75': None, 'p90': None}


def test_compute_holding_percentiles_all_none_returns_none_percentiles():
    rows = [{'fii_holding_pct': None}, {'fii_holding_pct': None}]
    result = compute_holding_percentiles(rows, 'fii_holding_pct')
    assert result['count'] == 0
    assert result['p50'] is None


def test_compute_holding_percentiles_works_for_dii_field_too():
    rows = [{'dii_holding_pct': 5}, {'dii_holding_pct': 15}, {'dii_holding_pct': 25}]

    result = compute_holding_percentiles(rows, 'dii_holding_pct')

    assert result['count'] == 3
    assert result['p50'] == 15


def test_compute_holding_percentiles_single_value():
    rows = [{'fii_holding_pct': 42}]
    result = compute_holding_percentiles(rows, 'fii_holding_pct')
    assert result == {'count': 1, 'p10': 42, 'p25': 42, 'p50': 42, 'p75': 42, 'p90': 42}


def test_compute_holding_percentiles_mid_cap_and_large_cap_pools_are_independent():
    # Mirrors the real diagnostic's use case: same function, two separate
    # calls, one per pool -- confirms there's no shared/leaked state.
    mid_cap_rows = [{'fii_holding_pct': v} for v in (5, 10, 15)]
    large_cap_rows = [{'fii_holding_pct': v} for v in (30, 40, 50)]

    mid_cap_result = compute_holding_percentiles(mid_cap_rows, 'fii_holding_pct')
    large_cap_result = compute_holding_percentiles(large_cap_rows, 'fii_holding_pct')

    assert mid_cap_result['p50'] == 10
    assert large_cap_result['p50'] == 40
