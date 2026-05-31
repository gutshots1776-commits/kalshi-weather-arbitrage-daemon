"""Unit tests for the pure statistical core in kalshi.probability.

These functions price real-money contracts, so they are the highest-value code
to pin down. Everything here is deterministic and network-free.
"""
import math
from datetime import datetime, timedelta

import pytest

from kalshi.probability import (
    normal_cdf,
    market_adjusted_fair,
    calculate_confidence_score,
    fair_probability,
    kelly_size,
    detect_contract_type,
    parse_event_date,
)
from kalshi.config import MAX_CONTRACTS


# ── normal_cdf ───────────────────────────────────────────────────────────

def test_normal_cdf_center_is_half():
    assert normal_cdf(0.0) == pytest.approx(0.5)


def test_normal_cdf_known_quantiles():
    # ~95% of mass below +1.645σ, ~97.5% below +1.96σ
    assert normal_cdf(1.645) == pytest.approx(0.95, abs=1e-3)
    assert normal_cdf(1.96) == pytest.approx(0.975, abs=1e-3)


def test_normal_cdf_is_symmetric():
    for x in (0.3, 1.0, 2.5):
        assert normal_cdf(-x) == pytest.approx(1.0 - normal_cdf(x), abs=1e-12)


def test_normal_cdf_monotonic_and_bounded():
    prev = 0.0
    for x in [-5, -2, -1, 0, 1, 2, 5]:
        cur = normal_cdf(x)
        assert 0.0 <= cur <= 1.0
        assert cur >= prev
        prev = cur


# ── market_adjusted_fair (log-odds Bayesian blend) ───────────────────────

def test_blend_full_market_weight_returns_market():
    # model_weight=0 → result is the (clamped) market probability
    assert market_adjusted_fair(0.9, 0.4, model_weight=0.0) == pytest.approx(0.4, abs=1e-9)


def test_blend_full_model_weight_returns_model():
    assert market_adjusted_fair(0.7, 0.2, model_weight=1.0) == pytest.approx(0.7, abs=1e-9)


def test_blend_equal_inputs_is_identity():
    assert market_adjusted_fair(0.6, 0.6, model_weight=0.3) == pytest.approx(0.6, abs=1e-9)


def test_blend_is_between_inputs():
    blended = market_adjusted_fair(0.8, 0.4, model_weight=0.3)
    assert 0.4 < blended < 0.8


def test_blend_monotonic_in_model_weight():
    # As we trust the (higher) model more, the blend moves toward it.
    weights = [0.0, 0.25, 0.5, 0.75, 1.0]
    vals = [market_adjusted_fair(0.8, 0.4, w) for w in weights]
    assert vals == sorted(vals)


def test_blend_clamps_extreme_probabilities():
    # Inputs of 0/1 are clamped to [0.02, 0.98] to keep the logit finite.
    out = market_adjusted_fair(1.0, 1.0, model_weight=0.5)
    assert out == pytest.approx(0.98, abs=1e-9)
    out_low = market_adjusted_fair(0.0, 0.0, model_weight=0.5)
    assert out_low == pytest.approx(0.02, abs=1e-9)


def test_blend_always_in_unit_interval():
    for mp in (0.01, 0.3, 0.5, 0.99):
        for kp in (0.01, 0.5, 0.95):
            out = market_adjusted_fair(mp, kp, 0.3)
            assert 0.0 < out < 1.0


# ── calculate_confidence_score ───────────────────────────────────────────

def test_confidence_zero_when_no_ensemble():
    assert calculate_confidence_score({}, 75.0, 2.0) == 0.0


def test_confidence_single_provider_base():
    details = {"provider_count": 1, "individual_forecasts": {"NOAA": 75.0}}
    assert calculate_confidence_score(details, 75.0, 2.0) == pytest.approx(0.7)


def test_confidence_perfect_agreement_is_max():
    details = {"provider_count": 3,
               "individual_forecasts": {"A": 75.0, "B": 75.0, "C": 75.0}}
    assert calculate_confidence_score(details, 75.0, 2.0) == pytest.approx(1.0)


def test_confidence_drops_with_disagreement():
    spread = {"provider_count": 2, "individual_forecasts": {"A": 70.0, "B": 80.0}}
    tight = {"provider_count": 2, "individual_forecasts": {"A": 74.0, "B": 76.0}}
    assert calculate_confidence_score(spread, 75.0, 2.0) < calculate_confidence_score(tight, 75.0, 2.0)


def test_confidence_in_unit_interval():
    details = {"provider_count": 5,
               "individual_forecasts": {"A": 70, "B": 72, "C": 75, "D": 78, "E": 90}}
    score = calculate_confidence_score(details, 77.0, 2.0)
    assert 0.0 <= score <= 1.0


# ── fair_probability (CDF strike geometry) ───────────────────────────────

def test_fair_prob_less_far_below_cap_is_near_one():
    # Forecast well under the cap → P(high < cap) ≈ 1
    p = fair_probability(60.0, {}, None, 80.0, std=2.0, days_ahead=2, strike_type="less")
    assert p > 0.99


def test_fair_prob_less_far_above_cap_is_near_zero():
    p = fair_probability(95.0, {}, None, 80.0, std=2.0, days_ahead=2, strike_type="less")
    assert p < 0.01


def test_fair_prob_greater_is_complement_of_less_at_same_strike():
    strike = 75.0
    p_less = fair_probability(73.0, {}, None, strike, std=2.0, days_ahead=2, strike_type="less")
    p_greater = fair_probability(73.0, {}, strike, None, std=2.0, days_ahead=2, strike_type="greater")
    assert p_less + p_greater == pytest.approx(1.0, abs=1e-9)


