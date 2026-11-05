"""Sector -> macro-driver map and the pure scoring math behind the "market
drivers" layer (see utils/market_signals.py for the DB/sync side). Pure
logic only, no DB or network access -- same DB-free/DB-orchestration split
as nns_score.py / news_sentiment.py.

Why this exists: a stock can fall for reasons that never show up in its own
price history or its own headlines -- e.g. Ashok Leyland dropping on a crude
oil spike plus a sector-wide sell-off. This module encodes WHICH external
drivers matter to WHICH industries, and turns driver shocks + sector news
sentiment + the sector's own realised move into one -1..1 "sector pressure"
number per industry group.

The sensitivities below are first-pass JUDGMENT CALLS (sign and rough
magnitude of how an industry's earnings/valuation react to a driver), not
fitted coefficients -- flagged here, in one visible table, so they are easy
to find and tune, same convention as the ROCE/ROA ceilings in nns_score.py.
Validate against utils.suggestion_engine's recommendation tracker (were past
stop-outs preceded by a driver shock?) before tightening the buy gate.

Matching is by case-insensitive SUBSTRING against stock_universe.industry
(Screener-style names, which vary: "Auto Ancillaries", "Automobiles - Cars
& Jeeps", "Paints", ...). First matching rule wins, so more specific rules
must come before broader ones. An industry matching nothing falls into
DEFAULT_RULE (market-wide risk only).
"""
import math


# driver key -> display label, Yahoo Finance symbol, and the Google News query
# used to pull driver-specific headlines (see market_signals.sync_market_signals).
DRIVERS = {
    'brent':    {'label': 'Brent crude',      'yahoo': 'BZ=F',      'query': 'Brent crude oil price'},
    'usdinr':   {'label': 'USD/INR',          'yahoo': 'INR=X',     'query': 'rupee dollar exchange rate'},
    'gold':     {'label': 'Gold',             'yahoo': 'GC=F',      'query': 'gold price'},
    'copper':   {'label': 'Copper',           'yahoo': 'HG=F',      'query': 'copper steel metal prices'},
    'natgas':   {'label': 'Natural gas',      'yahoo': 'NG=F',      'query': 'natural gas price LNG'},
    'indiavix': {'label': 'India VIX',        'yahoo': '^INDIAVIX', 'query': 'Sensex Nifty market volatility'},
    'us10y':    {'label': 'US 10Y yield',     'yahoo': '^TNX',      'query': 'US treasury yield Fed'},
}

# A driver's own move only counts as a "shock" worth alerting on at this
# many standard deviations (vs its trailing daily-return volatility).
SHOCK_ZSCORE = 2.0
# z-score at which a driver's contribution saturates at +/-1.0.
ZSCORE_SATURATION = 3.0
# Minimum number of trailing daily returns needed for a meaningful z-score.
MIN_RETURNS_FOR_ZSCORE = 20

# group key -> rule. 'match' are lowercase substrings of the industry name;
# 'drivers' are {driver_key: sensitivity in -1..1} (negative = the industry
# is HURT when the driver rises); 'query' is the sector-news search string.
SECTOR_RULES = [
    {'group': 'airlines', 'match': ('airline', 'aviation', 'air transport'),
     'drivers': {'brent': -1.0, 'usdinr': -0.4}, 'query': 'airlines aviation sector India'},
    {'group': 'tyres', 'match': ('tyre', 'tire', 'rubber'),
     'drivers': {'brent': -0.8}, 'query': 'tyre rubber sector India'},
    {'group': 'paints', 'match': ('paint', 'varnish', 'coating'),
     'drivers': {'brent': -0.9}, 'query': 'paints sector India crude'},
    {'group': 'oil_gas', 'match': ('refiner', 'petroleum', 'oil exploration', 'oil drill', 'oil market', 'oil & gas', 'gas distribution', 'lng'),
     'drivers': {'brent': 0.4, 'natgas': 0.2}, 'query': 'oil and gas sector India'},
    {'group': 'auto', 'match': ('auto', 'vehicle', 'truck', '2/3 wheeler', 'two wheeler', 'commercial veh'),
     'drivers': {'brent': -0.6, 'copper': -0.2}, 'query': 'auto sector India'},
    {'group': 'chemicals', 'match': ('chemical', 'fertili', 'agrochem', 'plastic'),
     'drivers': {'brent': -0.5, 'natgas': -0.3}, 'query': 'chemicals sector India'},
    {'group': 'cement', 'match': ('cement', 'building material'),
     'drivers': {'brent': -0.4, 'natgas': -0.2}, 'query': 'cement sector India'},
    {'group': 'metals', 'match': ('steel', 'iron', 'metal', 'aluminium', 'aluminum', 'mining', 'copper', 'zinc'),
     'drivers': {'copper': 0.6}, 'query': 'steel metals sector India'},
    {'group': 'it', 'match': ('software', 'it services', 'it - ', 'information technology', 'computers'),
     'drivers': {'usdinr': 0.5, 'us10y': -0.2}, 'query': 'IT services sector India'},
    {'group': 'pharma', 'match': ('pharma', 'drug', 'healthcare', 'hospital', 'biotech'),
     'drivers': {'usdinr': 0.3}, 'query': 'pharma sector India'},
    {'group': 'banks_finance', 'match': ('bank', 'finance', 'nbfc', 'insurance', 'broking', 'capital market'),
     'drivers': {'indiavix': -0.4, 'us10y': -0.3}, 'query': 'banking financial sector India'},
    {'group': 'realty', 'match': ('realty', 'real estate', 'housing', 'construction'),
     'drivers': {'us10y': -0.3, 'copper': -0.2, 'brent': -0.2}, 'query': 'real estate sector India'},
    {'group': 'power', 'match': ('power', 'electric util', 'utilities'),
     'drivers': {'natgas': -0.2, 'indiavix': -0.2}, 'query': 'power sector India'},
    {'group': 'capital_goods', 'match': ('capital goods', 'engineering', 'infra', 'industrial', 'electrical equipment'),
     'drivers': {'copper': -0.3, 'brent': -0.3}, 'query': 'infrastructure capital goods sector India'},
    {'group': 'fmcg_consumer', 'match': ('fmcg', 'consumer', 'food', 'beverage', 'retail', 'textile', 'apparel'),
     'drivers': {'brent': -0.3}, 'query': 'FMCG consumer sector India'},
]
DEFAULT_RULE = {'group': 'general', 'match': (), 'drivers': {'indiavix': -0.3}, 'query': 'Sensex Nifty stock market India'}

