"""
RFS Reserve Study Stress Test
==============================

Runs a Monte Carlo stress test on a property reserve study. Each trial:

  1. Samples a shocked design life multiplier per component (Normal, mean<1
     => life decreases under stress) for both replacement and maintenance.
  2. Samples a shocked cost multiplier per component (Lognormal, mean>1
     => costs increase under stress) for both tracks.
  3. Builds a year-by-year cash-flow forecast over the horizon, holding the
     annual contribution STATIC (per project brief). Cost-only rows
     (e.g. "Interior Doors" — cost set, no schedule) are treated as a
     year-1 deferred-maintenance lump.
  4. Runs the reserve balance forward; whenever it would go negative, the
     deficit is captured as a "special assessment" that year.
  5. Sums special assessments across the year 1-5 and year 6-10 windows
     and reports per-unit values.

Aggregating across trials produces P25/P50/P75/P90/P95 distributions for
each KPI, plus a few static reference numbers (starting reserve total /
per-unit, contribution total / per-unit).

Usage:
    python3 stress_test.py
"""

import csv
import json
import os
from typing import Any, Dict, List

import numpy as np

import rfs_calculator as calc

# ---------------------------------------------------------------------------
# Property-level parameters (placeholders — swap for real values when known)
# ---------------------------------------------------------------------------

NUM_UNITS = 100
STARTING_RESERVE = 500_000
ANNUAL_CONTRIBUTION = 300_000      # held STATIC across all trials/years
FORECAST_HORIZON_YEARS = 30

# Monte Carlo trial count for the stress test. Each trial is one full
# 30-year forecast, so this is heavier than the per-component calculator.
NUM_TRIALS = 2_000

# ---------------------------------------------------------------------------
# Shock distribution parameters
# ---------------------------------------------------------------------------
# Life shock = MULTIPLIER applied to Replacement/Maintenance Desi.
# Mean < 1 means life decreases under stress.
LIFE_SHOCK_MEAN = 0.80      # central case: 20% life decrease
LIFE_SHOCK_SIGMA = 0.10     # std-dev of the multiplier (Normal)

# Cost shock = MULTIPLIER applied to Replacement/Maintenance Cost.
# Mean > 1 means costs increase under stress. Sampled from a Lognormal so
# the upside (cost overrun) tail is heavier than the downside.
COST_SHOCK_MEAN = 1.30      # central case: 30% cost increase
COST_SHOCK_SIGMA = 0.15     # arithmetic std-dev of the multiplier

# Floors to keep samples physically reasonable.
MIN_LIFE_MULTIPLIER = 0.20  # cap life decrease at 80% reduction
MIN_COST_MULTIPLIER = 0.50  # cap cost decrease at 50% reduction

# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_JSON = os.path.join(PROJECT_DIR, "stress_test_results.json")
OUTPUT_CSV = os.path.join(PROJECT_DIR, "stress_test_kpi_summary.csv")


# ---------------------------------------------------------------------------
# Shock sampling
# ---------------------------------------------------------------------------


def sample_life_multipliers(num_trials: int,
                            num_components: int,
                            rng: np.random.Generator,
                            life_shock_mean: float = LIFE_SHOCK_MEAN,
                            life_shock_sigma: float = LIFE_SHOCK_SIGMA) -> np.ndarray:
    """Per-trial, per-component life multiplier from Normal(mean, sigma)."""
    samples = rng.normal(life_shock_mean, life_shock_sigma,
                         size=(num_trials, num_components))
    return np.maximum(MIN_LIFE_MULTIPLIER, samples)


def sample_cost_multipliers(num_trials: int,
                            num_components: int,
                            rng: np.random.Generator,
                            cost_shock_mean: float = COST_SHOCK_MEAN,
                            cost_shock_sigma: float = COST_SHOCK_SIGMA) -> np.ndarray:
    """Per-trial, per-component cost multiplier from Lognormal(mean, sigma)."""
    log_p = calc.calculate_lognormal_params(cost_shock_mean, cost_shock_sigma)
    normal = rng.normal(log_p["mu"], log_p["sigma"],
                        size=(num_trials, num_components))
    samples = np.exp(normal)
    return np.maximum(MIN_COST_MULTIPLIER, samples)


# ---------------------------------------------------------------------------
# Schedule building
# ---------------------------------------------------------------------------