def test_fair_prob_at_strike_is_half():
    # Forecast exactly at the cap → P(less) = 0.5
    p = fair_probability(80.0, {}, None, 80.0, std=2.0, days_ahead=2, strike_type="less")
    assert p == pytest.approx(0.5, abs=1e-9)


def test_fair_prob_between_is_highest_when_centered():
    floor_s, cap_s = 70.0, 80.0
    centered = fair_probability(75.0, {}, floor_s, cap_s, std=2.0, days_ahead=2, strike_type="between")
    offset = fair_probability(85.0, {}, floor_s, cap_s, std=2.0, days_ahead=2, strike_type="between")
    assert 0.0 < offset < centered <= 1.0


def test_fair_prob_lead_time_pulls_toward_half():
    # Larger days_ahead widens the std, pulling a non-trivial probability toward 0.5.
    near = fair_probability(77.0, {}, None, 80.0, std=2.0, days_ahead=0, strike_type="less")
    far = fair_probability(77.0, {}, None, 80.0, std=2.0, days_ahead=5, strike_type="less")
    assert abs(near - 0.5) > abs(far - 0.5)


def test_fair_prob_unknown_strike_type_returns_half():
    assert fair_probability(75.0, {}, 70.0, 80.0, std=2.0, strike_type="banana") == 0.5


def test_fair_prob_missing_forecast_returns_half():
    assert fair_probability(None, {}, None, 80.0, strike_type="less") == 0.5


def test_fair_prob_zero_degree_forecast_is_not_treated_as_missing():
    # A 0.0°F forecast well below an 80°F cap should price near 1.0, not 0.5.
    # (Regression guard for the `if not forecast_temp` truthiness footgun.)
    assert fair_probability(0.0, {}, None, 80.0, std=2.0, days_ahead=2, strike_type="less") > 0.99


def test_fair_prob_zero_degree_forecast_across_strike_types():
    # 0.0°F vs an 80°F cap: P(less) ~ 1, P(greater above 80) ~ 0, and a band
    # well above 0 has ~0 mass — none of these should collapse to the 0.5
    # "missing forecast" sentinel.
    assert fair_probability(0.0, {}, None, 80.0, std=2.0, days_ahead=2, strike_type="less") > 0.99
    assert fair_probability(0.0, {}, 80.0, None, std=2.0, days_ahead=2, strike_type="greater") < 0.01
    assert fair_probability(0.0, {}, 70.0, 80.0, std=2.0, days_ahead=2, strike_type="between") < 0.01


def test_fair_prob_nan_forecast_returns_half():
    # A non-finite forecast (e.g. a provider NaN) must not propagate to a NaN
    # price; it is treated as missing.
    assert fair_probability(float("nan"), {}, None, 80.0, std=2.0, strike_type="less") == 0.5


def test_fair_prob_missing_required_strike_returns_half():
    # A valid forecast but a None strike for the requested side returns 0.5
    # instead of raising (the 0.0 fix makes valid forecasts reach this arithmetic).
    assert fair_probability(50.0, {}, None, None, std=2.0, strike_type="less") == 0.5
    assert fair_probability(50.0, {}, None, None, std=2.0, strike_type="greater") == 0.5
    assert fair_probability(50.0, {}, 70.0, None, std=2.0, strike_type="between") == 0.5


# ── kelly_size (quarter-Kelly for binary odds) ───────────────────────────

def test_kelly_known_value_then_capped():
    # p=0.6, price=50 → b=1, f*=(0.6*1-0.4)/1=0.2, quarter=0.05.
    # contracts = floor(10000 * 0.05 / 50) = 10, capped to MAX_CONTRACTS.
    assert kelly_size(0.6, 50, 10000, fraction=0.25) == min(10, MAX_CONTRACTS)


def test_kelly_no_edge_is_zero():
    # p == breakeven price → f* = 0 → no position
    assert kelly_size(0.5, 50, 10000, fraction=0.25) == 0


def test_kelly_negative_edge_is_zero():
    assert kelly_size(0.3, 60, 10000, fraction=0.25) == 0


def test_kelly_rejects_degenerate_inputs():
    assert kelly_size(0.0, 50, 10000) == 0
    assert kelly_size(1.0, 50, 10000) == 0
    assert kelly_size(0.6, 0, 10000) == 0


def test_kelly_respects_max_contracts():
    # Huge edge + bankroll must still cap at MAX_CONTRACTS.
    assert kelly_size(0.95, 10, 10_000_000, fraction=0.25) == MAX_CONTRACTS


def test_kelly_scales_with_bankroll_until_cap():
    small = kelly_size(0.7, 40, 200, fraction=0.25)
    big = kelly_size(0.7, 40, 5000, fraction=0.25)
    assert small <= big <= MAX_CONTRACTS


# ── contract / date helpers ──────────────────────────────────────────────

def test_detect_contract_type():
    assert detect_contract_type("KXHIGHTPHX-25JAN15-T80") == "threshold"
    assert detect_contract_type("KXHIGHTPHX-25JAN15-B7577") == "bracket"
    assert detect_contract_type("KXHIGHTPHX-25JAN15") is None


def test_parse_event_date_explicit_month_day():
    d = parse_event_date("Highest temperature in Phoenix on Jan 15")
    assert d is not None and (d.month, d.day) == (1, 15)


def test_parse_event_date_today_tomorrow():
    today = datetime.now().date()
    assert parse_event_date("High temp today").date() == today
    assert parse_event_date("High temp tomorrow").date() == today + timedelta(days=1)


def test_parse_event_date_unparseable_returns_none():
    assert parse_event_date("no date here") is None
