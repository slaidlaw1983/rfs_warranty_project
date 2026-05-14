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


def _budget_starved_component():
    # Forces SA in early years: huge year-1 cost relative to reserves/contribution
    return [{
        "Property Components": "Big Item",
        "Replacement Curr": 0.0, "Replacement Desi": 5.0,
        "Replacement Year": 1.0, "Replacement Cost": 500_000.0,
        "Maintenance Curr": 0.0, "Maintenance Desi": 0.0,
        "Maintenance Year": 0.0, "Maintenance Cost": 0.0,
    }]


def test_run_stress_test_summary_shape():
    summary = st.run_stress_test(
        components=_budget_starved_component(),
        num_trials=100,
        starting_reserve=50_000.0,
        base_contribution=10_000.0,
        full_funding_contribution=200_000.0,
        inflation_rate=0.03,
        interest_rate=0.025,
        num_units=10,
        horizon=30,
    )

    # config
    cfg = summary["config"]
    assert cfg["num_units"] == 10
    assert cfg["base_contribution"] == 10_000.0
    assert cfg["full_funding_contribution"] == 200_000.0
    assert cfg["num_trials"] == 100
    assert cfg["horizon"] == 30

    # raw trials per model
    for key in ("raw_trials_base", "raw_trials_full"):
        rt = summary[key]
        assert len(rt["assessment_yr_1_5"]) == 100
        assert len(rt["assessment_yr_1_10"]) == 100
        assert len(rt["assessment_yr_total_30"]) == 100

    # per-model derived
    for key in ("base", "full"):
        m = summary[key]
        assert set(m["prob_assessment"]) == {"yr_1_5", "yr_1_10", "yr_30"}
        for v in m["prob_assessment"].values():
            assert 0.0 <= v <= 1.0

        # New P5/P25/P50 distribution blocks
        for block_name in ("sa_total", "sa_per_unit", "n_assessment_years"):
            block = m[block_name]
            assert set(block) == {"p5", "p25", "p50"}, f"{block_name} keys mismatch"
            # Worse outcomes monotonically larger: p50 ≤ p25 ≤ p5
            assert block["p50"] <= block["p25"] <= block["p5"], (
                f"{block_name} not monotone: {block}"
            )

        # Removed in this task
        assert "median_total_assessment" not in m
        assert "median_n_assessment_years" not in m

    # chart arrays
    ch = summary["chart"]
    assert len(ch["years"]) == 30
    assert ch["years"][0] == 1 and ch["years"][-1] == 30
    assert len(ch["p50_outflow"]) == 30
    assert len(ch["p50_balance_base"]) == 30
    assert len(ch["p50_balance_full"]) == 30
    assert len(ch["base_contribution_stream"]) == 30
    assert len(ch["full_contribution_stream"]) == 30

    # removed blocks
    for old_key in ("kpis_total", "kpis_per_unit", "kpis_balance_total",
                    "kpis_balance_per_unit", "expenditure_distribution",
                    "assessment_frequency"):
        assert old_key not in summary, f"{old_key} should be removed"


def test_run_stress_test_full_funding_lower_or_equal_prob():
    # Same trials, higher contribution → P(SA) for Full ≤ P(SA) for Base
    summary = st.run_stress_test(
        components=_budget_starved_component(),
        num_trials=200,
        starting_reserve=50_000.0,
        base_contribution=10_000.0,
        full_funding_contribution=500_000.0,
        inflation_rate=0.0,
        interest_rate=0.0,
        num_units=10,
        horizon=30,
    )
    for window in ("yr_1_5", "yr_1_10", "yr_30"):
        assert (summary["full"]["prob_assessment"][window]
                <= summary["base"]["prob_assessment"][window])


def test_run_stress_test_sa_per_unit_is_total_divided_by_units():
    summary = st.run_stress_test(
        components=_budget_starved_component(),
        num_trials=100,
        starting_reserve=0.0,
        base_contribution=0.0,
        full_funding_contribution=10_000_000.0,
        inflation_rate=0.0,
        interest_rate=0.0,
        num_units=10,
        horizon=30,
    )
    base = summary["base"]
    # sa_per_unit is sa_total divided by num_units, element-wise
    for k in ("p5", "p25", "p50"):
        assert abs(base["sa_per_unit"][k] - base["sa_total"][k] / 10) < 0.01


def test_run_stress_test_contribution_streams_match_inflation():
    summary = st.run_stress_test(
        components=_budget_starved_component(),
        num_trials=10,
        starting_reserve=0.0,
        base_contribution=1000.0,
        full_funding_contribution=2000.0,
        inflation_rate=0.05,
        interest_rate=0.0,
        num_units=1,
        horizon=5,
    )
    base_stream = summary["chart"]["base_contribution_stream"]
    full_stream = summary["chart"]["full_contribution_stream"]
    # Year 1 = nominal amount, year N = nominal × (1.05)^(N-1)
    for y in range(5):
        assert abs(base_stream[y] - 1000.0 * (1.05 ** y)) < 0.01
        assert abs(full_stream[y] - 2000.0 * (1.05 ** y)) < 0.01