def schedule_track(original_life: float,
                   original_year: float,
                   original_cost: float,
                   shocked_life: float,
                   shocked_cost: float,
                   horizon: int) -> List[float]:
    """Year-indexed outflow array (length=horizon) for one track of one component.

    Three cases:
      * inactive track (life=0 and cost=0): returns zeros.
      * cost-only (life=0, cost>0): one lump in year 1.
      * scheduled (life>0, cost>0): events on a shocked cycle.

    Event timing under shock: keep the engineer's "offset" (original_year -
    original_life) so that year-1 events still happen in year 1, and shift
    proportionally with life. Concretely: shocked_year = max(1, original_year
    - (original_life - shocked_life)). Subsequent events recur every
    shocked_life years.
    """
    outflows = [0.0] * horizon

    if original_life <= 0 and original_cost <= 0:
        return outflows

    if original_life <= 0 and original_cost > 0:
        # Cost-only row: lump in year 1 at shocked cost
        outflows[0] = shocked_cost
        return outflows

    if original_cost <= 0:
        return outflows  # life set but no cost — nothing to spend

    # Scheduled cycle. shocked_life floored at 1 already (calculator floor),
    # but enforce here for safety.
    shocked_life = max(1.0, shocked_life)
    delta = original_life - shocked_life          # how much life shrank
    shocked_year = max(1.0, original_year - delta)

    # Generate event years on the shocked cycle until we exit the horizon
    t = shocked_year
    while t <= horizon:
        idx = int(round(t)) - 1   # year 1 → index 0
        if 0 <= idx < horizon:
            outflows[idx] += shocked_cost
        t += shocked_life

    return outflows


# ---------------------------------------------------------------------------
# Single-trial forecast
# ---------------------------------------------------------------------------


def run_trial(components: List[Dict[str, Any]],
              life_mults: np.ndarray,
              cost_mults: np.ndarray,
              starting_reserve: float,
              annual_contribution: float,
              horizon: int) -> Dict[str, Any]:
    """Run one stress-test trial and return KPIs.

    ``life_mults`` and ``cost_mults`` are 1-D arrays of length
    2*len(components) — first half is replacement track, second half is
    maintenance track, in the same order as ``components``.
    """
    n = len(components)
    yearly_outflow = np.zeros(horizon)

    for i, comp in enumerate(components):
        # Replacement track
        rep_life = comp["Replacement Desi"]
        rep_year = comp["Replacement Year"]
        rep_cost = comp["Replacement Cost"]
        if rep_life > 0 or rep_cost > 0:
            shocked_life = rep_life * life_mults[i]
            shocked_cost = rep_cost * cost_mults[i]
            of = schedule_track(rep_life, rep_year, rep_cost,
                                shocked_life, shocked_cost, horizon)
            yearly_outflow += np.array(of)

        # Maintenance track
        m_life = comp["Maintenance Desi"]
        m_year = comp["Maintenance Year"]
        m_cost = comp["Maintenance Cost"]
        if m_life > 0 or m_cost > 0:
            shocked_life = m_life * life_mults[n + i]
            shocked_cost = m_cost * cost_mults[n + i]
            of = schedule_track(m_life, m_year, m_cost,
                                shocked_life, shocked_cost, horizon)
            yearly_outflow += np.array(of)

    # Run the reserve balance forward; capture deficits as special assessments
    balance = starting_reserve
    assessments = np.zeros(horizon)
    balance_path = np.zeros(horizon)
    for y in range(horizon):
        balance += annual_contribution - yearly_outflow[y]
        if balance < 0:
            assessments[y] = -balance   # bring balance back to zero
            balance = 0.0
        balance_path[y] = balance

    return {
        "yearly_outflow": yearly_outflow,
        "yearly_assessment": assessments,
        "balance_path": balance_path,
        "total_outflow_30yr": float(yearly_outflow.sum()),
        "assessment_yr_1_5": float(assessments[0:5].sum()),
        "assessment_yr_6_10": float(assessments[5:10].sum()),
        "assessment_yr_1_10": float(assessments[0:10].sum()),
        "assessment_total": float(assessments.sum()),
    }


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def percentile_summary(values: np.ndarray) -> Dict[str, float]:
    return {
        "mean": float(np.mean(values)),
        "p25": float(np.percentile(values, 25)),
        "p50": float(np.percentile(values, 50)),
        "p75": float(np.percentile(values, 75)),
        "p90": float(np.percentile(values, 90)),
        "p95": float(np.percentile(values, 95)),
    }


