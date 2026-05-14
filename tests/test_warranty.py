import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from warranty import calculate_warranty_risk_analysis


def _trials(claim_prob: float, n: int = 1000) -> dict:
    """Build a raw_trials dict where exactly claim_prob fraction of trials had an SA."""
    n_with = int(round(n * claim_prob))
    assessment_yr_1_5 = [1.0] * n_with + [0.0] * (n - n_with)
    return {"assessment_yr_1_5": assessment_yr_1_5}


def test_warranty_returns_none_if_no_trials():
    result = calculate_warranty_risk_analysis(
        raw_trials={"assessment_yr_1_5": []},
        num_units=10,
        standard_price=10_000.0,
    )
    assert result is None


def test_payout_is_full_coverage_no_deductible():
    # 10 units × $500 coverage_per_unit = $5,000 total coverage
    result = calculate_warranty_risk_analysis(
        raw_trials=_trials(0.5),
        num_units=10,
        standard_price=10_000.0,
    )
    t = result["target_terms"]
    s = result["stress_results"]
    assert t["coverage_total"] == 5_000.0
    assert t["coverage_per_unit"] == 500.0
    assert "deductible" not in t           # deductible removed
    assert s["payout_per_claim"] == 5_000.0  # full coverage
    assert s["payout_per_unit"] == 500.0     # coverage_per_unit


def test_verdict_good_at_low_loss_ratio():
    # premium = 0.50 × 10k = 5,000. coverage = 5,000.
    # claim_prob 0.3 → expected_payout = 0.3 × 5000 = 1,500 → LR = 0.30
    result = calculate_warranty_risk_analysis(
        raw_trials=_trials(0.3),
        num_units=10,
        standard_price=10_000.0,
    )
    assert result["verdict"] == "good"
    assert result["verdict_label"] == "Good property to insure"
    assert result["verdict_color"] == "success"
    # No premium adjustment in Base scenario
    assert result["target_terms"]["warranty_premium_displayed"] == \
           result["target_terms"]["warranty_premium_default"]


def test_verdict_moderate_at_mid_loss_ratio():
    # claim_prob 0.6 → expected_payout = 0.6 × 5000 = 3,000 → LR = 0.60 → moderate
    result = calculate_warranty_risk_analysis(
        raw_trials=_trials(0.6),
        num_units=10,
        standard_price=10_000.0,
    )
    assert result["verdict"] == "moderate"
    assert result["verdict_color"] == "warning"
    # Displayed premium = default × 1.25
    t = result["target_terms"]
    assert abs(t["warranty_premium_displayed"] - t["warranty_premium_default"] * 1.25) < 0.01
    # Displayed LR recalculated against the higher premium → lower than default
    assert result["loss_ratio_displayed"] < result["loss_ratio_default"]


def test_verdict_not_eligible_at_high_loss_ratio():
    # claim_prob 0.9 → expected_payout = 4,500 → LR = 0.90 → not_eligible
    result = calculate_warranty_risk_analysis(
        raw_trials=_trials(0.9),
        num_units=10,
        standard_price=10_000.0,
    )
    assert result["verdict"] == "not_eligible"
    assert result["verdict_color"] == "danger"
    # No premium displayed for ineligible: should equal default (no adjustment)
    t = result["target_terms"]
    assert t["warranty_premium_displayed"] == t["warranty_premium_default"]


def test_full_funding_discount_when_good():
    # Same trials as test_verdict_good, but with funding_model="full"
    result = calculate_warranty_risk_analysis(
        raw_trials=_trials(0.3),
        num_units=10,
        standard_price=10_000.0,
        funding_model="full",
    )
    assert result["verdict"] == "good"
    t = result["target_terms"]
    # -20% discount applied to displayed premium
    assert abs(t["warranty_premium_displayed"] - t["warranty_premium_default"] * 0.80) < 0.01
    assert result["applied_full_funding_discount"] is True
    # Loss ratio shown stays at default (not recalculated) per spec
    assert result["loss_ratio_displayed"] == result["loss_ratio_default"]


def test_no_full_funding_discount_when_moderate():
    # Moderate verdict → no discount even if scenario=full
    result = calculate_warranty_risk_analysis(
        raw_trials=_trials(0.6),
        num_units=10,
        standard_price=10_000.0,
        funding_model="full",
    )
    assert result["verdict"] == "moderate"
    assert result["applied_full_funding_discount"] is False
    # Moderate +25% adjustment applies regardless of funding model
    t = result["target_terms"]
    assert abs(t["warranty_premium_displayed"] - t["warranty_premium_default"] * 1.25) < 0.01


def test_base_scenario_never_gets_discount():
    # Base scenario, Good verdict — no discount
    result = calculate_warranty_risk_analysis(
        raw_trials=_trials(0.3),
        num_units=10,
        standard_price=10_000.0,
        funding_model="base",
    )
    assert result["verdict"] == "good"
    assert result["applied_full_funding_discount"] is False
    t = result["target_terms"]
    assert t["warranty_premium_displayed"] == t["warranty_premium_default"]


def test_verdict_boundary_at_40_pct_is_good():
    # LR exactly at 0.40 should be Good (≤ 0.40 boundary)
    # premium=5000, claim_prob=0.4 → expected_payout=2000 → LR=0.40
    result = calculate_warranty_risk_analysis(
        raw_trials=_trials(0.4),
        num_units=10,
        standard_price=10_000.0,
    )
    assert abs(result["loss_ratio_default"] - 0.40) < 1e-9
    assert result["verdict"] == "good"


def test_verdict_boundary_at_80_pct_is_moderate():
    # LR exactly at 0.80 should be Moderate (≤ 0.80 boundary)
    result = calculate_warranty_risk_analysis(
        raw_trials=_trials(0.8),
        num_units=10,
        standard_price=10_000.0,
    )
    assert abs(result["loss_ratio_default"] - 0.80) < 1e-9
    assert result["verdict"] == "moderate"
