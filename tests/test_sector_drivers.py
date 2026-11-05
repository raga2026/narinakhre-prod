"""Tests for utils/sector_drivers.py -- pure logic, no DB or network."""
import random

from stoqbell.utils.nns_score import compute_nns_score
from stoqbell.utils.sector_drivers import (
    DEFAULT_RULE,
    GATE_PRESSURE,
    compute_driver_shock,
    compute_sector_pressure,
    group_for_industry,
    is_gated,
    pressure_score_adjustment,
    rule_for_industry,
)
from stoqbell.utils.suggestion_engine import is_suggestion_eligible


def _calm_series(n=60, last_move=0.0, seed=1):
    rng = random.Random(seed)
    closes = [100.0]
    for _ in range(n - 1):
        closes.append(closes[-1] * (1 + rng.uniform(-0.01, 0.01)))
    closes.append(closes[-1] * (1 + last_move))
    return closes


def test_industry_matching_specific_before_broad():
    assert group_for_industry('Tyres & Rubber Products') == 'tyres'
    assert group_for_industry('Auto Ancillaries') == 'auto'
    assert group_for_industry('Automobiles - Trucks/Lcv') == 'auto'
    assert group_for_industry('Paints') == 'paints'
    assert group_for_industry('Software & Services') == 'it'
    assert group_for_industry('Private Sector Bank') == 'banks_finance'
    assert group_for_industry('Edible Oil') != 'oil_gas'


def test_unknown_or_missing_industry_uses_default_rule():
    assert rule_for_industry(None) is DEFAULT_RULE
    assert rule_for_industry('') is DEFAULT_RULE
    assert rule_for_industry('Underwater Basket Weaving') is DEFAULT_RULE


def test_driver_shock_detects_large_move():
    shock = compute_driver_shock(_calm_series(last_move=0.05))
    assert shock['change_1d_pct'] > 4.5
    assert shock['zscore'] > 3


def test_driver_shock_calm_day_is_small_z():
    shock = compute_driver_shock(_calm_series(last_move=0.002))
    assert abs(shock['zscore']) < 1.5


def test_driver_shock_needs_history_for_zscore():
    shock = compute_driver_shock([100, 101, 102])
    assert shock['zscore'] is None
    assert shock['change_1d_pct'] > 0
    assert compute_driver_shock([100]) is None
    assert compute_driver_shock([]) is None


def test_driver_shock_ignores_bad_closes():
    shock = compute_driver_shock([None, 100, 0, -5, 105])
    assert shock['value'] == 105.0


def test_crude_spike_is_headwind_for_paints_tailwind_for_oil():
    signals = {'brent': {'zscore': 3.5}}
    paints = compute_sector_pressure(rule_for_industry('Paints'), signals)
    oil = compute_sector_pressure(rule_for_industry('Refineries'), signals)
    assert paints['pressure'] < 0 and paints['label'] == 'headwind'
    assert paints['top_driver'] == 'brent'
    assert oil['pressure'] > 0 and oil['label'] == 'tailwind'


def test_pressure_blends_news_and_realised_move():
    rule = rule_for_industry('Automobiles')
    base = compute_sector_pressure(rule, {'brent': {'zscore': 3.0}})
    worse = compute_sector_pressure(rule, {'brent': {'zscore': 3.0}}, sector_sentiment=-1.0, industry_change_pct=-3.0)
    assert worse['pressure'] < base['pressure']
    assert -1.0 <= worse['pressure'] <= 1.0


def test_pressure_with_no_data_is_neutral_zero():
    result = compute_sector_pressure(rule_for_industry('Automobiles'), {})
    assert result['pressure'] == 0.0
    assert result['label'] == 'neutral'
    assert result['top_driver'] is None


def test_missing_zscore_driver_is_skipped():
    result = compute_sector_pressure(rule_for_industry('Paints'), {'brent': {'zscore': None}})
    assert result['pressure'] == 0.0


def test_score_adjustment_is_asymmetric_and_none_safe():
    assert pressure_score_adjustment(None) == 0.0
    assert pressure_score_adjustment(-1.0) == -1.0
    assert pressure_score_adjustment(1.0) == 0.25
    assert pressure_score_adjustment(0.0) == 0.0


def test_gate_threshold():
    assert is_gated(GATE_PRESSURE) is True
    assert is_gated(GATE_PRESSURE + 0.01) is False
    assert is_gated(None) is False


def _eligible_candidate(**overrides):
    candidate = {'peg_ratio': 0.8, 'volume_trend': 'confirming', 'cross_status': 'golden_cross'}
    candidate.update(overrides)
    return candidate


def test_eligibility_gate_blocks_pressured_sector_only():
    assert is_suggestion_eligible(_eligible_candidate()) is True
    assert is_suggestion_eligible(_eligible_candidate(sector_pressure=None)) is True
    assert is_suggestion_eligible(_eligible_candidate(sector_pressure=-0.2)) is True
    assert is_suggestion_eligible(_eligible_candidate(sector_pressure=-0.7)) is False


def test_nns_score_applies_sector_adjustment():
    candidate = {'pe_ratio': 20, 'peg_ratio': 0.5, 'opm_pct': 25, 'roce_pct': 20, 'roa_pct': 10, 'rsi_14': 52}
    base_score, base_breakdown = compute_nns_score(candidate)
    hit_score, hit_breakdown = compute_nns_score(dict(candidate, sector_pressure=-0.8))
    assert base_breakdown['sector_pressure_adjustment'] == 0.0
    assert hit_breakdown['sector_pressure_adjustment'] == -0.8
    assert hit_score < base_score


def test_probabilities_follow_pressure_and_sum_to_100():
    from stoqbell.utils.sector_drivers import estimate_move_probabilities
    up, down = estimate_move_probabilities(0.8)
    assert up > 50 > down and up + down == 100
    up, down = estimate_move_probabilities(-0.8)
    assert down > 50 > up and up + down == 100
    assert estimate_move_probabilities(0.0) == (50, 50)


def test_probabilities_clamped_and_trend_nudged_and_none_safe():
    from stoqbell.utils.sector_drivers import estimate_move_probabilities
    assert estimate_move_probabilities(1.0) == (85, 15)
    assert estimate_move_probabilities(-1.0) == (15, 85)
    assert estimate_move_probabilities(0.0, 'golden_cross')[0] > 50
    assert estimate_move_probabilities(0.0, 'death_cross')[0] < 50
    assert estimate_move_probabilities(None) == (None, None)
