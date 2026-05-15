import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from warranty import calculate_warranty_risk_analysis


def _trials(claim_prob: float, n: int = 1000) -> dict:
    """Build a raw_trials dict where exactly claim_prob fraction of trials had an SA."""
    n_with = int(round(n * claim_prob))
    assessment_yr_1_5 = [1.0] * n_with + [0.0] * (n - n_with)
    return {"assessment_yr_1_5": assessment_yr_1_5}


# Under the post-2026-05-15 spec:
#   standard_price=10_000, premium_markup_pct=0.50
#     → premium_tier_price = 15_000
#     → warranty_premium    = 5_000
#     → coverage_total      = 2 × 15_000 = 30_000  (10 units → 3000/unit)
# The loss-ratio multiplier therefore becomes:
#     LR = claim_prob × coverage / premium = claim_prob × 6
# Tier cutoffs by claim probability:
#     Good       claim_prob ≤ 0.0667
#     Moderate   0.0667 < claim_prob ≤ 0.1333
#     Not elig.  claim_prob > 0.1333


def test_warranty_returns_none_if_no_trials():
    result = calculate_warranty_risk_analysis(
        raw_trials={"assessment_yr_1_5": []},
        num_units=10,
        standard_price=10_000.0,
    )
    assert result is None


def test_coverage_is_two_times_premium_tier_price():
    result = calculate_warranty_risk_analysis(
        raw_trials=_trials(0.05),
        num_units=10,
        standard_price=10_000.0,
    )
    t = result["target_terms"]
    assert t["premium_tier_price"] == 15_000.0
    assert t["coverage_total"] == 30_000.0      # 2 × 15,000
    assert t["coverage_per_unit"] == 3_000.0    # 30,000 / 10 units


def test_payout_is_full_coverage_no_deductible():
    result = calculate_warranty_risk_analysis(
        raw_trials=_trials(0.05),
        num_units=10,
        standard_price=10_000.0,
    )
    t = result["target_terms"]
    s = result["stress_results"]
    assert t["coverage_total"] == 30_000.0
    assert "deductible" not in t              # deductible removed
    assert s["payout_per_claim"] == 30_000.0  # full coverage
    assert s["payout_per_unit"] == 3_000.0    # coverage_total / num_units


def test_verdict_good_at_low_loss_ratio():
    # claim_prob 0.05 → LR = 0.05 × 6 = 0.30 → good
    result = calculate_warranty_risk_analysis(
        raw_trials=_trials(0.05),
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
    # claim_prob 0.10 → LR = 0.10 × 6 = 0.60 → moderate
    result = calculate_warranty_risk_analysis(
        raw_trials=_trials(0.10),
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
    # claim_prob 0.20 → LR = 0.20 × 6 = 1.20 → not_eligible
    result = calculate_warranty_risk_analysis(
        raw_trials=_trials(0.20),
        num_units=10,
        standard_price=10_000.0,
    )
    assert result["verdict"] == "not_eligible"
    assert result["verdict_color"] == "danger"
    # No premium displayed for ineligible: should equal default (no adjustment)
    t = result["target_terms"]
    assert t["warranty_premium_displayed"] == t["warranty_premium_default"]


def test_full_funding_discount_when_good():
    result = calculate_warranty_risk_analysis(
        raw_trials=_trials(0.05),
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
    result = calculate_warranty_risk_analysis(
        raw_trials=_trials(0.10),
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
    result = calculate_warranty_risk_analysis(
        raw_trials=_trials(0.05),
        num_units=10,
        standard_price=10_000.0,
        funding_model="base",
    )
    assert result["verdict"] == "good"
    assert result["applied_full_funding_discount"] is False
    t = result["target_terms"]
    assert t["warranty_premium_displayed"] == t["warranty_premium_default"]


def test_verdict_boundary_at_40_pct_is_good():
    # LR exactly at 0.40 should be Good (≤ 0.40 boundary).
    # With LR = claim_prob × 6, we need claim_prob = 1/15 → 2 out of 30 trials.
    raw_trials = {"assessment_yr_1_5": [1.0] * 2 + [0.0] * 28}
    result = calculate_warranty_risk_analysis(
        raw_trials=raw_trials,
        num_units=10,
        standard_price=10_000.0,
    )
    assert abs(result["loss_ratio_default"] - 0.40) < 1e-9
    assert result["verdict"] == "good"


def test_verdict_boundary_at_80_pct_is_moderate():
    # LR exactly at 0.80 → claim_prob = 2/15 → 4 out of 30 trials.
    raw_trials = {"assessment_yr_1_5": [1.0] * 4 + [0.0] * 26}
    result = calculate_warranty_risk_analysis(
        raw_trials=raw_trials,
        num_units=10,
        standard_price=10_000.0,
    )
    assert abs(result["loss_ratio_default"] - 0.80) < 1e-9
    assert result["verdict"] == "moderate"
