# Design: 30-Year Cash Flow Projection

**Date:** 2026-05-10
**Status:** Approved

---

## Overview

Add a year-by-year cash flow projection table to the RFS stress test results page, showing both an **unstressed** scenario (base reserve study assumptions) and a **stressed** scenario (median stress multipliers applied uniformly). The projection includes inflation on costs, return on the reserve balance, and growing contributions.

---

## Form Additions

Three new financial parameters in the existing Shock Parameters section (or a new "Financial Parameters" section):

| Field | Form name | Default | Notes |
|---|---|---|---|
| Inflation rate | `inflation_rate` | 0.03 | Applied to every component cost each future year |
| Return on savings | `interest_rate` | 0.025 | Earned on opening balance each year |
| Contribution growth | `contribution_growth` | 0.04 | Annual % increase in contribution |

---

## New Module: `projection.py`

### `build_yearly_schedule(components, horizon, inflation_rate, life_mult=1.0, cost_mult=1.0)`

Returns `{component_name: [yr1_cost, yr2_cost, ..., yr_horizon_cost]}`.

Logic:
- For each component:
  - Apply `life_mult` to design lives and `cost_mult` to base costs
  - Use the same offset-preserving event scheduling as `stress_test.schedule_track()` so year-1 events still happen in year 1
  - Inflate each event's cost by `(1 + inflation_rate) ** (year - 1)`
- Skip components with no events in the horizon

### `build_financial_projection(yearly_total_outflow, starting_reserve, annual_contribution, contribution_growth, interest_rate, num_units, horizon)`

Returns a list of dicts, one per year:
```python
[
  {
    "year": 1,
    "expenditure": ...,
    "opening_balance": ...,
    "contribution": ...,
    "return_on_savings": ...,
    "closing_balance": ...,
    "monthly_per_unit": ...,
    "special_assessment": ...,
  },
  ...
]
```

Logic per year:
1. `opening = closing_of_prior_year` (or `starting_reserve` for year 1)
2. `interest = opening × interest_rate`
3. `running = opening + contribution + interest − expenditure`
4. If `running < 0` → `special_assessment = -running`, `closing = 0`
5. Else → `special_assessment = 0`, `closing = running`
6. `monthly_per_unit = contribution / 12 / num_units`
7. `contribution *= (1 + contribution_growth)` for next year

---

## `app.py` Changes

In `/run`:
1. Read `inflation_rate`, `interest_rate`, `contribution_growth` from form
2. Build **unstressed projection**:
   - `schedule = build_yearly_schedule(components, horizon, inflation_rate, life_mult=1.0, cost_mult=1.0)`
   - `total_outflow = [sum(schedule[c][y] for c in schedule) for y in range(horizon)]`
   - `financials = build_financial_projection(total_outflow, ...)`
3. Build **stressed projection** the same way but with `life_mult=life_shock_mean` and `cost_mult=cost_shock_mean`
4. Add to summary:
   ```python
   summary["projection"] = {
     "unstressed": {"schedule": schedule_u, "financials": financials_u},
     "stressed":   {"schedule": schedule_s, "financials": financials_s},
     "params": {"inflation_rate": ..., "interest_rate": ..., "contribution_growth": ...}
   }
   ```

---

## `templates/results.html` Changes

New section between the existing Reserve Balance tables and the Risk Indicators panel.

### Layout

```
┌─ 30-Year Cash Flow Projection ──────────────────────────┐
│  [ Unstressed ] [ Stressed ]   ← tab switcher           │
│                                                         │
│  ┌─── Years 1–5 ─────────────────────────────────┐      │
│  │ Component       Yr 1   Yr 2   Yr 3   Yr 4   Yr 5    │
│  │ Asphalt         $0     $0     $43k   $0     $0      │
│  │ Concrete        $0     $0     $0     $0     $3.5k   │
│  │ ...                                                  │
│  ├─── Financials ────────────────────────────────┤      │
│  │ Expenditures    $0     $36k   $51k   $16k   $19k    │
│  │ Opening Balance $200k  $230k  ...                    │
│  │ Contributions   $25k   $25.3k ...                    │
│  │ Return On Savings ...                                │
│  │ Closing Balance ...                                  │
│  │ Avg Monthly/Unit $72   $73    ...                    │
│  │ Special Assessment $0  $0     ...                    │
│  └──────────────────────────────────────────────┘       │
│                                                         │
│  ┌─── Years 6–10 ────────────────────────────────┐      │
│  │ ...                                                  │
└─────────────────────────────────────────────────────────┘
```

Tab switcher uses Bootstrap nav-tabs to toggle between unstressed and stressed views.

For a 30-year horizon: 6 blocks. For shorter horizons: fewer blocks.

Components are sorted alphabetically. Components with zero expenditure across all 30 years are hidden.

---

## CSV Download

Update `/download-csv` to include a second sheet/section: the projection data (year-by-year by component) for both scenarios. Simplest: append rows to the existing CSV with a `view` column distinguishing `projection_unstressed_schedule`, `projection_unstressed_financials`, `projection_stressed_schedule`, `projection_stressed_financials`.

---

## Out of Scope

- Configurable inflation per component
- Component category grouping (CSV template doesn't include category yet)
- Charts / graphs
- Per-unit financial views (already covered by "Avg Monthly Per Unit" row)
