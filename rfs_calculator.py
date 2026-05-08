"""
RFS Per-Component Monte Carlo Calculator
=========================================

Loads a property reserve-study component table (JSON) and runs Monte Carlo
simulations on each component, separately for the Replacement and Maintenance
tracks. Models design life with a Normal distribution and cost with a
Lognormal distribution. Outputs P25/P50/P75 percentiles plus simulated mean
to a detailed JSON and a flattened CSV summary.

This is the "uncertainty quantification" layer. The stress-test module
(stress_test.py) imports the sampling helpers below and adds shock
parameters + a multi-year forecast on top.

Usage:
    python3 rfs_calculator.py
"""

import csv
import json
import os
from typing import Any, Dict, List, Optional

import numpy as np

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
INPUT_JSON = os.path.join(PROJECT_DIR, "reserve_study_components.json")
OUTPUT_JSON = os.path.join(PROJECT_DIR, "rfs_calculator_results.json")
OUTPUT_CSV = os.path.join(PROJECT_DIR, "rfs_calculator_summary.csv")

NUM_SIMULATIONS = 10_000
LIFE_SIGMA_PCT = 0.15   # Normal distribution: std-dev as % of mean
COST_SIGMA_PCT = 0.15   # Lognormal distribution: std-dev (arithmetic) as % of mean
LIFE_FLOOR_YEARS = 1    # Sampled life can't go below this

# Rows to drop entirely (engineer's placeholder rows with no scheduled events
# and no cost). Anomaly handling decided up front in this project.
DROP_NAMES = {"Foundation", "Power Distribution"}

# ---------------------------------------------------------------------------
# Distribution helpers
# ---------------------------------------------------------------------------


def calculate_lognormal_params(mean: float, std_dev: float) -> Dict[str, float]:
    """Convert arithmetic mean and std-dev into log-space (mu, sigma).

    These are the parameters scipy.stats.lognorm needs:
        scipy expects ``s = sigma_log`` and ``scale = exp(mu_log)``.
    """
    if mean <= 0 or std_dev < 0:
        return {"mu": float("nan"), "sigma": float("nan")}

    variance = std_dev ** 2
    sigma_log = float(np.sqrt(np.log(1.0 + variance / (mean ** 2))))
    mu_log = float(np.log(mean) - 0.5 * sigma_log ** 2)
    return {"mu": mu_log, "sigma": sigma_log}


def _rng(rng: Optional[np.random.Generator]) -> np.random.Generator:
    return rng if rng is not None else np.random.default_rng()


def sample_life(mean_years: float,
                sigma_pct: float = LIFE_SIGMA_PCT,
                size: int = NUM_SIMULATIONS,
                floor: float = LIFE_FLOOR_YEARS,
                rng: Optional[np.random.Generator] = None) -> np.ndarray:
    """Sample design life from a Normal distribution, floored at ``floor``."""
    if mean_years <= 0:
        return np.zeros(size)
    std_dev = mean_years * sigma_pct
    samples = _rng(rng).normal(loc=mean_years, scale=std_dev, size=size)
    return np.maximum(floor, samples)


def sample_cost(mean_cost: float,
                sigma_pct: float = COST_SIGMA_PCT,
                size: int = NUM_SIMULATIONS,
                rng: Optional[np.random.Generator] = None) -> np.ndarray:
    """Sample cost from a Lognormal distribution centered on ``mean_cost``.

    Implemented as ``exp(Normal(mu_log, sigma_log))`` — algebraically identical
    to ``scipy.stats.lognorm.rvs(s=sigma_log, scale=exp(mu_log))``.
    """
    if mean_cost <= 0:
        return np.zeros(size)
    std_dev = mean_cost * sigma_pct
    log_p = calculate_lognormal_params(mean_cost, std_dev)
    normal_samples = _rng(rng).normal(loc=log_p["mu"], scale=log_p["sigma"], size=size)
    return np.exp(normal_samples)


# ---------------------------------------------------------------------------
# Per-track simulation
# ---------------------------------------------------------------------------


def _percentiles(samples: np.ndarray) -> Dict[str, float]:
    return {
        "mean": float(np.mean(samples)),
        "p25": float(np.percentile(samples, 25)),
        "p50": float(np.percentile(samples, 50)),
        "p75": float(np.percentile(samples, 75)),
    }


def simulate_track(life_mean: float,
                   cost_mean: float,
                   num_simulations: int = NUM_SIMULATIONS,
                   life_sigma_pct: float = LIFE_SIGMA_PCT,
                   cost_sigma_pct: float = COST_SIGMA_PCT) -> Dict[str, Any]:
    """Run MC for one (life, cost) track. Returns input echo + percentiles.

    A track is "active" when it has either a non-zero life OR a non-zero
    cost. If both are zero we return an inactive shell so callers can skip
    cleanly.
    """
    active = life_mean > 0 or cost_mean > 0
    out: Dict[str, Any] = {
        "active": active,
        "input_life_mean": life_mean,
        "input_cost_mean": cost_mean,
    }
    if not active:
        return out

    # Life: only simulate when life_mean is positive. For cost-only rows
    # (e.g. Interior Doors) we leave life out and let the caller decide
    # how to schedule (typically as a year-1 lump).
    if life_mean > 0:
        life_samples = sample_life(life_mean, life_sigma_pct, num_simulations)
        out["life"] = _percentiles(life_samples)
    else:
        out["life"] = None

    if cost_mean > 0:
        cost_samples = sample_cost(cost_mean, cost_sigma_pct, num_simulations)
        out["cost"] = _percentiles(cost_samples)
    else:
        out["cost"] = None

    return out


