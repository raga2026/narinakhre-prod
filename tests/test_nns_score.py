from stoqbell.utils.nns_score import (
    NNS_BRONZE_MIN,
    NNS_GOLDEN_MIN,
    NNS_SILVER_MIN,
    WEIGHTS,
    compute_nns_score,
    nns_tier,
)

# Every sub-score at its ceiling/ideal -- should score a perfect (or
# near-perfect) 10.0.
EXCELLENT_CANDIDATE = {
    'pe_ratio': 20,       # center of the flat 15-25 fallback band
    'peg_ratio': 0.0,     # as low as possible
    'opm_pct': 40,        # at/above the 40% ceiling
    'roce_pct': 25,       # at the excellent ceiling
    'roa_pct': 15,        # at the excellent ceiling
    'quarterly_profit_growth_pct': 30,
    'quarterly_revenue_growth_pct': 30,
    'price_to_book': 0.0,
    'reserves_to_debt_ratio': 2.0,  # at the excellent ceiling
    'free_cash_flow': 1.0,          # any positive value scores full credit
    'rsi_14': 52.5,        # exact center of 40-65
    'promoter_holding_pct': 60,
    'fii_holding_pct': 20,
}
EXCELLENT_PREVIOUS = {'promoter_holding_pct': 55, 'fii_holding_pct': 15}

# Every sub-score at its worst (floor or missing) -- should score 0.0.
WORST_CANDIDATE = {
    'pe_ratio': None, 'peg_ratio': 5.0, 'opm_pct': 0, 'roce_pct': 0, 'roa_pct': 0,
    'quarterly_profit_growth_pct': 0, 'quarterly_revenue_growth_pct': 0,
    'price_to_book': 999, 'reserves_to_debt_ratio': 0, 'free_cash_flow': -1.0, 'rsi_14': None,
    'promoter_holding_pct': None, 'fii_holding_pct': None,
}


def test_excellent_candidate_scores_a_perfect_ten():
    # No cross_status and no news_sentiment_score here -- golden_cross_bonus
    # and news_sentiment_bonus are both 0.0, the other twelve are all 1.0
    # (see the golden-cross- and news-sentiment-specific tests below for
    # the bonuses themselves).
    score, breakdown = compute_nns_score(EXCELLENT_CANDIDATE, EXCELLENT_PREVIOUS)
    assert score == 10.0
    assert breakdown['golden_cross_bonus'] == 0.0
    assert breakdown['news_sentiment_bonus'] == 0.0
    assert all(v == 1.0 for k, v in breakdown.items() if k not in ('golden_cross_bonus', 'news_sentiment_bonus'))


def test_worst_candidate_scores_zero():
    score, breakdown = compute_nns_score(WORST_CANDIDATE, previous_fundamentals_row=None)
    assert score == 0.0
    assert all(v == 0.0 for v in breakdown.values())


def test_score_is_a_single_decimal_place():
    score, _ = compute_nns_score({**EXCELLENT_CANDIDATE, 'roce_pct': 12.34}, EXCELLENT_PREVIOUS)
    assert score == round(score, 1)


def test_twelve_sub_scores_are_all_present_in_breakdown():
    _, breakdown = compute_nns_score(EXCELLENT_CANDIDATE, EXCELLENT_PREVIOUS)
    assert set(breakdown.keys()) == {
        'pe_fit', 'peg', 'opm', 'roce', 'roa', 'profit_growth', 'revenue_growth',
        'price_to_book_fit', 'reserves_to_debt', 'fcf', 'rsi_position', 'holding_trend',
        'golden_cross_bonus', 'news_sentiment_bonus',
    }


# --- golden_cross_bonus --------------------------------------------------

def test_golden_cross_adds_the_bonus_for_an_otherwise_middling_candidate():
    middling = {
        'pe_ratio': 20, 'peg_ratio': 2.0, 'opm_pct': 0, 'roce_pct': 0, 'roa_pct': 0,
        'quarterly_profit_growth_pct': 0, 'quarterly_revenue_growth_pct': 0,
        'price_to_book': 999, 'rsi_14': None, 'promoter_holding_pct': None, 'fii_holding_pct': None,
    }
    without_cross = compute_nns_score(middling, previous_fundamentals_row=None)
    with_cross = compute_nns_score({**middling, 'cross_status': 'golden_cross'}, previous_fundamentals_row=None)

    assert with_cross[1]['golden_cross_bonus'] == 0.5
    assert with_cross[0] == round(without_cross[0] + 0.5, 1)


