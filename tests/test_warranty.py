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


def test_warranty_basic_shape():
    result = calculate_warranty_risk_analysis(
        raw_trials=_trials(0.2),
        num_units=10,
        standard_price=10_000.0,
    )
    assert result is not None
    assert result["target_terms"]["coverage_per_unit"] == 500.0
    assert result["target_terms"]["coverage_total"] == 5_000.0
    assert "warranty_premium" in result["target_terms"]
    assert "claim_probability" in result["stress_results"]
    assert abs(result["stress_results"]["claim_probability"] - 0.2) < 1e-9
    assert "actual_loss_ratio" in result
    assert "verdict" in result