def simulate_component(row: Dict[str, Any],
                       num_simulations: int = NUM_SIMULATIONS,
                       life_sigma_pct: float = LIFE_SIGMA_PCT,
                       cost_sigma_pct: float = COST_SIGMA_PCT) -> Dict[str, Any]:
    """Run MC for one property-table row across both tracks."""
    name = row["Property Components"]
    replacement = simulate_track(
        life_mean=row["Replacement Desi"],
        cost_mean=row["Replacement Cost"],
        num_simulations=num_simulations,
        life_sigma_pct=life_sigma_pct,
        cost_sigma_pct=cost_sigma_pct,
    )
    maintenance = simulate_track(
        life_mean=row["Maintenance Desi"],
        cost_mean=row["Maintenance Cost"],
        num_simulations=num_simulations,
        life_sigma_pct=life_sigma_pct,
        cost_sigma_pct=cost_sigma_pct,
    )
    return {
        "name": name,
        "current_age_replacement": row["Replacement Curr"],
        "remaining_years_replacement": row["Replacement Year"],
        "current_age_maintenance": row["Maintenance Curr"],
        "remaining_years_maintenance": row["Maintenance Year"],
        "replacement": replacement,
        "maintenance": maintenance,
    }


# ---------------------------------------------------------------------------
# Batch driver
# ---------------------------------------------------------------------------


def load_components(path: str = INPUT_JSON) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def filter_components(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drop placeholder rows with neither replacement nor maintenance activity."""
    keep = []
    for r in rows:
        name = r["Property Components"]
        if name in DROP_NAMES:
            continue
        any_activity = (
            r["Replacement Desi"] > 0 or r["Replacement Cost"] > 0
            or r["Maintenance Desi"] > 0 or r["Maintenance Cost"] > 0
        )
        if not any_activity:
            continue
        keep.append(r)
    return keep


def run_calculator(rows: List[Dict[str, Any]],
                   num_simulations: int = NUM_SIMULATIONS,
                   life_sigma_pct: float = LIFE_SIGMA_PCT,
                   cost_sigma_pct: float = COST_SIGMA_PCT) -> List[Dict[str, Any]]:
    return [
        simulate_component(r, num_simulations, life_sigma_pct, cost_sigma_pct)
        for r in rows
    ]


def flatten_for_csv(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    flat = []
    for r in results:
        for track_name in ("replacement", "maintenance"):
            t = r[track_name]
            if not t["active"]:
                continue
            row = {
                "component": r["name"],
                "track": track_name,
                "input_life_mean": t["input_life_mean"],
                "input_cost_mean": t["input_cost_mean"],
            }
            for fld, key in (("life", "life"), ("cost", "cost")):
                d = t.get(key)
                if d is None:
                    row.update({f"{fld}_mean": "", f"{fld}_p25": "", f"{fld}_p50": "", f"{fld}_p75": ""})
                else:
                    row[f"{fld}_mean"] = round(d["mean"], 2)
                    row[f"{fld}_p25"] = round(d["p25"], 2)
                    row[f"{fld}_p50"] = round(d["p50"], 2)
                    row[f"{fld}_p75"] = round(d["p75"], 2)
            flat.append(row)
    return flat


def write_outputs(results: List[Dict[str, Any]],
                  json_path: str = OUTPUT_JSON,
                  csv_path: str = OUTPUT_CSV) -> None:
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    flat = flatten_for_csv(results)
    if not flat:
        return
    fieldnames = list(flat[0].keys())
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in flat:
            writer.writerow(row)


def main() -> None:
    rows = load_components()
    print(f"Loaded {len(rows)} components from {os.path.basename(INPUT_JSON)}")
    kept = filter_components(rows)
    dropped = len(rows) - len(kept)
    print(f"Dropped {dropped} placeholder rows; simulating {len(kept)} components")

    results = run_calculator(kept)
    write_outputs(results)

    print(f"Wrote {OUTPUT_JSON}")
    print(f"Wrote {OUTPUT_CSV}")

    # Quick console summary: top 5 components by replacement cost p50
    summary = []
    for r in results:
        rep = r["replacement"]
        if rep["active"] and rep["cost"]:
            summary.append((r["name"], rep["cost"]["p50"]))
    summary.sort(key=lambda x: x[1], reverse=True)
    print("\nTop 5 components by simulated median replacement cost:")
    for name, cost in summary[:5]:
        print(f"  {name:35} ${cost:>12,.0f}")


if __name__ == "__main__":
    main()
