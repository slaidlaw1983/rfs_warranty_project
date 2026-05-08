# Design: RFS Reserve Fund Study Stress Test Web App

**Date:** 2026-05-08  
**Status:** Approved

---

## Overview

A standalone Flask web app that wraps the existing Monte Carlo stress test engine (`rfs_calculator.py` + `stress_test.py`). Users upload their reserve study component data as a CSV, enter property-level financial parameters and shock parameters, run the stress test, and view a KPI dashboard showing special assessment distributions and risk indicators across a 30-year forecast horizon.

---

## Background — Existing Engine

`rfs_calculator.py` and `stress_test.py` are already complete and tested. Key entry point:

```python
stress_test.run_stress_test(
    components,            # list of component dicts
    num_trials,
    starting_reserve,
    annual_contribution,
    num_units,
    horizon,
    seed,
    # shock params passed via module-level constants (refactor needed — see below)
)
```

The shock parameters (`LIFE_SHOCK_MEAN`, `LIFE_SHOCK_SIGMA`, `COST_SHOCK_MEAN`, `COST_SHOCK_SIGMA`) are currently module-level constants. The web app needs to pass them per-request, so `run_stress_test()` must accept them as arguments.

---

## CSV Format

Users export the `2-Component` sheet from their reserve study Excel workbook as CSV. Required columns (case-insensitive, whitespace-trimmed):

| Column | Maps to |
|---|---|
| `Component` | component name |
| `Replacement Budget` | "Yes" / "No" — whether replacement is funded |
| `Design Life` | replacement design life (years) |
| `Current Age` | current age (years) — used with Design Life to determine schedule |
| `Replacement Year` | years until next replacement event |
| `Current Replacement Cost` | replacement cost ($) |
| `Maintenance Budget` | "Yes" / "No" — whether maintenance is funded |
| `Maintenance Life Cycle` | maintenance cycle (years) |
| `Maintenance Year` | years until next maintenance event |
| `Current Maintenance Cost` | maintenance cost ($) |

Rows where both `Replacement Budget` and `Maintenance Budget` are "No" (or blank) are dropped — same logic as the existing `filter_components()`. Rows with non-numeric design life / cost values are skipped silently.

Mapped component dict format (matches `reserve_study_components.json`):
```json
{
  "Property Components": "Roof - Asphalt Shingle",
  "Replacement Curr": 1,
  "Replacement Desi": 25,
  "Replacement Year": 25,
  "Replacement Cost": 16500,
  "Maintenance Curr": 0,
  "Maintenance Desi": 0,
  "Maintenance Year": 0,
  "Maintenance Cost": 0
}
```

---

## Input Form (`index.html`)

Three sections:

### Reserve Study Upload
- CSV file input (`name="reserve_csv"`, `accept=".csv"`, required)

### Property Parameters
| Field | Name | Type | Required |
|---|---|---|---|
| Number of units | `num_units` | integer | Yes |
| Opening reserve balance ($) | `starting_reserve` | decimal | Yes |
| Annual contribution ($) | `annual_contribution` | decimal | Yes |

### Shock Parameters (with defaults)
| Field | Name | Default | Notes |
|---|---|---|---|
| Life shock mean | `life_shock_mean` | 0.80 | Multiplier — 0.80 = 20% life decrease |
| Life shock std dev | `life_shock_sigma` | 0.10 | Spread of life multiplier |
| Cost shock mean | `cost_shock_mean` | 1.30 | Multiplier — 1.30 = 30% cost increase |
| Cost shock std dev | `cost_shock_sigma` | 0.15 | Spread of cost multiplier |
| Number of trials | `num_trials` | 2000 | Monte Carlo iterations |
| Forecast horizon (years) | `horizon` | 30 | |

---

## stress_test.py Refactor

Three changes to `stress_test.py`:

1. Add shock params as kwargs to `sample_life_multipliers()` and `sample_cost_multipliers()` (currently they read module constants directly):

