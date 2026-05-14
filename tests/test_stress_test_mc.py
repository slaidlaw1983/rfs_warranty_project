import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import stress_test as st


# Minimal component fixture: one component, replacement event year 1, cost 100k, life 10
SINGLE_COMP = [{
    "Property Components": "Roof",
    "Replacement Curr":    0.0,
    "Replacement Desi":    10.0,
    "Replacement Year":    1.0,
    "Replacement Cost":    100_000.0,
    "Maintenance Curr":    0.0,
    "Maintenance Desi":    0.0,
    "Maintenance Year":    0.0,
    "Maintenance Cost":    0.0,
}]


def _unit_mults(n_comp: int) -> np.ndarray:
    # Two tracks per component (replacement + maintenance)
    return np.ones(2 * n_comp)


def test_run_trial_returns_dual_balance_arrays():
    result = st.run_trial(
        components=SINGLE_COMP,
        life_mults=_unit_mults(1),
        cost_mults=_unit_mults(1),
        starting_reserve=50_000.0,
        base_contribution=10_000.0,
        full_funding_contribution=30_000.0,
        inflation_rate=0.0,
        interest_rate=0.0,
        horizon=5,
    )
    assert "yearly_outflow" in result
    assert "balance_base" in result
    assert "balance_full" in result
    assert "assess_base" in result
    assert "assess_full" in result
    assert len(result["balance_base"]) == 5
    assert len(result["balance_full"]) == 5


def test_run_trial_base_underfunds_full_funds():
    # year 1: outflow 100k, opening 50k. Base contributes 10k → SA of 40k.
    # Full contributes 60k → balance 10k, no SA. (no interest, no inflation)
    result = st.run_trial(
        components=SINGLE_COMP,
        life_mults=_unit_mults(1),
        cost_mults=_unit_mults(1),
        starting_reserve=50_000.0,
        base_contribution=10_000.0,
        full_funding_contribution=60_000.0,
        inflation_rate=0.0,
        interest_rate=0.0,
        horizon=5,
    )
    assert result["assess_base"][0] == 40_000.0
    assert result["assess_full"][0] == 0.0
    assert result["balance_base"][0] == 0.0
    assert result["balance_full"][0] == 10_000.0


def test_run_trial_inflation_applied_to_outflow():
    # Outflow event in year 1 at index 0 inflated by (1.10)^0 = 1.0 (no inflation
    # at year 1). Recurrence at year 11 (life=10) should be inflated by (1.10)^10.
    result = st.run_trial(
        components=SINGLE_COMP,
        life_mults=_unit_mults(1),
        cost_mults=_unit_mults(1),
        starting_reserve=0.0,
        base_contribution=0.0,
        full_funding_contribution=0.0,
        inflation_rate=0.10,
        interest_rate=0.0,
        horizon=15,
    )
    # Year 1 (index 0): 100k at year 0 inflation factor (1.10^0) = 100,000
    assert abs(result["yearly_outflow"][0] - 100_000.0) < 0.01
    # Year 11 (index 10): 100k × (1.10)^10
    expected_yr11 = 100_000.0 * (1.10 ** 10)
    assert abs(result["yearly_outflow"][10] - expected_yr11) < 1.0


def test_run_trial_interest_compounds_on_balance():
    # No outflows. Opening 100k, base contribution 0, interest 5%.
    # Year 1: balance = (100k + 0) * 1.05 = 105k
    # Year 2: balance = (105k + 0) * 1.05 = 110,250
    empty_comp = [{
        "Property Components": "None",
        "Replacement Curr": 0.0, "Replacement Desi": 0.0,
        "Replacement Year": 0.0, "Replacement Cost": 0.0,
        "Maintenance Curr": 0.0, "Maintenance Desi": 0.0,
        "Maintenance Year": 0.0, "Maintenance Cost": 0.0,
    }]
    result = st.run_trial(
        components=empty_comp,
        life_mults=_unit_mults(1),
        cost_mults=_unit_mults(1),
        starting_reserve=100_000.0,
        base_contribution=0.0,
        full_funding_contribution=0.0,
        inflation_rate=0.0,
        interest_rate=0.05,
        horizon=2,
    )
    assert abs(result["balance_base"][0] - 105_000.0) < 0.01
    assert abs(result["balance_base"][1] - 110_250.0) < 0.01
