# Chart Axes, P-Percentile KPIs, Full-Coverage Warranty, Risk Tiers

**Status:** Design — not yet implemented
**Date:** 2026-05-14
**Builds on:** `2026-05-14-base-vs-full-funding-comparison-design.md`

## Goal

Three iterations on the live results page:

1. Make the forecast chart readable by giving the closing-balance lines their own Y-axis.
2. Replace median SA KPIs with a P5/P25/P50 distribution view of stress outcomes, add deterioration totals.
3. Reframe warranty risk around a full-coverage payout, three customer-facing risk tiers, and a Full Funding discount.

## 1. Chart — dual Y-axis

`templates/results.html` Chart.js config currently puts bars and lines on the same `y` scale. Switch to two scales:

- `y` (left): Dollars — Cash Flow. Bars only: P50 expenditure, Base contribution, Full Funding contribution.
- `y1` (right): Dollars — Reserve Balance. Lines only: P50 closing balance (Base), P50 closing balance (Full Funding).

Both axes use the same dollar formatter (`$` prefix, thousands separators). Each line dataset gets `yAxisID: 'y1'`; bar datasets default to `y`. Add `position: 'right'` and `grid: { drawOnChartArea: false }` to `y1` so the right-side gridlines don't overlay the left.

No other Chart.js options change.

## 2. KPIs — P5/P25/P50 distribution + deterioration block

### Stress-test aggregator (`stress_test.py`)

Add per-funding-model percentile arrays alongside the existing `prob_assessment` and `median_total_assessment` blocks. Drop the existing median-based fields (kept `prob_assessment` is unchanged).

In `_model_block(assess_mat)`:

```python
total_sa     = assess_mat.sum(axis=1)
n_years_arr  = (assess_mat > 0).sum(axis=1)

def _pcts(arr):
    # P5 = worst 5%   (= np.percentile(arr, 95))
    # P25 = worst 25% (= np.percentile(arr, 75))
    # P50 = typical   (= np.percentile(arr, 50))
    return {
        "p5":  float(np.percentile(arr, 95)),
        "p25": float(np.percentile(arr, 75)),
        "p50": float(np.percentile(arr, 50)),
    }

return {
    "raw_trials": {...},                    # unchanged
    "prob_assessment": {...},               # unchanged
    "sa_total":         _pcts(total_sa),
    "sa_per_unit":      {k: v / max(1, num_units) for k, v in _pcts(total_sa).items()},
    "n_assessment_years": _pcts(n_years_arr),
}
```

Removed from the per-model block: `median_total_assessment`, `median_n_assessment_years`.

Each metric is percentiled independently — `sa_total.p5` and `n_assessment_years.p5` need not come from the same trial. This is a deliberate statistical choice; the row reads "5% chance of being this bad or worse on this dimension."

### Deterioration

`calculate_annual_deterioration()` already returns `total_annual` and `per_unit_annual`. Surface both in the results template. The component-level breakdown table (`deterioration.components`) stays in the data structure but is not rendered.

`funding_ratio` (already a dict `{base, full}`) is unchanged.

### Results template

Above the comparison section, render a single "Property Deterioration" row:

```
Total annual deterioration: $X/yr   ·   Deterioration per unit: $X/yr/unit
```

Then **two** comparison tables (replacing the current single 7-row table):

**Table A — Probability of Special Assessment**

| Metric | Base | Full Funding |
|---|---|---|
| P(SA), years 1–5 | xx.x% | xx.x% |
| P(SA), years 1–10 | xx.x% | xx.x% |
| P(SA), 30 yr | xx.x% | xx.x% |
| Contribution ÷ annual deterioration | xxx% | xxx% |

**Table B — Special Assessment Distribution**

Six data columns, grouped header (Base/Full each spanning P5/P25/P50):

| Metric | Base P5 | Base P25 | Base P50 | Full P5 | Full P25 | Full P50 |
|---|---|---|---|---|---|---|
| Total SA over 30 yr | $ | $ | $ | $ | $ | $ |
| Total SA per unit | $ | $ | $ | $ | $ | $ |
| # assessment years (of 30) | n | n | n | n | n | n |

Removed from the page: the prior single table's "Median total SA / per unit / # assessment years" rows. Probability rows and funding-ratio row stay (now in Table A).

## 3. Warranty Risk Analysis — full coverage + risk tiers + Full Funding discount

### Math (in `calculate_warranty_risk_analysis()`)

New payout model. Any SA in the 5-year warranty window triggers the full coverage amount; no deductible reduction.

```python
payout_per_claim   = coverage_total                       # full coverage, was: coverage - deductible
claim_probability  = sum(1 for t in trials if t > 0) / len(trials)
expected_payout    = claim_probability * payout_per_claim
loss_ratio_default = expected_payout / warranty_premium   # used for verdict assignment
```

The `deductible` field is removed from the warranty data structure and the displayed card. Coverage_per_unit stays.

### Verdict tiers

Replace the four-tier verdict system with three customer-facing tiers, all keyed off `loss_ratio_default`:

| Loss ratio | Verdict key | Customer label | Color |
|---|---|---|---|
| ≤ 0.40 | `good` | Good property to insure | green (success) |
| 0.40–0.80 | `moderate` | Moderate risk | yellow (warning) |
| > 0.80 | `not_eligible` | Not eligible | red (danger) |

Verdict is **always** computed from the default-premium loss ratio. Neither the +25% Moderate adjustment nor the −20% Full Funding discount feeds back into verdict assignment.

