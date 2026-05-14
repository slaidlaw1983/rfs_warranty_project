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

from typing import Any, Dict, List

import numpy as np

import rfs_calculator as calc

# ---------------------------------------------------------------------------
# Property-level parameters
# ---------------------------------------------------------------------------

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
                   horizon: int,
                   inflation_rate: float = 0.0) -> List[float]:
    """Year-indexed outflow array (length=horizon) for one track of one component.

    Three cases:
      * inactive track (life=0 and cost=0): returns zeros.
      * cost-only (life=0, cost>0): one lump in year 1, no inflation applied.
      * scheduled (life>0, cost>0): events on a shocked cycle; each event is
        multiplied by (1 + inflation_rate) ** year_index (year 1 = index 0, no inflation).
    """
    outflows = [0.0] * horizon

    if original_life <= 0 and original_cost <= 0:
        return outflows

    if original_life <= 0 and original_cost > 0:
        outflows[0] = shocked_cost  # year 1 lump, no inflation
        return outflows

    if original_cost <= 0:
        return outflows

    shocked_life = max(1.0, shocked_life)
    delta = original_life - shocked_life
    shocked_year = max(1.0, original_year - delta)

    t = shocked_year
    while t <= horizon:
        idx = int(round(t)) - 1   # year 1 → index 0
        if 0 <= idx < horizon:
            outflows[idx] += shocked_cost * ((1 + inflation_rate) ** idx)
        t += shocked_life

    return outflows


# ---------------------------------------------------------------------------
# Single-trial forecast
# ---------------------------------------------------------------------------


