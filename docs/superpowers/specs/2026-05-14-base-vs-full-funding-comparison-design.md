# RFS Stress Test — Base Case vs. Full Funding Comparison

**Status:** Design — not yet implemented
**Date:** 2026-05-14

## Goal

Refocus the app around comparing two funding scenarios — Base Case and Full Funding — under a single Monte Carlo run. Strip out the inputs and outputs that no longer earn their place once the methodology is unified around P50-centered MC and a paired comparison.

## What changes

### Input form (`templates/index.html`)

Keep:

- Submitter info: `property_name`, `contact_name`, `contact_email`
- Reserve study: CSV upload (template unchanged)
- Property: `property_type`, `num_units`, `starting_reserve` (Opening Reserve Balance)
- Financial: `inflation_rate`, `interest_rate` (Return on Savings)

Replace the single `annual_contribution` + `contribution_growth` fields with:

- `base_contribution` ($)
- `full_funding_contribution` ($)

Both contribution streams grow at `inflation_rate` year-over-year. `contribution_growth` is deleted.

Delete entirely:

- Variability Parameters section (`cost_shock_mean`, `cost_shock_sigma`, `life_shock_mean`, `life_shock_sigma`)
- `num_trials` and `horizon` inputs

Hardcoded internally:

- `num_trials = 1000`
- `horizon = 30`
- Cost multiplier: lognormal, arithmetic mean 1.0, σ 0.30
- Life multiplier: normal, mean 1.0, σ 0.30

### Monte Carlo (`stress_test.py`)

One pass, common random numbers across both funding models:

1. Sample cost & life multipliers per trial × per component × per track (replacement + maintenance) — unchanged from current methodology.
2. For each trial, build the yearly outflow array using `_schedule_component_track`-style logic, inflated by `(1 + inflation_rate) ** (year - 1)` at each event.
3. Run the balance forward **twice in the same trial** — once under Base, once under Full Funding. Each contribution stream starts at the user-entered dollar amount in year 1 and grows by `(1 + inflation_rate)` annually. Interest accrues on `(opening + contribution)` at `interest_rate`. When balance would go negative, the deficit is recorded as a special assessment for that year and the balance resets to 0.
4. Persist per-trial per-year arrays for: `outflow`, `balance_base`, `balance_full`, `assess_base`, `assess_full`.

Per funding model, compute:

- `raw_trials.assessment_yr_1_5`, `.yr_1_10`, `.yr_total_30` (sums of assess[0:5], [0:10], [0:30] per trial)
- `prob_assessment.yr_1_5`, `.yr_1_10`, `.yr_30`
- `median_total_assessment` (total and per unit)
- `median_n_assessment_years`

For the chart, also compute per-year P50 across the 1000 trials:

- `p50_outflow_by_year[30]`
- `p50_balance_base_by_year[30]`, `p50_balance_full_by_year[30]`

The two contribution streams shown on the chart are deterministic (not P50): `base_contribution * (1 + inflation_rate) ** y` and the same for Full.

Delete from the summary returned to the view:

- `kpis_total`, `kpis_per_unit` (percentile tables of assessment totals)
- `kpis_balance_total`, `kpis_balance_per_unit`
- `expenditure_distribution`
- `assessment_frequency`

### Projection (`projection.py`)

The deterministic stressed/unstressed schedule is no longer used by the results page — the chart pulls P50 directly from MC outputs. `projection.py` and the `summary["projection"]` block in `app.py` are removed.

### Warranty analysis

`calculate_warranty_risk_analysis()` is unchanged structurally but is called **twice** — once with `raw_trials_base.assessment_yr_1_5`, once with `raw_trials_full.assessment_yr_1_5`. The summary stores both results under `summary["warranty_analysis"]["base"]` and `summary["warranty_analysis"]["full"]`.

### Deterioration / funding ratio

`calculate_annual_deterioration()` stays. `funding_ratio` is now a dict: `{ "base": ..., "full": ... }`, each = `(year-1 contribution) / total_annual_deterioration`. The component-breakdown table is dropped from the results page; only the summary numbers stay.

### Results page (`templates/results.html`)

Four cards, top to bottom:

1. **Configuration**: units · opening reserve · base contribution · full funding contribution · inflation · interest. One row.