def run_stress_test(components: List[Dict[str, Any]],
                    num_trials: int = NUM_TRIALS,
                    starting_reserve: float = STARTING_RESERVE,
                    annual_contribution: float = ANNUAL_CONTRIBUTION,
                    num_units: int = NUM_UNITS,
                    horizon: int = FORECAST_HORIZON_YEARS,
                    seed: int = 42,
                    life_shock_mean: float = LIFE_SHOCK_MEAN,
                    life_shock_sigma: float = LIFE_SHOCK_SIGMA,
                    cost_shock_mean: float = COST_SHOCK_MEAN,
                    cost_shock_sigma: float = COST_SHOCK_SIGMA) -> Dict[str, Any]:
    rng = np.random.default_rng(seed)
    n = len(components)

    # Two tracks per component → 2n columns of multipliers
    life_mults_all = sample_life_multipliers(num_trials, 2 * n, rng,
                                             life_shock_mean, life_shock_sigma)
    cost_mults_all = sample_cost_multipliers(num_trials, 2 * n, rng,
                                             cost_shock_mean, cost_shock_sigma)

    a_1_5 = np.zeros(num_trials)
    a_6_10 = np.zeros(num_trials)
    a_1_10 = np.zeros(num_trials)
    a_total = np.zeros(num_trials)
    total_outflow = np.zeros(num_trials)
    min_bal_1_5  = np.zeros(num_trials)
    min_bal_6_10 = np.zeros(num_trials)
    min_bal_30yr = np.zeros(num_trials)
    bal_at_yr10  = np.zeros(num_trials)
    bal_at_yr30  = np.zeros(num_trials)
    pct_outflow_1_5  = np.zeros(num_trials)
    pct_outflow_6_10 = np.zeros(num_trials)
    n_assessment_yrs  = np.zeros(num_trials)
    max_single_assessment = np.zeros(num_trials)

    for t in range(num_trials):
        result = run_trial(
            components,
            life_mults=life_mults_all[t],
            cost_mults=cost_mults_all[t],
            starting_reserve=starting_reserve,
            annual_contribution=annual_contribution,
            horizon=horizon,
        )
        bp = result["balance_path"]
        of = result["yearly_outflow"]
        assessments = result["yearly_assessment"]
        total_of = float(of.sum())

        a_1_5[t] = result["assessment_yr_1_5"]
        a_6_10[t] = result["assessment_yr_6_10"]
        a_1_10[t] = result["assessment_yr_1_10"]
        a_total[t] = result["assessment_total"]
        total_outflow[t] = total_of

        min_bal_1_5[t]  = float(bp[0:5].min())
        min_bal_6_10[t] = float(bp[5:min(10, horizon)].min()) if horizon > 5 else float(bp.min())
        min_bal_30yr[t] = float(bp.min())
        bal_at_yr10[t]  = float(bp[min(9, horizon - 1)])
        bal_at_yr30[t]  = float(bp[-1])
        pct_outflow_1_5[t]  = float(of[0:5].sum() / total_of) if total_of > 0 else 0.0
        pct_outflow_6_10[t] = float(of[5:min(10, horizon)].sum() / total_of) if total_of > 0 else 0.0
        n_assessment_yrs[t]  = float((assessments > 0).sum())
        max_single_assessment[t] = float(assessments.max())

    # Per-unit views
    def per_unit(arr: np.ndarray) -> np.ndarray:
        return arr / max(1, num_units)

    summary = {
        "config": {
            "num_units": num_units,
            "starting_reserve_total": starting_reserve,
            "starting_reserve_per_unit": starting_reserve / max(1, num_units),
            "annual_contribution_total": annual_contribution,
            "annual_contribution_per_unit": annual_contribution / max(1, num_units),
            "forecast_horizon_years": horizon,
            "num_trials": num_trials,
            "life_shock_mean": life_shock_mean,
            "life_shock_sigma": life_shock_sigma,
            "cost_shock_mean": cost_shock_mean,
            "cost_shock_sigma": cost_shock_sigma,
        },
        "kpis_total": {
            "assessment_yr_1_5":    percentile_summary(a_1_5),
            "assessment_yr_6_10":   percentile_summary(a_6_10),
            "assessment_yr_1_10":   percentile_summary(a_1_10),
            "assessment_30yr_total": percentile_summary(a_total),
            "outflow_30yr_total":   percentile_summary(total_outflow),
        },
        "kpis_per_unit": {
            "assessment_yr_1_5":    percentile_summary(per_unit(a_1_5)),
            "assessment_yr_6_10":   percentile_summary(per_unit(a_6_10)),
            "assessment_yr_1_10":   percentile_summary(per_unit(a_1_10)),
            "assessment_30yr_total": percentile_summary(per_unit(a_total)),
        },
        "kpis_balance_total": {
            "min_balance_yr_1_5":  percentile_summary(min_bal_1_5),
            "min_balance_yr_6_10": percentile_summary(min_bal_6_10),
            "min_balance_30yr":    percentile_summary(min_bal_30yr),
            "balance_at_yr10":     percentile_summary(bal_at_yr10),
            "balance_at_yr30":     percentile_summary(bal_at_yr30),
        },
        "kpis_balance_per_unit": {
            "min_balance_yr_1_5":  percentile_summary(per_unit(min_bal_1_5)),
            "min_balance_yr_6_10": percentile_summary(per_unit(min_bal_6_10)),
            "min_balance_30yr":    percentile_summary(per_unit(min_bal_30yr)),
            "balance_at_yr10":     percentile_summary(per_unit(bal_at_yr10)),
            "balance_at_yr30":     percentile_summary(per_unit(bal_at_yr30)),
        },
        "expenditure_distribution": {
            "pct_outflow_yr_1_5":  float(np.median(pct_outflow_1_5)),
            "pct_outflow_yr_6_10": float(np.median(pct_outflow_6_10)),
        },
        "assessment_frequency": {
            "n_assessment_years":    percentile_summary(n_assessment_yrs),
            "max_single_assessment": percentile_summary(max_single_assessment),
        },
        "probability_of_assessment": {
            "yr_1_5": float(np.mean(a_1_5 > 0)),
            "yr_6_10": float(np.mean(a_6_10 > 0)),
            "yr_1_10": float(np.mean(a_1_10 > 0)),
            "30yr": float(np.mean(a_total > 0)),
        },
        "contribution_adequacy_ratio": float(
            annual_contribution / (float(np.median(total_outflow)) / horizon)
            if np.median(total_outflow) > 0 else float("inf")
        ),
    }
    return summary


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def write_outputs(summary: Dict[str, Any]) -> None:
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    # Flatten KPIs into a CSV for at-a-glance reading
    rows = []
    for view in ("kpis_total", "kpis_per_unit"):
        for kpi, stats in summary[view].items():
            rows.append({
                "view": view,
                "kpi": kpi,
                **{k: round(v, 2) for k, v in stats.items()},
            })

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["view", "kpi", "mean", "p25", "p50", "p75", "p90", "p95"],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def print_summary(summary: Dict[str, Any]) -> None:
    cfg = summary["config"]
    print("\n" + "=" * 70)
    print("RFS Stress Test Summary")
    print("=" * 70)
    print(f"  Property:  {cfg['num_units']} units, "
          f"${cfg['starting_reserve_total']:,.0f} starting reserve "
          f"(${cfg['starting_reserve_per_unit']:,.0f}/unit)")
    print(f"  Funding:   ${cfg['annual_contribution_total']:,.0f}/yr held static "
          f"(${cfg['annual_contribution_per_unit']:,.0f}/unit/yr)")
    print(f"  Horizon:   {cfg['forecast_horizon_years']} yrs   |   Trials: {cfg['num_trials']:,}")
    print(f"  Shocks:    life ×N({cfg['life_shock_mean']:.2f}, {cfg['life_shock_sigma']:.2f}); "
          f"cost ×Lognorm(mean={cfg['cost_shock_mean']:.2f}, "
          f"sigma={cfg['cost_shock_sigma']:.2f})")
    print()
    print(f"{'KPI':30}  {'mean':>12}  {'P25':>12}  {'P50':>12}  "
          f"{'P75':>12}  {'P90':>12}  {'P95':>12}")
    print("-" * 110)
    for view_name, view in (("TOTAL", summary["kpis_total"]),
                            ("PER UNIT", summary["kpis_per_unit"])):
        print(f"-- {view_name} --")
        for kpi, stats in view.items():
            row = (f"{kpi:30}  "
                   f"${stats['mean']:>10,.0f}  ${stats['p25']:>10,.0f}  "
                   f"${stats['p50']:>10,.0f}  ${stats['p75']:>10,.0f}  "
                   f"${stats['p90']:>10,.0f}  ${stats['p95']:>10,.0f}")
            print(row)
    print()
    print("Probability of any special assessment:")
    for window, prob in summary["probability_of_assessment"].items():
        print(f"  {window:10}: {prob:.1%}")


def main() -> None:
    rows = calc.load_components()
    components = calc.filter_components(rows)
    print(f"Loaded {len(rows)} components, simulating {len(components)} after filter")

    summary = run_stress_test(components)
    write_outputs(summary)
    print_summary(summary)

    print(f"\nWrote {OUTPUT_JSON}")
    print(f"Wrote {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