def test_golden_cross_bonus_is_zero_for_death_cross_or_no_cross_status():
    row = {**WORST_CANDIDATE, 'cross_status': 'death_cross'}
    _, breakdown = compute_nns_score(row, previous_fundamentals_row=None)
    assert breakdown['golden_cross_bonus'] == 0.0

    _, breakdown = compute_nns_score(WORST_CANDIDATE, previous_fundamentals_row=None)
    assert breakdown['golden_cross_bonus'] == 0.0


def test_golden_cross_bonus_does_not_push_score_above_ten():
    already_perfect = {**EXCELLENT_CANDIDATE, 'cross_status': 'golden_cross'}
    score, breakdown = compute_nns_score(already_perfect, EXCELLENT_PREVIOUS)
    assert breakdown['golden_cross_bonus'] == 0.5
    assert score == 10.0  # capped, not 10.5


# --- news_sentiment_bonus -------------------------------------------------

def test_positive_sentiment_adds_to_the_score_for_a_middling_candidate():
    middling = {
        'pe_ratio': 20, 'peg_ratio': 2.0, 'opm_pct': 0, 'roce_pct': 0, 'roa_pct': 0,
        'quarterly_profit_growth_pct': 0, 'quarterly_revenue_growth_pct': 0,
        'price_to_book': 999, 'rsi_14': None, 'promoter_holding_pct': None, 'fii_holding_pct': None,
    }
    without_news = compute_nns_score(middling, previous_fundamentals_row=None)
    with_positive_news = compute_nns_score(middling, previous_fundamentals_row=None, news_sentiment_score=1.0)

    assert with_positive_news[1]['news_sentiment_bonus'] == 0.5
    assert with_positive_news[0] == round(without_news[0] + 0.5, 1)


def test_negative_sentiment_subtracts_from_the_score():
    middling = {
        'pe_ratio': 20, 'peg_ratio': 2.0, 'opm_pct': 0, 'roce_pct': 0, 'roa_pct': 0,
        'quarterly_profit_growth_pct': 0, 'quarterly_revenue_growth_pct': 0,
        'price_to_book': 999, 'rsi_14': None, 'promoter_holding_pct': None, 'fii_holding_pct': None,
    }
    without_news = compute_nns_score(middling, previous_fundamentals_row=None)
    with_negative_news = compute_nns_score(middling, previous_fundamentals_row=None, news_sentiment_score=-1.0)

    assert with_negative_news[1]['news_sentiment_bonus'] == -0.5
    assert with_negative_news[0] == round(without_news[0] - 0.5, 1)


def test_no_news_score_or_none_is_treated_as_neutral():
    _, breakdown_omitted = compute_nns_score(WORST_CANDIDATE, previous_fundamentals_row=None)
    _, breakdown_explicit_none = compute_nns_score(
        WORST_CANDIDATE, previous_fundamentals_row=None, news_sentiment_score=None
    )
    _, breakdown_zero = compute_nns_score(
        WORST_CANDIDATE, previous_fundamentals_row=None, news_sentiment_score=0.0
    )
    assert breakdown_omitted['news_sentiment_bonus'] == 0.0
    assert breakdown_explicit_none['news_sentiment_bonus'] == 0.0
    assert breakdown_zero['news_sentiment_bonus'] == 0.0


def test_news_sentiment_bonus_does_not_push_score_above_ten():
    already_perfect = {**EXCELLENT_CANDIDATE, 'cross_status': 'golden_cross'}
    score, breakdown = compute_nns_score(already_perfect, EXCELLENT_PREVIOUS, news_sentiment_score=1.0)
    assert breakdown['news_sentiment_bonus'] == 0.5
    assert score == 10.0  # capped, not 11.0


def test_negative_news_sentiment_does_not_push_score_below_zero():
    score, breakdown = compute_nns_score(WORST_CANDIDATE, previous_fundamentals_row=None, news_sentiment_score=-1.0)
    assert breakdown['news_sentiment_bonus'] == -0.5
    assert score == 0.0  # floored, not negative


# --- pe_fit -------------------------------------------------------------