def run_trial(components: List[Dict[str, Any]],
              life_mults: np.ndarray,
              cost_mults: np.ndarray,
              starting_reserve: float,
              base_contribution: float,
              full_funding_contribution: float,
              inflation_rate: float,
              interest_rate: float,
              horizon: int) -> Dict[str, Any]:
    """Run one stress-test trial and return both funding-model paths.

    Shared work per trial:
      - Sample-driven yearly outflow (with cost inflation)

    Per funding model (Base / Full Funding):
      - Contribution grows each year by (1 + inflation_rate)
      - Interest accrues on (opening + contribution) at interest_rate
      - Negative balance becomes a special assessment for that year; balance resets to 0
    """
    n = len(components)
    yearly_outflow = np.zeros(horizon)

    for i, comp in enumerate(components):
        rep_life = comp["Replacement Desi"]
        rep_year = comp["Replacement Year"]
        rep_cost = comp["Replacement Cost"]
        if rep_life > 0 or rep_cost > 0:
            shocked_life = rep_life * life_mults[i]
            shocked_cost = rep_cost * cost_mults[i]
            of = schedule_track(rep_life, rep_year, rep_cost,
                                shocked_life, shocked_cost,
                                horizon, inflation_rate)
            yearly_outflow += np.array(of)

        m_life = comp["Maintenance Desi"]
        m_year = comp["Maintenance Year"]
        m_cost = comp["Maintenance Cost"]
        if m_life > 0 or m_cost > 0:
            shocked_life = m_life * life_mults[n + i]
            shocked_cost = m_cost * cost_mults[n + i]
            of = schedule_track(m_life, m_year, m_cost,
                                shocked_life, shocked_cost,
                                horizon, inflation_rate)
            yearly_outflow += np.array(of)

    def _walk(initial_contribution: float):
        balance = float(starting_reserve)
        contribution = float(initial_contribution)
        balance_path = np.zeros(horizon)
        assess_path = np.zeros(horizon)
        for y in range(horizon):
            opening = balance
            interest = (opening + contribution) * interest_rate
            running = opening + contribution + interest - yearly_outflow[y]
            if running < 0:
                assess_path[y] = -running
                balance = 0.0
            else:
                balance = running
            balance_path[y] = balance
            contribution *= (1 + inflation_rate)
        return balance_path, assess_path

    balance_base, assess_base = _walk(base_contribution)
    balance_full, assess_full = _walk(full_funding_contribution)

    return {
        "yearly_outflow": yearly_outflow,
        "balance_base": balance_base,
        "balance_full": balance_full,
        "assess_base": assess_base,
        "assess_full": assess_full,
    }


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def run_stress_test(components: List[Dict[str, Any]],
                    num_trials: int = 1000,
                    starting_reserve: float = 0.0,
                    base_contribution: float = 0.0,
                    full_funding_contribution: float = 0.0,
                    inflation_rate: float = 0.03,
                    interest_rate: float = 0.025,
                    num_units: int = 1,
                    horizon: int = 30,
                    seed: int = 42,
                    life_shock_mean: float = 1.0,
                    life_shock_sigma: float = 0.30,
                    cost_shock_mean: float = 1.0,
                    cost_shock_sigma: float = 0.30) -> Dict[str, Any]:
    """Run num_trials Monte Carlo trials of the 30-year forecast.

    Each trial: sample cost (lognormal mean=1.0) and life (normal mean=1.0)
    multipliers, build the yearly outflow (with cost inflation), then walk
    the balance forward under BOTH the base and full-funding contribution
    streams (common random numbers).

    Returns a summary dict — see the design spec for the full shape.
    """
    rng = np.random.default_rng(seed)
    n = len(components)

    life_mults_all = sample_life_multipliers(num_trials, 2 * n, rng,
                                             life_shock_mean, life_shock_sigma)
    cost_mults_all = sample_cost_multipliers(num_trials, 2 * n, rng,
                                             cost_shock_mean, cost_shock_sigma)

    # Per-trial × per-year matrices
    outflow_mat       = np.zeros((num_trials, horizon))
    balance_base_mat  = np.zeros((num_trials, horizon))
    balance_full_mat  = np.zeros((num_trials, horizon))
    assess_base_mat   = np.zeros((num_trials, horizon))
    assess_full_mat   = np.zeros((num_trials, horizon))

    for t in range(num_trials):
        result = run_trial(
            components,
            life_mults=life_mults_all[t],
            cost_mults=cost_mults_all[t],
            starting_reserve=starting_reserve,
            base_contribution=base_contribution,
            full_funding_contribution=full_funding_contribution,
            inflation_rate=inflation_rate,
            interest_rate=interest_rate,
            horizon=horizon,
        )
        outflow_mat[t]      = result["yearly_outflow"]
        balance_base_mat[t] = result["balance_base"]
        balance_full_mat[t] = result["balance_full"]
        assess_base_mat[t]  = result["assess_base"]
        assess_full_mat[t]  = result["assess_full"]

    def _model_block(assess_mat: np.ndarray) -> Dict[str, Any]:
        a_1_5  = assess_mat[:, 0:min(5, horizon)].sum(axis=1)
        a_1_10 = assess_mat[:, 0:min(10, horizon)].sum(axis=1)
        a_total = assess_mat.sum(axis=1)
        n_yrs = (assess_mat > 0).sum(axis=1)
        med_total = float(np.median(a_total))
        return {
            "raw_trials": {
                "assessment_yr_1_5":   a_1_5.tolist(),
                "assessment_yr_1_10":  a_1_10.tolist(),
                "assessment_yr_total_30": a_total.tolist(),
            },
            "prob_assessment": {
                "yr_1_5":  float(np.mean(a_1_5 > 0)),
                "yr_1_10": float(np.mean(a_1_10 > 0)),
                "yr_30":   float(np.mean(a_total > 0)),
            },
            "median_total_assessment": {
                "total":    med_total,
                "per_unit": med_total / max(1, num_units),
            },
            "median_n_assessment_years": float(np.median(n_yrs)),
        }

    base_block = _model_block(assess_base_mat)
    full_block = _model_block(assess_full_mat)

    base_stream = [base_contribution * ((1 + inflation_rate) ** y) for y in range(horizon)]
    full_stream = [full_funding_contribution * ((1 + inflation_rate) ** y) for y in range(horizon)]

    return {
        "config": {
            "num_units": num_units,
            "starting_reserve": starting_reserve,
            "base_contribution": base_contribution,
            "full_funding_contribution": full_funding_contribution,
            "inflation_rate": inflation_rate,
            "interest_rate": interest_rate,
            "horizon": horizon,
            "num_trials": num_trials,
        },
        "raw_trials_base": base_block.pop("raw_trials"),
        "raw_trials_full": full_block.pop("raw_trials"),
        "base": base_block,
        "full": full_block,
        "chart": {
            "years": list(range(1, horizon + 1)),
            "p50_outflow":      np.median(outflow_mat, axis=0).tolist(),
            "p50_balance_base": np.median(balance_base_mat, axis=0).tolist(),
            "p50_balance_full": np.median(balance_full_mat, axis=0).tolist(),
            "base_contribution_stream": base_stream,
            "full_contribution_stream": full_stream,
        },
    }