# Blend of the three pressure components (sums to 1.0). Driver shocks carry
# the most weight because they are the signal the company's own candles and
# headlines can't provide; news and the sector's realised move corroborate.
WEIGHT_DRIVERS = 0.5
WEIGHT_NEWS = 0.25
WEIGHT_MOVE = 0.25
# A sector's average daily % move at which the "move" component saturates.
MOVE_SATURATION_PCT = 3.0

# |pressure| at/below which a sector is 'neutral'.
LABEL_BAND = 0.2
# Pressure at/below which new buy suggestions in the sector are suppressed
# (see suggestion_engine.is_suggestion_eligible).
GATE_PRESSURE = -0.5
# NNS Score adjustment (see nns_score.compute_nns_score): asymmetric on
# purpose -- avoiding a headwind matters more than chasing a tailwind.
HEADWIND_MALUS_MAX = 1.0
TAILWIND_BONUS_MAX = 0.25


def _clamp(value, lo=-1.0, hi=1.0):
    return max(lo, min(hi, value))


def rule_for_industry(industry):
    """The first SECTOR_RULES entry whose 'match' substring appears in the
    industry name (case-insensitive), else DEFAULT_RULE. None/empty industry
    -> DEFAULT_RULE."""
    name = (industry or '').strip().lower()
    if not name:
        return DEFAULT_RULE
    for rule in SECTOR_RULES:
        if any(token in name for token in rule['match']):
            return rule
    return DEFAULT_RULE


def group_for_industry(industry):
    return rule_for_industry(industry)['group']


def all_rules():
    return SECTOR_RULES + [DEFAULT_RULE]


def rule_for_group(group):
    for rule in all_rules():
        if rule['group'] == group:
            return rule
    return None


def compute_driver_shock(closes):
    """closes: daily closing values, OLDEST first (None/<=0 entries dropped).
    Returns {'value', 'change_1d_pct', 'change_5d_pct', 'zscore'} or None
    with fewer than 2 usable closes.

    zscore is the latest 1-day return (or the 5-day return scaled by
    sqrt(5), whichever is larger in magnitude) divided by the standard
    deviation of the PRIOR daily returns -- so a 4% crude move on a day
    crude normally moves 1.3% reads ~3, but the same 4% on a wild week
    reads lower. None when there are fewer than MIN_RETURNS_FOR_ZSCORE
    prior returns or their volatility is zero."""
    series = [float(c) for c in closes if c is not None and float(c) > 0]
    if len(series) < 2:
        return None

    returns = [(series[i] - series[i - 1]) / series[i - 1] for i in range(1, len(series))]
    last_return = returns[-1]
    change_1d = last_return * 100
    change_5d = ((series[-1] - series[-6]) / series[-6] * 100) if len(series) >= 6 else None

    prior = returns[:-1]
    zscore = None
    if len(prior) >= MIN_RETURNS_FOR_ZSCORE:
        mean = sum(prior) / len(prior)
        variance = sum((r - mean) ** 2 for r in prior) / (len(prior) - 1)
        std = variance ** 0.5
        if std > 0:
            z_1d = last_return / std
            z_5d = None
            if change_5d is not None:
                z_5d = (change_5d / 100) / (std * (5 ** 0.5))
            zscore = z_1d if z_5d is None or abs(z_1d) >= abs(z_5d) else z_5d

    return {
        'value': round(series[-1], 4),
        'change_1d_pct': round(change_1d, 3),
        'change_5d_pct': round(change_5d, 3) if change_5d is not None else None,
        'zscore': round(zscore, 3) if zscore is not None else None,
    }