def test_pe_exactly_at_band_edges_scores_zero_for_pe_fit():
    _, low = compute_nns_score({**EXCELLENT_CANDIDATE, 'pe_ratio': 15})
    _, high = compute_nns_score({**EXCELLENT_CANDIDATE, 'pe_ratio': 25})
    assert low['pe_fit'] == 0.0
    assert high['pe_fit'] == 0.0


def test_pe_outside_band_scores_zero():
    _, breakdown = compute_nns_score({**EXCELLENT_CANDIDATE, 'pe_ratio': 100})
    assert breakdown['pe_fit'] == 0.0


def test_pe_fit_uses_industry_benchmark_when_trusted():
    # Industry average PE of 40 (0.5x-1.5x = 20-60) -- PE of 40 should be
    # a perfect fit even though it's way outside the flat 15-25 fallback.
    benchmark = {'pe_ratio': {'avg': 40, 'count': 5}, 'price_to_book': None}
    _, breakdown = compute_nns_score({**EXCELLENT_CANDIDATE, 'pe_ratio': 40}, industry_benchmarks=benchmark)
    assert breakdown['pe_fit'] == 1.0


def test_pe_fit_ignores_untrusted_small_sample_industry_benchmark():
    benchmark = {'pe_ratio': {'avg': 40, 'count': 1}, 'price_to_book': None}
    _, breakdown = compute_nns_score({**EXCELLENT_CANDIDATE, 'pe_ratio': 40}, industry_benchmarks=benchmark)
    assert breakdown['pe_fit'] == 0.0  # falls back to flat 15-25 band -- 40 is outside it


# --- peg / opm / roce / roa / growth -------------------------------------

def test_peg_at_or_above_max_scores_zero():
    _, breakdown = compute_nns_score({**EXCELLENT_CANDIDATE, 'peg_ratio': 1.0})
    assert breakdown['peg'] == 0.0


def test_opm_below_silver_floor_scores_zero():
    _, breakdown = compute_nns_score({**EXCELLENT_CANDIDATE, 'opm_pct': 15})
    assert breakdown['opm'] == 0.0


def test_roce_between_floor_and_ceiling_scores_proportionally():
    _, breakdown = compute_nns_score({**EXCELLENT_CANDIDATE, 'roce_pct': 12.5})  # halfway to 25
    assert breakdown['roce'] == 0.5


def test_growth_above_ceiling_still_caps_at_one_not_penalized():
    # Growing 50% shouldn't score worse than growing exactly 30% -- no
    # upper penalty, same "growth is a floor not a range" philosophy as
    # fundamental_screen.py's own pass/fail check.
    _, breakdown = compute_nns_score({**EXCELLENT_CANDIDATE, 'quarterly_profit_growth_pct': 50})
    assert breakdown['profit_growth'] == 1.0


# --- rsi_position ---------------------------------------------------------

def test_rsi_at_zone_edges_scores_zero():
    _, low = compute_nns_score({**EXCELLENT_CANDIDATE, 'rsi_14': 40})
    _, high = compute_nns_score({**EXCELLENT_CANDIDATE, 'rsi_14': 65})
    assert low['rsi_position'] == 0.0
    assert high['rsi_position'] == 0.0


def test_rsi_missing_scores_zero():
    _, breakdown = compute_nns_score({**EXCELLENT_CANDIDATE, 'rsi_14': None})
    assert breakdown['rsi_position'] == 0.0


# --- holding_trend ----------------------------------------------------------

def test_holding_trend_with_no_previous_snapshot_scores_zero():
    _, breakdown = compute_nns_score(EXCELLENT_CANDIDATE, previous_fundamentals_row=None)
    assert breakdown['holding_trend'] == 0.0


def test_holding_trend_full_credit_when_both_promoter_and_fii_improve():
    previous = {'promoter_holding_pct': 60, 'fii_holding_pct': 10}  # promoter stable, FII increasing
    _, breakdown = compute_nns_score(EXCELLENT_CANDIDATE, previous)
    assert breakdown['holding_trend'] == 1.0  # promoter stable-or-increasing (60>=60) AND fii increasing (20>10)


def test_holding_trend_half_credit_for_just_one_of_two_improving():
    previous_bad_promoter = {'promoter_holding_pct': 65, 'fii_holding_pct': 10}  # promoter DECLINED, FII increasing
    _, breakdown = compute_nns_score(EXCELLENT_CANDIDATE, previous_bad_promoter)
    assert breakdown['holding_trend'] == 0.5  # only FII improving