### Displayed premium + displayed loss ratio adjustments

After verdict is locked from `loss_ratio_default`:

| Scenario | Displayed premium | Displayed loss ratio |
|---|---|---|
| Verdict = Moderate | `warranty_premium × 1.25` | `expected_payout / (premium × 1.25)` (recalculated) |
| Verdict = Good AND scenario = Full Funding | `warranty_premium × 0.80` | `loss_ratio_default` (unchanged) |
| Otherwise | `warranty_premium` | `loss_ratio_default` |

These adjustments apply only to **display**. The verdict is locked to `loss_ratio_default`; neither the +25% Moderate hike nor the −20% Full Funding discount feeds back into verdict assignment. The asymmetry — Moderate recalculates the displayed LR, Full Funding Good does not — is intentional: the +25% hike is a real pricing decision that lowers risk-adjusted LR (warrants showing customers), while the −20% discount is a marketing presentation on a property that is already well within the Good band.

### Eligibility / quote outputs

Replace the existing `verdict`, `verdict_label`, `eligibility`, `custom_quote` fields with a simpler shape:

```python
{
    "target_terms": {
        "warranty_term_years": 5,
        "coverage_per_unit": float,
        "coverage_total": float,
        "warranty_premium_default": float,
        "warranty_premium_displayed": float,    # default × 1.25 if Moderate; × 0.80 if Good+Full
        "target_loss_ratio": 0.40,              # changed from 0.50 to align with Good cutoff
    },
    "stress_results": {
        "n_trials": int,
        "claim_probability": float,
        "payout_per_claim": float,              # = coverage_total
        "payout_per_unit": float,               # = coverage_per_unit
        "expected_payout": float,
    },
    "loss_ratio_default": float,                # drives verdict
    "loss_ratio_displayed": float,              # at displayed premium (different only if Moderate)
    "verdict": "good" | "moderate" | "not_eligible",
    "verdict_label": "Good property to insure" | "Moderate risk" | "Not eligible",
    "verdict_color": "success" | "warning" | "danger",
    "verdict_message": str,                     # one-line explanation per tier
    "applied_full_funding_discount": bool,      # true only when scenario=full AND verdict=good
}
```

`property_type_used`, `standard_price_used`, `methodology_note` stay as today.

### Card layout (`templates/results.html`)

Render two side-by-side warranty blocks (Base | Full Funding) as today. Each block shows:

- Header: "Base Case" / "Full Funding" + property type + Standard $X
- Row 1: Warranty Premium (displayed) — with sub-label if adjusted: "+25% moderate risk" or "−20% Full Funding discount"
- Row 2: Coverage total · Coverage per unit (no deductible row)
- Row 3: Claim probability (5-yr)
- Row 4: Payout per claim · Payout per unit
- Row 5: Expected payout · Loss ratio (displayed)
- Row 6: Verdict banner (`alert-{color}`): label + message

Replace the existing "Custom Quote" / "Required Premium" section: not needed under the new model since Moderate adjustment is automatic and Not Eligible has no quote.

## What stays unchanged

- Monte Carlo methodology, σ=0.30 lock, dual-balance run_trial, common random numbers
- Chart datasets list (5 series) — only the axis assignment changes
- Form fields, /run / /download-csv route signatures (data shape inside changes; routes don't)
- Email / Sheets logging — payload field names already use base_p_sa_*/full_p_sa_*; we'll add the P5/P25/P50 totals to the log so the Sheet captures the distribution view too
- Component-level deterioration data in `summary.deterioration.components` (kept for downstream use even though the page doesn't render the table)

## Files touched

- `stress_test.py` — `_model_block` rewrite (drop medians, add P5/P25/P50 blocks). No signature change to `run_stress_test`.
- `app.py` — `calculate_warranty_risk_analysis` rewrite for the new payout model + tier system. Email body + Sheets `log_payload` updated to use new field names and add P5 totals.
- `templates/results.html` — two-table layout, deterioration row, dual Y-axis chart config, warranty card rewrite.
- `app.py` `/download-csv` — pulls from the new shape (per-model P5/P25/P50 totals + per-unit, prob_assessment, n_assessment_years).
- Tests: `tests/test_stress_test_mc.py` — update `test_run_stress_test_summary_shape` to assert the new keys (`sa_total`, `sa_per_unit`, `n_assessment_years` with `p5/p25/p50` each); add a test for the warranty tier logic if `calculate_warranty_risk_analysis` is unit-testable in isolation (it lives in `app.py` today — we may extract it, see below).

## Suggested refactor — extract `calculate_warranty_risk_analysis` into its own module

`calculate_warranty_risk_analysis` currently lives in `app.py` at ~150 lines and isn't unit-tested. Since we're rewriting it substantially and changing its output shape, this is a good moment to extract it to `warranty.py` and add direct unit tests for the tier boundaries (≤40%, 40-80%, >80%) and the discount logic. Keeps `app.py` focused on Flask glue.

If you'd rather keep it in `app.py` for this change and extract later, the spec accommodates either choice — the implementation plan will reflect whichever path you pick.

## Out of scope

- Province catalogue (still deferred from prior spec)
- User-editable σ or trial count
- Variable horizon
- Component-level deterioration table render (data still computed)
- Re-deriving verdict after the +25% or −20% adjustment (locked to default-premium LR by design)

## Open questions

None. All clarifications resolved in brainstorming session 2026-05-14.