```python
def sample_life_multipliers(num_trials, num_components, rng,
                             life_shock_mean=LIFE_SHOCK_MEAN,
                             life_shock_sigma=LIFE_SHOCK_SIGMA):
    samples = rng.normal(life_shock_mean, life_shock_sigma,
                         size=(num_trials, num_components))
    return np.maximum(MIN_LIFE_MULTIPLIER, samples)

def sample_cost_multipliers(num_trials, num_components, rng,
                             cost_shock_mean=COST_SHOCK_MEAN,
                             cost_shock_sigma=COST_SHOCK_SIGMA):
    log_p = calc.calculate_lognormal_params(cost_shock_mean, cost_shock_sigma)
    normal = rng.normal(log_p["mu"], log_p["sigma"],
                        size=(num_trials, num_components))
    return np.maximum(MIN_COST_MULTIPLIER, np.exp(normal))
```

2. Add shock params as kwargs to `run_stress_test()` and thread them through:

```python
def run_stress_test(components, num_trials=NUM_TRIALS,
                    starting_reserve=STARTING_RESERVE,
                    annual_contribution=ANNUAL_CONTRIBUTION,
                    num_units=NUM_UNITS, horizon=FORECAST_HORIZON_YEARS,
                    seed=42,
                    life_shock_mean=LIFE_SHOCK_MEAN,
                    life_shock_sigma=LIFE_SHOCK_SIGMA,
                    cost_shock_mean=COST_SHOCK_MEAN,
                    cost_shock_sigma=COST_SHOCK_SIGMA):
    ...
    life_mults_all = sample_life_multipliers(num_trials, 2*n, rng,
                                             life_shock_mean, life_shock_sigma)
    cost_mults_all = sample_cost_multipliers(num_trials, 2*n, rng,
                                             cost_shock_mean, cost_shock_sigma)
```

3. Update the `config` block in the returned summary to use the passed values (not the module constants).

---

## Results Dashboard (`results.html`)

### 1. Configuration Summary
One-line summary: units, opening reserve, contribution, shock means, trials.

### 2. Special Assessment KPIs
Two tables (Total $ and Per Unit $):

| KPI | Mean | P25 | P50 | P75 | P90 | P95 |
|---|---|---|---|---|---|---|
| Assessment — Years 1–5 | | | | | | |
| Assessment — Years 6–10 | | | | | | |
| Assessment — Years 1–10 | | | | | | |
| Assessment — 30yr Total | | | | | | |

### 3. Risk Indicators
Static reference values plus probability of any assessment:

| Indicator | Value |
|---|---|
| Starting reserve (total) | |
| Starting reserve per unit | |
| Annual contribution (total) | |
| Annual contribution per unit | |
| Probability of assessment — yrs 1–5 | |
| Probability of assessment — yrs 1–10 | |
| Probability of assessment — 30yr | |
| Components loaded | N |

### 4. Download
"Download CSV" button exports the full KPI table to CSV.

---

## Processing Flow

```
POST /run
  ├── Parse CSV → component list (csv_parser.parse_reserve_csv)
  ├── Validate required parameters
  ├── Call stress_test.run_stress_test(...) with user params
  ├── Store summary in Flask session
  └── Render results.html

GET /download-csv
  └── Return stress test KPI summary as CSV attachment
```

Loading overlay shown while the stress test runs (5–15 seconds for 2,000 trials).

---

## File Structure

```
RFS_Warranty_Project/
  app.py                  ← new Flask app
  csv_parser.py           ← new: parses 2-Component CSV
  rfs_calculator.py       ← existing (unchanged)
  stress_test.py          ← existing (minor refactor: shock params as args)
  templates/
    index.html            ← new: upload + parameter form
    results.html          ← new: KPI dashboard
  requirements.txt        ← new
  .gitignore              ← new
  docs/superpowers/specs/ ← this document
```

---

## Error Handling

| Scenario | Behaviour |
|---|---|
| CSV missing required columns | Flash error message, return to form |
| CSV has no valid components after filtering | Flash error, return to form |
| Non-numeric values in numeric columns | Skip row silently, continue |
| Stress test runtime error | Render error page with message |

---

## Out of Scope

- User authentication
- Saving/storing results
- Charts or visualisations
- Multi-property comparison
- Inflation or interest rate adjustments to the projection