2. **30-year forecast chart** (Chart.js, mixed bar + line):
   - X axis: years 1–30
   - Bars (grouped, three per year): P50 expenditure (red), Base contribution (light blue), Full Funding contribution (dark blue)
   - Lines: P50 closing balance under Base (light blue), P50 closing balance under Full (dark blue)
   - Y axis: dollars, single scale
   - Single legend

3. **KPI comparison table** — two columns (Base | Full Funding), one row per metric:
   - P(SA), years 1–5
   - P(SA), years 1–10
   - P(SA), 30 yr
   - Median total SA over 30 yr (total)
   - Median total SA per unit
   - Median # assessment years (of 30)
   - Contribution ÷ annual deterioration (funding ratio)

4. **Warranty risk analysis** — current card structure but rendered twice side-by-side. Each side shows: premium, claim probability, expected payout, loss ratio, verdict, eligibility, custom quote. Header band labels each column ("Base Case" / "Full Funding").

Removed from the page entirely: tabbed unstressed/stressed projections, percentile assessment tables, balance percentile tables, expenditure distribution card, assessment frequency card, deterioration component breakdown table.

### App glue (`app.py`)

- `/run` parses two contribution amounts instead of one. Drops shock-param parsing, `contribution_growth`, and `num_trials`/`horizon`.
- Calls `run_stress_test()` once with both contribution streams. Receives the per-model summary blocks.
- Calls `calculate_warranty_risk_analysis()` twice; stitches under `summary["warranty_analysis"]["base"]` and `["full"]`.
- Email body and Sheets payload: include both contribution amounts and both 1-5yr P(SA) values; drop the old shock-param fields.
- `/download-csv`: flatten the new shape (one row per metric × funding model).

### Chart library

[Chart.js 4.x](https://www.chartjs.org/) loaded from CDN. Single `<script>` tag in `results.html`. No build step.

## Data flow

```
CSV upload
   └─ parse_reserve_csv() → components
        └─ stress_test.run_stress_test(components, base, full, inflation, interest, num_units)
              ├─ shared multipliers per trial × component × track (CRN)
              ├─ yearly outflow per trial (inflation-applied)
              ├─ balance forward under Base contribution stream
              ├─ balance forward under Full Funding contribution stream
              └─ returns summary {
                    config: { num_units, starting_reserve, base_contribution,
                              full_funding_contribution, inflation_rate, interest_rate,
                              horizon, num_trials },
                    raw_trials_base: { assessment_yr_1_5, assessment_yr_1_10, assessment_yr_total_30 },
                    raw_trials_full: { assessment_yr_1_5, assessment_yr_1_10, assessment_yr_total_30 },
                    base: { prob_assessment: {yr_1_5, yr_1_10, yr_30},
                            median_total_assessment: {total, per_unit},
                            median_n_assessment_years },
                    full: { prob_assessment: {yr_1_5, yr_1_10, yr_30},
                            median_total_assessment: {total, per_unit},
                            median_n_assessment_years },
                    chart: { years[30], p50_outflow[30], base_contribution_stream[30],
                             full_contribution_stream[30], p50_balance_base[30], p50_balance_full[30] }
                 }
        └─ app.py: calculate_annual_deterioration, calculate_warranty_risk_analysis × 2
        └─ render_template(results.html, summary)
```

## Files touched

- `templates/index.html` — form rewrite (drop variability + horizon + trials + contribution_growth, add two contribution fields)
- `templates/results.html` — full rewrite around the four-card layout, add Chart.js
- `stress_test.py` — strip percentile aggregations, add dual-balance trial loop, add per-year P50 arrays, return restructured summary
- `projection.py` — delete (functions no longer called)
- `app.py` — `/run` rewrite per above, `/download-csv` updated, projection imports removed
- `csv_parser.py` — unchanged
- `pricing.py` — unchanged
- `static/reserve_study_template.csv` — unchanged

## Out of scope

- Province catalogue selection (deferred to a separate project)
- User-editable σ or trial count (locked at 0.30 and 1000)
- Variable horizon (locked at 30)
- Component-level cost overrides

## Open questions

None. All clarifications resolved in brainstorming session 2026-05-14.
