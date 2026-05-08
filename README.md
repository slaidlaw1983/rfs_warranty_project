# RFS Warranty Project — Reserve Fund Study Stress Test

A web application for stress testing condominium reserve fund studies using Monte Carlo simulation.

## What It Does

Uploads a reserve study component CSV, applies probabilistic shocks to design life and replacement costs, and runs a 30-year financial forecast to determine the likelihood and magnitude of special assessments under adverse conditions. Contributions are held static across all trials.

## How It Works

1. **Upload** a CSV export of your reserve study component table
2. **Enter** property parameters (units, opening reserve balance, annual contribution)
3. **Configure** stress parameters (life shock, cost shock, number of trials)
4. **Run** the Monte Carlo simulation (2,000 trials × 30-year forecast)
5. **Review** the KPI dashboard — special assessments at P25/P50/P75/P90/P95 for years 1–5 and 6–10

## Stress Model

| Parameter | Distribution | Default |
|---|---|---|
| Design life multiplier | Normal | mean 0.80 (20% decrease), σ 0.10 |
| Cost multiplier | Lognormal | mean 1.30 (30% increase), σ 0.15 |

- Life shocks are applied per component, per trial
- Cost shocks are applied per component, per trial
- Reserve balance is run forward year by year; any deficit triggers a special assessment that year

## CSV Format

Export the component sheet from your reserve study workbook as CSV. Required columns:

| Column | Description |
|---|---|
| `Component` | Component name |
| `Replacement Budget` | Yes / No |
| `Design Life` | Replacement design life (years) |
| `Current Age` | Component current age (years) |
| `Replacement Year` | Years until next replacement |
| `Current Replacement Cost` | Replacement cost ($) |
| `Maintenance Budget` | Yes / No |
| `Maintenance Life Cycle` | Maintenance cycle (years) |
| `Maintenance Year` | Years until next maintenance |
| `Current Maintenance Cost` | Maintenance cost ($) |

Components where both Budget flags are "No" are excluded from the simulation.

## Output KPIs

- **Special assessment — years 1–5** (total and per unit): P25 / P50 / P75 / P90 / P95
- **Special assessment — years 6–10** (total and per unit): P25 / P50 / P75 / P90 / P95
- **Special assessment — years 1–10** and **30yr total**
- **Probability of any assessment** across each time window
- Starting reserve and contribution reference values (total and per unit)

## Project Structure

```
rfs_calculator.py          Per-component Monte Carlo (Normal life, Lognormal cost)
stress_test.py             Full stress test — 30-year forecast, special assessment calc
csv_parser.py              CSV → component dict parser (web app layer)
app.py                     Flask web application
templates/
  index.html               Upload + parameter form
  results.html             KPI dashboard
components_ab.json         Alberta component cost catalogue
components_bc.json         British Columbia component cost catalogue
components_on.json         Ontario component cost catalogue
```

## Tech Stack

Python 3, Flask, NumPy, Bootstrap 5

## Status

Web application under development. Engine (`rfs_calculator.py`, `stress_test.py`) is complete and tested.