# --- nns_tier ---------------------------------------------------------------

# --- reserves_to_debt ------------------------------------------------------

def test_reserves_to_debt_between_floor_and_ceiling_scores_proportionally():
    _, breakdown = compute_nns_score({**EXCELLENT_CANDIDATE, 'reserves_to_debt_ratio': 1.25})  # halfway 0.5-2.0
    assert breakdown['reserves_to_debt'] == 0.5


def test_reserves_to_debt_missing_scores_zero():
    _, breakdown = compute_nns_score({**EXCELLENT_CANDIDATE, 'reserves_to_debt_ratio': None})
    assert breakdown['reserves_to_debt'] == 0.0


def test_reserves_to_debt_debt_free_sentinel_scores_full_credit():
    # See screener_client.RESERVES_TO_DEBT_DEBT_FREE_SENTINEL -- comfortably
    # above the scoring ceiling, so it reads as "maximally healthy" with no
    # special-case branch needed in the scoring code itself.
    from stoqbell.utils.screener_client import RESERVES_TO_DEBT_DEBT_FREE_SENTINEL
    _, breakdown = compute_nns_score({**EXCELLENT_CANDIDATE, 'reserves_to_debt_ratio': RESERVES_TO_DEBT_DEBT_FREE_SENTINEL})
    assert breakdown['reserves_to_debt'] == 1.0


# --- fcf ---------------------------------------------------------------

def test_fcf_positive_scores_full_credit_negative_or_missing_scores_zero():
    _, positive = compute_nns_score({**EXCELLENT_CANDIDATE, 'free_cash_flow': 0.01})
    _, negative = compute_nns_score({**EXCELLENT_CANDIDATE, 'free_cash_flow': -50})
    _, zero = compute_nns_score({**EXCELLENT_CANDIDATE, 'free_cash_flow': 0})
    _, missing = compute_nns_score({**EXCELLENT_CANDIDATE, 'free_cash_flow': None})
    assert positive['fcf'] == 1.0
    assert negative['fcf'] == 0.0
    assert zero['fcf'] == 0.0
    assert missing['fcf'] == 0.0


# --- weight table (part 3 of the rebalancing instruction) -------------------

def test_weights_sum_to_100():
    assert sum(WEIGHTS.values()) == 100


def test_opm_weight_is_meaningfully_higher_than_fcf_weight():
    assert WEIGHTS['opm'] > WEIGHTS['fcf']
    assert WEIGHTS['opm'] >= WEIGHTS['fcf'] * 1.5  # "meaningfully", not just marginally


def test_opm_outweighs_fcf_in_a_synthetic_comparison():
    # Two candidates, identical except one trades a weak OPM for a strong
    # FCF and the other trades a weak FCF for a strong OPM -- if OPM truly
    # outweighs FCF, the strong-OPM/weak-FCF candidate must score higher.
    strong_opm_weak_fcf = {**EXCELLENT_CANDIDATE, 'opm_pct': 40, 'free_cash_flow': -10}
    weak_opm_strong_fcf = {**EXCELLENT_CANDIDATE, 'opm_pct': 15, 'free_cash_flow': 10}  # 15 = OPM_SILVER_MIN_PCT floor
    score_strong_opm, _ = compute_nns_score(strong_opm_weak_fcf)
    score_weak_opm, _ = compute_nns_score(weak_opm_strong_fcf)
    assert score_strong_opm > score_weak_opm


def test_peg_weight_raised_above_the_old_equal_share():
    # Old scheme: 10 equally-weighted components, PEG included -- 10 out of
    # 100. Confirms the rebalancing actually increased it, per the PEG
    # diagnosis in nns_score.py's own module docstring.
    assert WEIGHTS['peg'] > 10


# --- tier_boundaries ---------------------------------------------------

def test_tier_boundaries():
    assert nns_tier(NNS_GOLDEN_MIN) == 'golden'
    assert nns_tier(NNS_GOLDEN_MIN - 0.1) == 'silver'
    assert nns_tier(NNS_SILVER_MIN) == 'silver'
    assert nns_tier(NNS_SILVER_MIN - 0.1) == 'bronze'
    assert nns_tier(NNS_BRONZE_MIN) == 'bronze'
    assert nns_tier(NNS_BRONZE_MIN - 0.1) is None
    assert nns_tier(0.0) is None
    assert nns_tier(10.0) == 'golden'