def compute_sector_pressure(rule, driver_signals, sector_sentiment=None, industry_change_pct=None):
    """-1.0..1.0 pressure for one industry group.

    rule: a SECTOR_RULES entry. driver_signals: {driver_key: {'zscore', ...}}
    (see compute_driver_shock; a missing driver or one with no zscore
    contributes nothing). sector_sentiment: -1..1 from
    news_sentiment.compute_company_sentiment over the sector's headlines, or
    None. industry_change_pct: the sector's average realised 1-day % move
    (see industry_growth.compute_industry_changes), or None.

    Returns {'pressure', 'label', 'driver_component', 'news_component',
    'move_component', 'top_driver', 'top_driver_impact'}. top_driver is the
    driver key with the largest |sensitivity * saturated z|, for alert text.

    Driver component = sum(sens_i * sat(z_i)) / sum(|sens_i|) over drivers
    with a z-score -- a sign-weighted average, so crude +3σ on an airline
    (sens -1.0) reads -1.0 and the same move on an oil producer (+0.4)
    reads +1.0."""
    numerator = 0.0
    denominator = 0.0
    top_driver, top_impact = None, 0.0
    for key, sensitivity in rule['drivers'].items():
        z = (driver_signals.get(key) or {}).get('zscore')
        if z is None:
            continue
        impact = sensitivity * _clamp(z / ZSCORE_SATURATION)
        numerator += impact
        denominator += abs(sensitivity)
        if abs(impact) > abs(top_impact):
            top_driver, top_impact = key, impact
    driver_component = numerator / denominator if denominator else 0.0

    news_component = _clamp(sector_sentiment) if sector_sentiment is not None else 0.0
    move_component = _clamp(industry_change_pct / MOVE_SATURATION_PCT) if industry_change_pct is not None else 0.0

    pressure = _clamp(
        WEIGHT_DRIVERS * driver_component + WEIGHT_NEWS * news_component + WEIGHT_MOVE * move_component
    )
    if pressure <= -LABEL_BAND:
        label = 'headwind'
    elif pressure >= LABEL_BAND:
        label = 'tailwind'
    else:
        label = 'neutral'
    return {
        'pressure': round(pressure, 3),
        'label': label,
        'driver_component': round(driver_component, 3),
        'news_component': round(news_component, 3),
        'move_component': round(move_component, 3),
        'top_driver': top_driver,
        'top_driver_impact': round(top_impact, 3),
    }


def pressure_score_adjustment(pressure):
    """NNS Score points (negative = malus) for a sector pressure of
    -1..1; None is treated as 0 (no data -> no adjustment)."""
    if not pressure:
        return 0.0
    if pressure < 0:
        return round(pressure * HEADWIND_MALUS_MAX, 3)
    return round(pressure * TAILWIND_BONUS_MAX, 3)


def is_gated(pressure):
    """True when a sector's pressure is bad enough to suppress NEW buy
    suggestions in it. None (no data) never gates."""
    return pressure is not None and pressure <= GATE_PRESSURE


# ---------------------------------------------------------------------------
# Rise/decline estimate (display only)
# ---------------------------------------------------------------------------

# Steepness of the pressure -> probability curve, the nudge a golden/death
# cross gives the score, and the clamp that stops the estimate from ever
# claiming certainty.
PROBABILITY_STEEPNESS = 2.2
TREND_NUDGE = 0.15
PROBABILITY_FLOOR = 0.15
PROBABILITY_CEILING = 0.85


def estimate_move_probabilities(pressure, cross_status=None):
    """(p_rise, p_decline) as percentages summing to 100, for display on the
    super-admin Market Drivers page.

    This is a HEURISTIC read of the sector's driver/news/move pressure
    (-1..1) plus a small nudge from the stock's own golden/death cross --
    squashed through a logistic curve and clamped to 15-85%. It is NOT a
    calibrated forecast and has never been back-tested; it exists to rank
    which stocks are most exposed and in which direction. None pressure
    (no data) returns (None, None)."""
    if pressure is None:
        return None, None
    score = float(pressure)
    if cross_status == 'golden_cross':
        score += TREND_NUDGE
    elif cross_status == 'death_cross':
        score -= TREND_NUDGE
    p_rise = 1.0 / (1.0 + math.exp(-PROBABILITY_STEEPNESS * score))
    p_rise = min(PROBABILITY_CEILING, max(PROBABILITY_FLOOR, p_rise))
    rise = round(p_rise * 100)
    return rise, 100 - rise
