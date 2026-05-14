# Chart Axes, P-Percentile KPIs, Risk-Tier Warranty Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Three iterations on the live results page — dual Y-axis chart, P5/P25/P50 special-assessment distribution + deterioration totals, and a reframed warranty card with full-coverage payout, Good/Moderate/Not-Eligible tiers, and a Full Funding −20% discount.

**Architecture:** Extract `calculate_warranty_risk_analysis` from `app.py` into a new `warranty.py` module so the new payout model and tier logic can be unit-tested in isolation. Add P5/P25/P50 percentile blocks to `stress_test._model_block`. Two new tables in `results.html` replace the single comparison table; the Chart.js config gets a second Y-axis.

**Tech Stack:** Python 3.11, Flask, NumPy, Chart.js 4.x, Bootstrap 5.3, pytest 8.x.

**Spec:** `docs/superpowers/specs/2026-05-14-chart-axes-p-percentiles-warranty-tiers-design.md`

**Base SHA:** `7d6a6cb` (current main)

---

## File Structure

**Created:**
- `warranty.py` — pure-function warranty risk analysis: payout math, verdict tier assignment, premium adjustments, output struct construction. No Flask dependency.
- `tests/test_warranty.py` — unit tests for tier boundaries, full-coverage payout math, Moderate +25% adjustment, Full Funding −20% discount.

**Modified:**
- `stress_test.py` — `_model_block` rewrite: drop `median_total_assessment` and `median_n_assessment_years`; add `sa_total`, `sa_per_unit`, `n_assessment_years` blocks with `{p5, p25, p50}` each. No signature change to `run_stress_test`.
- `app.py` — replace the in-file `calculate_warranty_risk_analysis` with a `from warranty import calculate_warranty_risk_analysis` import + adjusted call site that passes the new `funding_model` arg. `/run` adds `scenario` flag per call; `/download-csv` pulls from the new shape. Email body + Sheets `log_payload` updated.
- `templates/results.html` — two-table KPI layout (Probability + Distribution), deterioration row, dual Y-axis Chart.js config, warranty card rewrite per spec.
- `tests/test_stress_test_mc.py` — `test_run_stress_test_summary_shape` updated to assert new percentile keys.

---

## Task 1: TDD — `_model_block` percentile rewrite in `stress_test.py`

**Files:**
- Modify: `tests/test_stress_test_mc.py`
- Modify: `stress_test.py` (`_model_block` inner function)

- [ ] **Step 1: Update the existing `test_run_stress_test_summary_shape` test**

Open `tests/test_stress_test_mc.py`. Find the existing `test_run_stress_test_summary_shape` function. Replace its "per-model derived" block with the new assertions for percentile keys.

Specifically, replace this block in the test:

```python
    # per-model derived
    for key in ("base", "full"):
        m = summary[key]
        assert set(m["prob_assessment"]) == {"yr_1_5", "yr_1_10", "yr_30"}
        for v in m["prob_assessment"].values():
            assert 0.0 <= v <= 1.0
        assert "total" in m["median_total_assessment"]
        assert "per_unit" in m["median_total_assessment"]
        assert isinstance(m["median_n_assessment_years"], float)
```

With:

```python
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
```

- [ ] **Step 2: Add a focused test for the per-unit derivation**

Append this new test to `tests/test_stress_test_mc.py`:

```python
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
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd /Users/stevenlaidlaw/RFS_Warranty_Project
python3 -m pytest tests/test_stress_test_mc.py -v
```

Expected: `test_run_stress_test_summary_shape` fails on missing `sa_total` key. `test_run_stress_test_sa_per_unit_is_total_divided_by_units` fails on KeyError.

- [ ] **Step 4: Rewrite `_model_block` in `stress_test.py`**

Open `stress_test.py`. Find the `_model_block` inner function inside `run_stress_test`. Replace its body with:

```python
    def _model_block(assess_mat: np.ndarray) -> Dict[str, Any]:
        a_1_5  = assess_mat[:, 0:min(5, horizon)].sum(axis=1)
        a_1_10 = assess_mat[:, 0:min(10, horizon)].sum(axis=1)
        a_total = assess_mat.sum(axis=1)
        n_yrs   = (assess_mat > 0).sum(axis=1).astype(float)

        def _pcts(arr: np.ndarray) -> Dict[str, float]:
            # P5  = worst 5%   → 95th percentile of the metric
            # P25 = worst 25%  → 75th percentile
            # P50 = typical    → 50th percentile (median)
            return {
                "p5":  float(np.percentile(arr, 95)),
                "p25": float(np.percentile(arr, 75)),
                "p50": float(np.percentile(arr, 50)),
            }

        sa_total = _pcts(a_total)
        sa_per_unit = {k: v / max(1, num_units) for k, v in sa_total.items()}

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
            "sa_total":           sa_total,
            "sa_per_unit":        sa_per_unit,
            "n_assessment_years": _pcts(n_yrs),
        }
```

(This removes the `median_total_assessment` and `median_n_assessment_years` keys; replaces them with the three percentile blocks.)

- [ ] **Step 5: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_stress_test_mc.py -v
```

Expected: all 8 tests pass (7 original + 1 new).

Also run csv_parser to confirm no regression:

```bash
python3 -m pytest tests/ -q
```

Expected: 16 passed.

- [ ] **Step 6: Commit**

```bash
git add stress_test.py tests/test_stress_test_mc.py
git commit -m "refactor(mc): replace median SA KPIs with P5/P25/P50 percentile blocks"
```

---

## Task 2: TDD — Extract warranty logic into `warranty.py` (preserve current behavior)

This is a pure mechanical extraction. We move the function from `app.py` to `warranty.py` without changing its math. Task 3 then rewrites the math inside `warranty.py`.

**Files:**
- Create: `warranty.py`
- Create: `tests/test_warranty.py`
- Modify: `app.py` (replace inline function with import)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_warranty.py` with this exact content:

```python
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from warranty import calculate_warranty_risk_analysis


def _trials(claim_prob: float, n: int = 1000) -> dict:
    """Build a raw_trials dict where exactly claim_prob fraction of trials had an SA."""
    n_with = int(round(n * claim_prob))
    assessment_yr_1_5 = [1.0] * n_with + [0.0] * (n - n_with)
    return {"assessment_yr_1_5": assessment_yr_1_5}


def test_warranty_returns_none_if_no_trials():
    result = calculate_warranty_risk_analysis(
        raw_trials={"assessment_yr_1_5": []},
        num_units=10,
        standard_price=10_000.0,
    )
    assert result is None


def test_warranty_basic_shape():
    result = calculate_warranty_risk_analysis(
        raw_trials=_trials(0.2),
        num_units=10,
        standard_price=10_000.0,
    )
    assert result is not None
    assert result["target_terms"]["coverage_per_unit"] == 500.0
    assert result["target_terms"]["coverage_total"] == 5_000.0
    assert "warranty_premium" in result["target_terms"]
    assert "claim_probability" in result["stress_results"]
    assert abs(result["stress_results"]["claim_probability"] - 0.2) < 1e-9
    assert "actual_loss_ratio" in result
    assert "verdict" in result
```

- [ ] **Step 2: Run tests to verify they fail with ModuleNotFoundError**

```bash
cd /Users/stevenlaidlaw/RFS_Warranty_Project
python3 -m pytest tests/test_warranty.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'warranty'`.

- [ ] **Step 3: Create `warranty.py` by copying the function from `app.py`**

Create the file `warranty.py` at the project root with this content (a near-verbatim copy of the existing `app.py` function — same math, same output shape, no Flask deps):

```python
"""
Warranty risk analysis for the RFS Warranty Project.

Pure functions — no Flask, no IO. Takes the per-trial 1-5yr special-assessment
totals from the Monte Carlo run and produces a quote + verdict for the warranty
product.

Current model (rewritten in a follow-up task):
  - premium_markup_pct × standard_price = one-time warranty premium
  - lump payout per claim = coverage_total − deductible (10% of coverage)
  - verdict tiers based on actual_loss_ratio vs target_loss_ratio
"""

from typing import Any, Dict, Optional


def calculate_warranty_risk_analysis(
    raw_trials: Dict[str, Any],
    num_units: int,
    standard_price: float,
    coverage_per_unit: float = 500.0,
    premium_markup_pct: float = 0.50,
    warranty_term_years: int = 5,
    target_loss_ratio: float = 0.50,
) -> Optional[Dict[str, Any]]:
    """
    Compute warranty pricing terms + verdict from a list of per-trial SA totals.

    raw_trials must have key 'assessment_yr_1_5' — a list of per-trial cumulative
    special assessment $ in years 1–5. P(claim) = fraction of trials with any SA.
    Returns None if no trials provided.
    """
    trials = raw_trials.get("assessment_yr_1_5", [])
    if not trials:
        return None

    coverage = coverage_per_unit * num_units
    warranty_premium = standard_price * premium_markup_pct
    deductible = coverage * 0.10

    payout_per_claim   = max(0.0, coverage - deductible)
    claim_count        = sum(1 for t in trials if t > 0)
    claim_probability  = claim_count / len(trials)
    expected_payout    = payout_per_claim * claim_probability

    expected_assessment_uncapped = sum(trials) / len(trials)

    actual_loss_ratio = (
        expected_payout / warranty_premium if warranty_premium > 0 else 0.0
    )

    if actual_loss_ratio <= 0.30:
        verdict = "highly_profitable"
        verdict_label = "Highly profitable — room to expand coverage or reduce premium"
    elif actual_loss_ratio <= target_loss_ratio:
        verdict = "on_target"
        verdict_label = "On target — warranty is profitable at these terms"
    elif actual_loss_ratio <= target_loss_ratio + 0.20:
        verdict = "marginal"
        verdict_label = "Marginal — close to break-even; consider adjusting terms"
    else:
        verdict = "underpriced"
        verdict_label = "Underpriced — warranty cannot cover modeled losses at these terms"

    if actual_loss_ratio <= target_loss_ratio:
        eligibility = "eligible"
        eligibility_label = "Eligible at standard terms"
        eligibility_color = "success"
        eligibility_reason = (
            f"Loss ratio {actual_loss_ratio:.0%} is at or below target ({target_loss_ratio:.0%}). "
            f"Standard 1.50× Standard pricing covers the modeled risk."
        )
    elif actual_loss_ratio <= 1.00:
        eligibility = "conditional"
        eligibility_label = "Conditional — custom quote required"
        eligibility_color = "warning"
        eligibility_reason = (
            f"Loss ratio {actual_loss_ratio:.0%} exceeds target ({target_loss_ratio:.0%}). "
            f"Standard pricing is insufficient — quote at the custom premium below."
        )
    else:
        eligibility = "decline"
        eligibility_label = "Decline — request funding plan first"
        eligibility_color = "danger"
        eligibility_reason = (
            f"Loss ratio {actual_loss_ratio:.0%} exceeds 100% — warranty would pay out more "
            f"than it collects. Property is structurally underfunded; recommend the board "
            f"address contribution levels before considering warranty."
        )

    custom_quote = None
    if eligibility != "eligible" and expected_payout > 0:
        required_premium = expected_payout / target_loss_ratio
        required_premium_tier_mult = (
            (standard_price + required_premium) / standard_price
            if standard_price else 0.0
        )
        custom_quote = {
            "required_premium":            round(required_premium, 0),
            "required_premium_tier_mult":  round(required_premium_tier_mult, 2),
            "default_premium_tier_mult":   round(1.0 + premium_markup_pct, 2),
            "premium_increase_pct":        round(
                (required_premium - warranty_premium) / warranty_premium, 2
            ) if warranty_premium > 0 else 0.0,
        }

    return {
        "target_terms": {
            "warranty_term_years":  warranty_term_years,
            "coverage_per_unit":    coverage_per_unit,
            "coverage_total":       coverage,
            "deductible":           deductible,
            "premium_markup_pct":   premium_markup_pct,
            "warranty_premium":     warranty_premium,
            "target_loss_ratio":    target_loss_ratio,
        },
        "stress_results": {
            "n_trials":                     len(trials),
            "claim_probability":            claim_probability,
            "payout_per_claim":             round(payout_per_claim, 0),
            "expected_payout":              round(expected_payout, 0),
            "mean_assessment_uncapped":     round(expected_assessment_uncapped, 0),
        },
        "actual_loss_ratio": round(actual_loss_ratio, 4),
        "verdict":           verdict,
        "verdict_label":     verdict_label,
        "eligibility": {
            "decision": eligibility,
            "label":    eligibility_label,
            "color":    eligibility_color,
            "reason":   eligibility_reason,
        },
        "custom_quote": custom_quote,
    }
```

- [ ] **Step 4: Replace `app.py`'s in-file function with an import**

In `app.py`, find the `def calculate_warranty_risk_analysis(...)` function (around line 44, ~150 lines long) and **delete the entire function body**. In its place, near the other imports at the top of the file (after the existing `from csv_parser import parse_reserve_csv` line), add:

```python
from warranty import calculate_warranty_risk_analysis
```

(Keep all other code in `app.py` — `/run` still references the function by name; only its definition moves.)

- [ ] **Step 5: Run all tests to verify behavior is preserved**

```bash
python3 -m pytest tests/ -v
```

Expected: 18 passed (16 from before + 2 new in `test_warranty.py`).

Also smoke check the app imports cleanly:

```bash
python3 -c "import app; print('ok')"
```

Expected: `ok`.

- [ ] **Step 6: Commit**

```bash
git add warranty.py tests/test_warranty.py app.py
git commit -m "refactor(warranty): extract calculate_warranty_risk_analysis to warranty.py"
```

---

## Task 3: TDD — Full-coverage payout + risk tier rewrite in `warranty.py`

**Files:**
- Modify: `tests/test_warranty.py`
- Modify: `warranty.py`

- [ ] **Step 1: Write the failing tests**

Replace the content of `tests/test_warranty.py` (currently has 2 simple tests) with this expanded suite:

```python
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from warranty import calculate_warranty_risk_analysis


def _trials(claim_prob: float, n: int = 1000) -> dict:
    """Build a raw_trials dict where exactly claim_prob fraction of trials had an SA."""
    n_with = int(round(n * claim_prob))
    assessment_yr_1_5 = [1.0] * n_with + [0.0] * (n - n_with)
    return {"assessment_yr_1_5": assessment_yr_1_5}


def test_warranty_returns_none_if_no_trials():
    result = calculate_warranty_risk_analysis(
        raw_trials={"assessment_yr_1_5": []},
        num_units=10,
        standard_price=10_000.0,
    )
    assert result is None


def test_payout_is_full_coverage_no_deductible():
    # 10 units × $500 coverage_per_unit = $5,000 total coverage
    result = calculate_warranty_risk_analysis(
        raw_trials=_trials(0.5),
        num_units=10,
        standard_price=10_000.0,
    )
    t = result["target_terms"]
    s = result["stress_results"]
    assert t["coverage_total"] == 5_000.0
    assert t["coverage_per_unit"] == 500.0
    assert "deductible" not in t           # deductible removed
    assert s["payout_per_claim"] == 5_000.0  # full coverage
    assert s["payout_per_unit"] == 500.0     # coverage_per_unit


def test_verdict_good_at_low_loss_ratio():
    # premium = 0.50 × 10k = 5,000. coverage = 5,000.
    # claim_prob 0.3 → expected_payout = 0.3 × 5000 = 1,500 → LR = 0.30
    result = calculate_warranty_risk_analysis(
        raw_trials=_trials(0.3),
        num_units=10,
        standard_price=10_000.0,
    )
    assert result["verdict"] == "good"
    assert result["verdict_label"] == "Good property to insure"
    assert result["verdict_color"] == "success"
    # No premium adjustment in Base scenario
    assert result["target_terms"]["warranty_premium_displayed"] == \
           result["target_terms"]["warranty_premium_default"]


def test_verdict_moderate_at_mid_loss_ratio():
    # claim_prob 0.6 → expected_payout = 0.6 × 5000 = 3,000 → LR = 0.60 → moderate
    result = calculate_warranty_risk_analysis(
        raw_trials=_trials(0.6),
        num_units=10,
        standard_price=10_000.0,
    )
    assert result["verdict"] == "moderate"
    assert result["verdict_color"] == "warning"
    # Displayed premium = default × 1.25
    t = result["target_terms"]
    assert abs(t["warranty_premium_displayed"] - t["warranty_premium_default"] * 1.25) < 0.01
    # Displayed LR recalculated against the higher premium → lower than default
    assert result["loss_ratio_displayed"] < result["loss_ratio_default"]


def test_verdict_not_eligible_at_high_loss_ratio():
    # claim_prob 0.9 → expected_payout = 4,500 → LR = 0.90 → not_eligible
    result = calculate_warranty_risk_analysis(
        raw_trials=_trials(0.9),
        num_units=10,
        standard_price=10_000.0,
    )
    assert result["verdict"] == "not_eligible"
    assert result["verdict_color"] == "danger"
    # No premium displayed for ineligible: should equal default (no adjustment)
    t = result["target_terms"]
    assert t["warranty_premium_displayed"] == t["warranty_premium_default"]


def test_full_funding_discount_when_good():
    # Same trials as test_verdict_good, but with funding_model="full"
    result = calculate_warranty_risk_analysis(
        raw_trials=_trials(0.3),
        num_units=10,
        standard_price=10_000.0,
        funding_model="full",
    )
    assert result["verdict"] == "good"
    t = result["target_terms"]
    # -20% discount applied to displayed premium
    assert abs(t["warranty_premium_displayed"] - t["warranty_premium_default"] * 0.80) < 0.01
    assert result["applied_full_funding_discount"] is True
    # Loss ratio shown stays at default (not recalculated) per spec
    assert result["loss_ratio_displayed"] == result["loss_ratio_default"]


def test_no_full_funding_discount_when_moderate():
    # Moderate verdict → no discount even if scenario=full
    result = calculate_warranty_risk_analysis(
        raw_trials=_trials(0.6),
        num_units=10,
        standard_price=10_000.0,
        funding_model="full",
    )
    assert result["verdict"] == "moderate"
    assert result["applied_full_funding_discount"] is False
    # Moderate +25% adjustment applies regardless of funding model
    t = result["target_terms"]
    assert abs(t["warranty_premium_displayed"] - t["warranty_premium_default"] * 1.25) < 0.01


def test_base_scenario_never_gets_discount():
    # Base scenario, Good verdict — no discount
    result = calculate_warranty_risk_analysis(
        raw_trials=_trials(0.3),
        num_units=10,
        standard_price=10_000.0,
        funding_model="base",
    )
    assert result["verdict"] == "good"
    assert result["applied_full_funding_discount"] is False
    t = result["target_terms"]
    assert t["warranty_premium_displayed"] == t["warranty_premium_default"]


def test_verdict_boundary_at_40_pct_is_good():
    # LR exactly at 0.40 should be Good (≤ 0.40 boundary)
    # premium=5000, claim_prob=0.4 → expected_payout=2000 → LR=0.40
    result = calculate_warranty_risk_analysis(
        raw_trials=_trials(0.4),
        num_units=10,
        standard_price=10_000.0,
    )
    assert abs(result["loss_ratio_default"] - 0.40) < 1e-9
    assert result["verdict"] == "good"


def test_verdict_boundary_at_80_pct_is_moderate():
    # LR exactly at 0.80 should be Moderate (≤ 0.80 boundary)
    result = calculate_warranty_risk_analysis(
        raw_trials=_trials(0.8),
        num_units=10,
        standard_price=10_000.0,
    )
    assert abs(result["loss_ratio_default"] - 0.80) < 1e-9
    assert result["verdict"] == "moderate"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/stevenlaidlaw/RFS_Warranty_Project
python3 -m pytest tests/test_warranty.py -v
```

Expected: 8 of 10 tests fail (the function still uses the old model; new fields like `payout_per_unit`, `warranty_premium_displayed`, `funding_model`, `verdict="good"` don't exist yet).

- [ ] **Step 3: Rewrite `warranty.py`**

Replace the entire content of `warranty.py` with:

```python
"""
Warranty risk analysis for the RFS Warranty Project.

Pure functions — no Flask, no IO. Takes the per-trial 1-5yr special-assessment
totals from the Monte Carlo run and produces a quote + verdict for the warranty
product.

Model (post-2026-05-14 spec):
  - Payout per claim = full coverage_total on ANY special assessment in the term
    (no deductible reduction). Coverage_per_unit defaults to $500.
  - Verdict tiers driven by default-premium loss ratio:
      ≤ 0.40        → "good"          (Good property to insure)
      0.40 – 0.80   → "moderate"      (Moderate risk; displayed premium × 1.25)
      > 0.80        → "not_eligible"  (Not eligible)
  - Funding-model discount: when funding_model="full" AND verdict="good", the
    displayed premium is reduced by 20%. Displayed loss ratio in this case is
    kept at the default-premium value (spec: do not recalculate).
"""

from typing import Any, Dict, Optional


GOOD_CEILING       = 0.40   # loss ratio ≤ 0.40 → Good
MODERATE_CEILING   = 0.80   # loss ratio ≤ 0.80 → Moderate; > 0.80 → Not eligible
MODERATE_PREMIUM_MULT = 1.25
FULL_FUNDING_DISCOUNT = 0.80   # 20% off


def calculate_warranty_risk_analysis(
    raw_trials: Dict[str, Any],
    num_units: int,
    standard_price: float,
    coverage_per_unit: float = 500.0,
    premium_markup_pct: float = 0.50,
    warranty_term_years: int = 5,
    funding_model: str = "base",
) -> Optional[Dict[str, Any]]:
    """
    Compute warranty pricing terms + verdict from a list of per-trial SA totals.

    raw_trials['assessment_yr_1_5']: list of per-trial cumulative SA $ in years 1–5.
    P(claim) = fraction of trials with any SA > 0.
    funding_model: "base" or "full". Only "full" + verdict="good" triggers the 20% discount.

    Returns None if no trials provided.
    """
    trials = raw_trials.get("assessment_yr_1_5", [])
    if not trials:
        return None

    coverage_total = coverage_per_unit * num_units
    warranty_premium_default = standard_price * premium_markup_pct

    payout_per_claim   = coverage_total          # full coverage on any SA
    payout_per_unit    = coverage_per_unit
    claim_count        = sum(1 for t in trials if t > 0)
    claim_probability  = claim_count / len(trials)
    expected_payout    = payout_per_claim * claim_probability

    loss_ratio_default = (
        expected_payout / warranty_premium_default
        if warranty_premium_default > 0 else 0.0
    )

    # Verdict comes from the default-premium loss ratio. Locked.
    if loss_ratio_default <= GOOD_CEILING:
        verdict = "good"
        verdict_label = "Good property to insure"
        verdict_color = "success"
        verdict_message = (
            f"Loss ratio {loss_ratio_default:.0%} is comfortably within the Good "
            f"tier (≤{GOOD_CEILING:.0%}). Eligible at standard premium."
        )
    elif loss_ratio_default <= MODERATE_CEILING:
        verdict = "moderate"
        verdict_label = "Moderate risk"
        verdict_color = "warning"
        verdict_message = (
            f"Loss ratio {loss_ratio_default:.0%} sits in the Moderate tier "
            f"({GOOD_CEILING:.0%}–{MODERATE_CEILING:.0%}). Eligible with a 25% "
            f"premium increase to cover the modeled risk."
        )
    else:
        verdict = "not_eligible"
        verdict_label = "Not eligible"
        verdict_color = "danger"
        verdict_message = (
            f"Loss ratio {loss_ratio_default:.0%} exceeds the Moderate ceiling "
            f"({MODERATE_CEILING:.0%}). Recommend the board address funding "
            f"levels before re-quoting."
        )

    # Displayed premium + displayed LR per spec table.
    applied_full_funding_discount = (
        funding_model == "full" and verdict == "good"
    )
    if verdict == "moderate":
        warranty_premium_displayed = warranty_premium_default * MODERATE_PREMIUM_MULT
        loss_ratio_displayed = (
            expected_payout / warranty_premium_displayed
            if warranty_premium_displayed > 0 else 0.0
        )
    elif applied_full_funding_discount:
        warranty_premium_displayed = warranty_premium_default * FULL_FUNDING_DISCOUNT
        loss_ratio_displayed = loss_ratio_default      # spec: keep original LR
    else:
        warranty_premium_displayed = warranty_premium_default
        loss_ratio_displayed = loss_ratio_default

    return {
        "target_terms": {
            "warranty_term_years":         warranty_term_years,
            "coverage_per_unit":           coverage_per_unit,
            "coverage_total":              coverage_total,
            "warranty_premium_default":    round(warranty_premium_default, 0),
            "warranty_premium_displayed":  round(warranty_premium_displayed, 0),
            "target_loss_ratio":           GOOD_CEILING,
        },
        "stress_results": {
            "n_trials":           len(trials),
            "claim_probability":  claim_probability,
            "payout_per_claim":   round(payout_per_claim, 0),
            "payout_per_unit":    round(payout_per_unit, 0),
            "expected_payout":    round(expected_payout, 0),
        },
        "loss_ratio_default":   round(loss_ratio_default, 4),
        "loss_ratio_displayed": round(loss_ratio_displayed, 4),
        "verdict":              verdict,
        "verdict_label":        verdict_label,
        "verdict_color":        verdict_color,
        "verdict_message":      verdict_message,
        "applied_full_funding_discount": applied_full_funding_discount,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_warranty.py -v
```

Expected: 10/10 pass.

- [ ] **Step 5: Run all tests for regression**

```bash
python3 -m pytest tests/ -v
```

Expected: 26 pass. The `/run` route in `app.py` still uses the OLD field names (`actual_loss_ratio`, `verdict_label` text, `eligibility` block, `custom_quote`) — that's a known broken state that Task 4 will fix. `app.py` should still IMPORT cleanly because we only removed fields, but `/run` will throw at runtime. The test suite doesn't exercise `/run`, so tests pass.

Also verify import:

```bash
python3 -c "import app; print('ok')"
```

Expected: `ok`.

- [ ] **Step 6: Commit**

```bash
git add warranty.py tests/test_warranty.py
git commit -m "feat(warranty): full-coverage payout, 3-tier verdict, Full Funding discount"
```

---

## Task 4: Update `/run` and `/download-csv` for the new warranty + percentile shape

**Files:**
- Modify: `app.py` (`run()` function — the warranty call block, log_payload, email body; `download_csv()` function)

- [ ] **Step 1: Replace the warranty section of `/run`**

In `app.py`, find this block inside `def run():` (currently around the warranty analysis section):

```python
    # Warranty risk analysis × 2 (one per funding model)
    property_type = request.form.get("property_type", "Apartment").strip() or "Apartment"
    try:
        from pricing import BASIC_PRICES, STANDARD_MARKUP, bracket_index_for_units, _round_to_50
        idx = bracket_index_for_units(num_units)
        if property_type in BASIC_PRICES:
            basic_price = BASIC_PRICES[property_type][idx]
        else:
            basic_price = BASIC_PRICES["Apartment"][idx]
        standard_price = _round_to_50(basic_price * STANDARD_MARKUP)

        def _warranty(raw_trials_dict):
            return calculate_warranty_risk_analysis(
                raw_trials={"assessment_yr_1_5": raw_trials_dict["assessment_yr_1_5"]},
                num_units=num_units,
                standard_price=standard_price,
                coverage_per_unit=500.0,
                premium_markup_pct=0.50,
                warranty_term_years=5,
                target_loss_ratio=0.50,
            )

        wa_base = _warranty(summary["raw_trials_base"])
        wa_full = _warranty(summary["raw_trials_full"])
        for wa in (wa_base, wa_full):
            if wa:
                wa["property_type_used"] = property_type
                wa["standard_price_used"] = standard_price
                wa["methodology_note"] = (
                    "Probability computed from 1000 Monte Carlo trials. "
                    "Each component gets independent random cost (lognormal, mean=1.0) "
                    "and life (normal, mean=1.0) multipliers per trial; σ=0.30 for both."
                )
        summary["warranty_analysis"] = {"base": wa_base, "full": wa_full}
    except Exception as e:
        print(f"Warranty analysis failed: {e}")
        summary["warranty_analysis"] = {"base": None, "full": None}
```

Replace it with this updated version (passes `funding_model`, drops `target_loss_ratio` arg, fixes the `_warranty` signature):

```python
    # Warranty risk analysis × 2 (one per funding model)
    property_type = request.form.get("property_type", "Apartment").strip() or "Apartment"
    try:
        from pricing import BASIC_PRICES, STANDARD_MARKUP, bracket_index_for_units, _round_to_50
        idx = bracket_index_for_units(num_units)
        if property_type in BASIC_PRICES:
            basic_price = BASIC_PRICES[property_type][idx]
        else:
            basic_price = BASIC_PRICES["Apartment"][idx]
        standard_price = _round_to_50(basic_price * STANDARD_MARKUP)

        def _warranty(raw_trials_dict, funding_model):
            return calculate_warranty_risk_analysis(
                raw_trials={"assessment_yr_1_5": raw_trials_dict["assessment_yr_1_5"]},
                num_units=num_units,
                standard_price=standard_price,
                coverage_per_unit=500.0,
                premium_markup_pct=0.50,
                warranty_term_years=5,
                funding_model=funding_model,
            )

        wa_base = _warranty(summary["raw_trials_base"], "base")
        wa_full = _warranty(summary["raw_trials_full"], "full")
        for wa in (wa_base, wa_full):
            if wa:
                wa["property_type_used"] = property_type
                wa["standard_price_used"] = standard_price
                wa["methodology_note"] = (
                    "Probability computed from 1000 Monte Carlo trials. "
                    "Each component gets independent random cost (lognormal, mean=1.0) "
                    "and life (normal, mean=1.0) multipliers per trial; σ=0.30 for both. "
                    "Any special assessment in years 1–5 triggers the full coverage payout."
                )
        summary["warranty_analysis"] = {"base": wa_base, "full": wa_full}
    except Exception as e:
        print(f"Warranty analysis failed: {e}")
        summary["warranty_analysis"] = {"base": None, "full": None}
```

- [ ] **Step 2: Update the `log_payload` block in `/run`**

In `app.py` inside `run()`, find the `log_payload = { ... }` dict. Replace it with the new shape (drops `*_median_total_sa`, adds `*_sa_total_p5`):

```python
    log_payload = {
        "timestamp_utc":             timestamp,
        "property_name":             property_name,
        "contact_name":              contact_name,
        "contact_email":             contact_email,
        "property_type":             property_type,
        "num_units":                 num_units,
        "starting_reserve":          starting_reserve,
        "base_contribution":         base_contribution,
        "full_funding_contribution": full_funding_contribution,
        "inflation_rate":            inflation_rate,
        "interest_rate":             interest_rate,
        "components_loaded":         len(components),
        "user_csv_filename":         user_csv_filename,
        "base_p_sa_1_5":             summary["base"]["prob_assessment"]["yr_1_5"],
        "base_p_sa_1_10":            summary["base"]["prob_assessment"]["yr_1_10"],
        "base_p_sa_30":              summary["base"]["prob_assessment"]["yr_30"],
        "full_p_sa_1_5":             summary["full"]["prob_assessment"]["yr_1_5"],
        "full_p_sa_1_10":            summary["full"]["prob_assessment"]["yr_1_10"],
        "full_p_sa_30":              summary["full"]["prob_assessment"]["yr_30"],
        "base_sa_total_p5":          summary["base"]["sa_total"]["p5"],
        "full_sa_total_p5":          summary["full"]["sa_total"]["p5"],
        "base_sa_total_p50":         summary["base"]["sa_total"]["p50"],
        "full_sa_total_p50":         summary["full"]["sa_total"]["p50"],
    }
```

- [ ] **Step 3: Update the email body in `/run`**

In `app.py` inside `run()`, find the `body = (...)` multi-line string that emails the admin. Replace it with:

```python
    body = (
        f"Reserve Study Stress Test — new submission\n\n"
        f"Timestamp:    {timestamp}\n"
        f"Property:     {property_name or '(unspecified)'}\n"
        f"Contact:      {contact_name or '(unspecified)'} <{contact_email or 'no-email'}>\n"
        f"Property Type:{property_type}\n\n"
        f"Inputs:\n"
        f"  {num_units} units, ${starting_reserve:,.0f} starting reserve\n"
        f"  Base contribution:        ${base_contribution:,.0f}/yr\n"
        f"  Full funding contribution:${full_funding_contribution:,.0f}/yr\n"
        f"  Inflation: {inflation_rate:.1%}   Interest: {interest_rate:.1%}\n\n"
        f"Probability of special assessment (1-5yr / 1-10yr / 30yr):\n"
        f"  Base:         {log_payload['base_p_sa_1_5']:.1%} / "
        f"{log_payload['base_p_sa_1_10']:.1%} / {log_payload['base_p_sa_30']:.1%}\n"
        f"  Full funding: {log_payload['full_p_sa_1_5']:.1%} / "
        f"{log_payload['full_p_sa_1_10']:.1%} / {log_payload['full_p_sa_30']:.1%}\n\n"
        f"P5 (worst-5%) total SA over 30yr:\n"
        f"  Base:         ${log_payload['base_sa_total_p5']:,.0f}\n"
        f"  Full funding: ${log_payload['full_sa_total_p5']:,.0f}\n\n"
        f"P50 (typical) total SA over 30yr:\n"
        f"  Base:         ${log_payload['base_sa_total_p50']:,.0f}\n"
        f"  Full funding: ${log_payload['full_sa_total_p50']:,.0f}\n\n"
        f"Components loaded: {len(components)}\n"
        f"Sheet logged: {'yes' if sheets_ok else 'no (not configured or failed)'}\n"
    )
```

- [ ] **Step 4: Replace `/download-csv` for the new shape**

In `app.py`, find `def download_csv():` and replace its entire body with:

```python
@app.route("/download-csv")
def download_csv():
    path = session.get("summary_path")
    if not path or not os.path.exists(path):
        return "No results available. Please run a stress test first.", 404

    with open(path) as f:
        summary = json.load(f)

    rows = []
    for model in ("base", "full"):
        m = summary.get(model, {}) or {}
        prob = m.get("prob_assessment", {})
        sa_t = m.get("sa_total", {})
        sa_pu = m.get("sa_per_unit", {})
        n_yrs = m.get("n_assessment_years", {})
        rows.append({
            "funding_model":                  model,
            "p_sa_yr_1_5":                    round(prob.get("yr_1_5", 0.0), 4),
            "p_sa_yr_1_10":                   round(prob.get("yr_1_10", 0.0), 4),
            "p_sa_yr_30":                     round(prob.get("yr_30", 0.0), 4),
            "sa_total_p5":                    round(sa_t.get("p5", 0.0), 0),
            "sa_total_p25":                   round(sa_t.get("p25", 0.0), 0),
            "sa_total_p50":                   round(sa_t.get("p50", 0.0), 0),
            "sa_per_unit_p5":                 round(sa_pu.get("p5", 0.0), 0),
            "sa_per_unit_p25":                round(sa_pu.get("p25", 0.0), 0),
            "sa_per_unit_p50":                round(sa_pu.get("p50", 0.0), 0),
            "n_assessment_years_p5":          round(n_yrs.get("p5", 0.0), 1),
            "n_assessment_years_p25":         round(n_yrs.get("p25", 0.0), 1),
            "n_assessment_years_p50":         round(n_yrs.get("p50", 0.0), 1),
            "funding_ratio":                  (summary.get("deterioration", {})
                                                       .get("funding_ratio", {})
                                                       .get(model)),
        })

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)

    return send_file(
        io.BytesIO(buf.getvalue().encode()),
        mimetype="text/csv",
        as_attachment=True,
        download_name="stress_test_kpis.csv",
    )
```

- [ ] **Step 5: Smoke check**

```bash
cd /Users/stevenlaidlaw/RFS_Warranty_Project
python3 -c "import app; print('ok')"
python3 -m pytest tests/ -q
```

Expected: `ok` and `26 passed`.

- [ ] **Step 6: Commit**

```bash
git add app.py
git commit -m "feat(app): /run and /download-csv consume new warranty + percentile shape"
```

---

## Task 5: Rewrite `templates/results.html` — dual Y-axis chart + new tables + new warranty card

**Files:**
- Modify: `templates/results.html`

- [ ] **Step 1: Replace the entire content of `templates/results.html`**

Open `/Users/stevenlaidlaw/RFS_Warranty_Project/templates/results.html` and replace its entire content with:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Stress Test Results</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet" />
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
  <style>
    body { background: #f4f6f9; }
    .card { border: none; border-radius: 12px; box-shadow: 0 2px 12px rgba(0,0,0,0.08); }
    .card-header { background: #1a3a5c; color: #fff; border-radius: 12px 12px 0 0 !important; }
    .card-header.gold { background: #b8860b; }
    .btn-primary { background: #1a3a5c; border-color: #1a3a5c; }
    .btn-success { background: #1e6b3c; border-color: #1e6b3c; }
    .kpi-table th { background: #D9E1F2; font-size: 0.85rem; }
    .kpi-table td { font-size: 0.9rem; }
    .scenario-col-base { background: #f0f6fb; }
    .scenario-col-full { background: #e0ecf6; }
    .pct-col { font-size: 0.78rem; font-weight: 500; color: #4a5568; }
    .deterioration-row {
      background: #fef9e7; border-radius: 8px; padding: 0.75rem 1rem;
      display: flex; gap: 2rem; flex-wrap: wrap;
    }
    .premium-adj-note { font-size: 0.72rem; color: #6c757d; }
  </style>
</head>
<body>
<div class="container py-5" style="max-width: 1200px;">

  <div class="d-flex align-items-center justify-content-between mb-4 flex-wrap gap-2">
    <div>
      <h2 class="fw-bold mb-0" style="color:#1a3a5c;">Stress Test Results</h2>
      <p class="text-muted mb-0">
        {{ summary.components_loaded }} components &middot;
        {{ summary.config.num_trials }} Monte Carlo trials &middot;
        {{ summary.config.horizon }} yr horizon
      </p>
    </div>
    <div class="d-flex gap-2">
      <a href="/download-csv" class="btn btn-success">⬇ Download CSV</a>
      <a href="/" class="btn btn-outline-secondary">← New Test</a>
    </div>
  </div>

  <!-- 1. Configuration -->
  <div class="card mb-4">
    <div class="card-header py-2"><h6 class="mb-0">Configuration</h6></div>
    <div class="card-body py-3 px-4">
      <div class="row g-2 text-center">
        <div class="col-md-2 col-6">
          <div class="text-muted small">Units</div>
          <div class="fw-semibold">{{ summary.config.num_units }}</div>
        </div>
        <div class="col-md-2 col-6">
          <div class="text-muted small">Opening Reserve</div>
          <div class="fw-semibold">${{ "{:,.0f}".format(summary.config.starting_reserve) }}</div>
        </div>
        <div class="col-md-2 col-6">
          <div class="text-muted small">Base Contribution</div>
          <div class="fw-semibold">${{ "{:,.0f}".format(summary.config.base_contribution) }}/yr</div>
        </div>
        <div class="col-md-2 col-6">
          <div class="text-muted small">Full Funding</div>
          <div class="fw-semibold">${{ "{:,.0f}".format(summary.config.full_funding_contribution) }}/yr</div>
        </div>
        <div class="col-md-2 col-6">
          <div class="text-muted small">Inflation</div>
          <div class="fw-semibold">{{ "{:.1%}".format(summary.config.inflation_rate) }}</div>
        </div>
        <div class="col-md-2 col-6">
          <div class="text-muted small">Interest</div>
          <div class="fw-semibold">{{ "{:.1%}".format(summary.config.interest_rate) }}</div>
        </div>
      </div>
    </div>
  </div>

  <!-- 2. Forecast Chart -->
  <div class="card mb-4">
    <div class="card-header py-2"><h6 class="mb-0">30-Year Forecast (P50)</h6></div>
    <div class="card-body">
      <canvas id="forecast-chart" style="min-height: 420px;"></canvas>
    </div>
  </div>

  <!-- 3. Property Deterioration -->
  <div class="card mb-4">
    <div class="card-header py-2"><h6 class="mb-0">Property Deterioration</h6></div>
    <div class="card-body py-3">
      <div class="deterioration-row">
        <div>
          <div class="text-muted small">Total annual deterioration</div>
          <div class="fw-semibold fs-5">${{ "{:,.0f}".format(summary.deterioration.total_annual) }}/yr</div>
        </div>
        <div>
          <div class="text-muted small">Per unit</div>
          <div class="fw-semibold fs-5">${{ "{:,.0f}".format(summary.deterioration.per_unit_annual) }}/yr/unit</div>
        </div>
      </div>
    </div>
  </div>

  <!-- 4. Probability of Special Assessment -->
  <div class="card mb-4">
    <div class="card-header py-2"><h6 class="mb-0">Probability of Special Assessment</h6></div>
    <div class="card-body p-0">
      <table class="table table-hover kpi-table mb-0">
        <thead>
          <tr>
            <th>Metric</th>
            <th class="text-end scenario-col-base">Base Case</th>
            <th class="text-end scenario-col-full">Full Funding</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>P(special assessment), years 1–5</td>
            <td class="text-end scenario-col-base">{{ "{:.1%}".format(summary.base.prob_assessment.yr_1_5) }}</td>
            <td class="text-end scenario-col-full">{{ "{:.1%}".format(summary.full.prob_assessment.yr_1_5) }}</td>
          </tr>
          <tr>
            <td>P(special assessment), years 1–10</td>
            <td class="text-end scenario-col-base">{{ "{:.1%}".format(summary.base.prob_assessment.yr_1_10) }}</td>
            <td class="text-end scenario-col-full">{{ "{:.1%}".format(summary.full.prob_assessment.yr_1_10) }}</td>
          </tr>
          <tr>
            <td>P(special assessment), 30 years</td>
            <td class="text-end scenario-col-base">{{ "{:.1%}".format(summary.base.prob_assessment.yr_30) }}</td>
            <td class="text-end scenario-col-full">{{ "{:.1%}".format(summary.full.prob_assessment.yr_30) }}</td>
          </tr>
          <tr>
            <td>Contribution ÷ annual deterioration</td>
            <td class="text-end scenario-col-base">
              {% if summary.deterioration.funding_ratio.base is not none %}
                {{ "{:.1%}".format(summary.deterioration.funding_ratio.base) }}
              {% else %}—{% endif %}
            </td>
            <td class="text-end scenario-col-full">
              {% if summary.deterioration.funding_ratio.full is not none %}
                {{ "{:.1%}".format(summary.deterioration.funding_ratio.full) }}
              {% else %}—{% endif %}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>

  <!-- 5. Special Assessment Distribution -->
  <div class="card mb-4">
    <div class="card-header py-2"><h6 class="mb-0">Special Assessment Distribution</h6></div>
    <div class="card-body p-0">
      <table class="table table-hover kpi-table mb-0">
        <thead>
          <tr>
            <th rowspan="2" style="vertical-align:bottom;">Metric</th>
            <th colspan="3" class="text-center scenario-col-base">Base Case</th>
            <th colspan="3" class="text-center scenario-col-full">Full Funding</th>
          </tr>
          <tr>
            <th class="text-end scenario-col-base pct-col">P5 (worst 5%)</th>
            <th class="text-end scenario-col-base pct-col">P25</th>
            <th class="text-end scenario-col-base pct-col">P50 (typical)</th>
            <th class="text-end scenario-col-full pct-col">P5 (worst 5%)</th>
            <th class="text-end scenario-col-full pct-col">P25</th>
            <th class="text-end scenario-col-full pct-col">P50 (typical)</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>Total special assessment over 30 yr</td>
            <td class="text-end scenario-col-base">${{ "{:,.0f}".format(summary.base.sa_total.p5) }}</td>
            <td class="text-end scenario-col-base">${{ "{:,.0f}".format(summary.base.sa_total.p25) }}</td>
            <td class="text-end scenario-col-base">${{ "{:,.0f}".format(summary.base.sa_total.p50) }}</td>
            <td class="text-end scenario-col-full">${{ "{:,.0f}".format(summary.full.sa_total.p5) }}</td>
            <td class="text-end scenario-col-full">${{ "{:,.0f}".format(summary.full.sa_total.p25) }}</td>
            <td class="text-end scenario-col-full">${{ "{:,.0f}".format(summary.full.sa_total.p50) }}</td>
          </tr>
          <tr>
            <td>Total SA per unit</td>
            <td class="text-end scenario-col-base">${{ "{:,.0f}".format(summary.base.sa_per_unit.p5) }}</td>
            <td class="text-end scenario-col-base">${{ "{:,.0f}".format(summary.base.sa_per_unit.p25) }}</td>
            <td class="text-end scenario-col-base">${{ "{:,.0f}".format(summary.base.sa_per_unit.p50) }}</td>
            <td class="text-end scenario-col-full">${{ "{:,.0f}".format(summary.full.sa_per_unit.p5) }}</td>
            <td class="text-end scenario-col-full">${{ "{:,.0f}".format(summary.full.sa_per_unit.p25) }}</td>
            <td class="text-end scenario-col-full">${{ "{:,.0f}".format(summary.full.sa_per_unit.p50) }}</td>
          </tr>
          <tr>
            <td># assessment years (of 30)</td>
            <td class="text-end scenario-col-base">{{ "{:.1f}".format(summary.base.n_assessment_years.p5) }}</td>
            <td class="text-end scenario-col-base">{{ "{:.1f}".format(summary.base.n_assessment_years.p25) }}</td>
            <td class="text-end scenario-col-base">{{ "{:.1f}".format(summary.base.n_assessment_years.p50) }}</td>
            <td class="text-end scenario-col-full">{{ "{:.1f}".format(summary.full.n_assessment_years.p5) }}</td>
            <td class="text-end scenario-col-full">{{ "{:.1f}".format(summary.full.n_assessment_years.p25) }}</td>
            <td class="text-end scenario-col-full">{{ "{:.1f}".format(summary.full.n_assessment_years.p50) }}</td>
          </tr>
        </tbody>
      </table>
      <div class="px-3 py-2 text-muted" style="font-size:0.75rem;">
        Pn = the n-th-worst percentile across 1000 Monte Carlo trials (P5 means a 5% chance of being this bad or worse).
      </div>
    </div>
  </div>

  <!-- 6. Warranty Risk Analysis -->
  {% if summary.warranty_analysis and (summary.warranty_analysis.base or summary.warranty_analysis.full) %}
  <div class="card mb-4">
    <div class="card-header gold py-2"><h6 class="mb-0 text-white">Warranty Risk Analysis</h6></div>
    <div class="card-body">
      <div class="row g-3">
        {% for scenario in ['base', 'full'] %}
          {% set wa = summary.warranty_analysis[scenario] %}
          {% if wa %}
          <div class="col-md-6">
            <div class="border rounded p-3 h-100 {% if scenario == 'base' %}scenario-col-base{% else %}scenario-col-full{% endif %}">
              <div class="fw-bold mb-2">
                {{ 'Base Case' if scenario == 'base' else 'Full Funding' }}
                <small class="text-muted">
                  · {{ wa.property_type_used }} · Standard ${{ "{:,.0f}".format(wa.standard_price_used) }}
                </small>
              </div>

              <div class="row g-2 text-center mb-2">
                <div class="col-12">
                  <div class="text-muted small">Warranty Premium (one-time)</div>
                  <div class="fw-bold fs-5">
                    ${{ "{:,.0f}".format(wa.target_terms.warranty_premium_displayed) }}
                  </div>
                  {% if wa.verdict == 'moderate' %}
                    <div class="premium-adj-note">
                      Default ${{ "{:,.0f}".format(wa.target_terms.warranty_premium_default) }} × 1.25 (moderate-risk adjustment)
                    </div>
                  {% elif wa.applied_full_funding_discount %}
                    <div class="premium-adj-note">
                      Default ${{ "{:,.0f}".format(wa.target_terms.warranty_premium_default) }} × 0.80 (Full Funding discount)
                    </div>
                  {% endif %}
                </div>

                <div class="col-6">
                  <div class="text-muted small">Coverage (total)</div>
                  <div class="fw-semibold">${{ "{:,.0f}".format(wa.target_terms.coverage_total) }}</div>
                </div>
                <div class="col-6">
                  <div class="text-muted small">Coverage per unit</div>
                  <div class="fw-semibold">${{ "{:,.0f}".format(wa.target_terms.coverage_per_unit) }}</div>
                </div>

                <div class="col-12">
                  <div class="text-muted small">Claim Probability (5 yr)</div>
                  <div class="fw-semibold">{{ "{:.1%}".format(wa.stress_results.claim_probability) }}</div>
                </div>

                <div class="col-6">
                  <div class="text-muted small">Payout per claim</div>
                  <div class="fw-semibold">${{ "{:,.0f}".format(wa.stress_results.payout_per_claim) }}</div>
                </div>
                <div class="col-6">
                  <div class="text-muted small">Payout per unit</div>
                  <div class="fw-semibold">${{ "{:,.0f}".format(wa.stress_results.payout_per_unit) }}</div>
                </div>

                <div class="col-6">
                  <div class="text-muted small">Expected payout</div>
                  <div class="fw-semibold">${{ "{:,.0f}".format(wa.stress_results.expected_payout) }}</div>
                </div>
                <div class="col-6">
                  <div class="text-muted small">Loss ratio</div>
                  <div class="fw-semibold">{{ "{:.0%}".format(wa.loss_ratio_displayed) }}</div>
                </div>
              </div>

              <div class="alert alert-{{ wa.verdict_color }} mb-0 py-2">
                <strong>{{ wa.verdict_label }}</strong>
                <div class="small">{{ wa.verdict_message }}</div>
              </div>
            </div>
          </div>
          {% endif %}
        {% endfor %}
      </div>
      <div class="text-muted small mt-3">
        {{ (summary.warranty_analysis.base or summary.warranty_analysis.full).methodology_note }}
      </div>
    </div>
  </div>
  {% endif %}

</div>

<script>
  const chartData = {{ summary.chart | tojson }};

  new Chart(document.getElementById('forecast-chart'), {
    type: 'bar',
    data: {
      labels: chartData.years.map(y => 'Yr ' + y),
      datasets: [
        {
          type: 'bar',
          label: 'P50 Expenditure',
          data: chartData.p50_outflow,
          backgroundColor: 'rgba(220, 53, 69, 0.7)',
          borderColor: 'rgba(220, 53, 69, 1)',
          borderWidth: 1,
          order: 3,
          yAxisID: 'y'
        },
        {
          type: 'bar',
          label: 'Base Contribution',
          data: chartData.base_contribution_stream,
          backgroundColor: 'rgba(120, 180, 220, 0.7)',
          borderColor: 'rgba(120, 180, 220, 1)',
          borderWidth: 1,
          order: 4,
          yAxisID: 'y'
        },
        {
          type: 'bar',
          label: 'Full Funding Contribution',
          data: chartData.full_contribution_stream,
          backgroundColor: 'rgba(30, 80, 140, 0.7)',
          borderColor: 'rgba(30, 80, 140, 1)',
          borderWidth: 1,
          order: 5,
          yAxisID: 'y'
        },
        {
          type: 'line',
          label: 'P50 Closing Balance (Base)',
          data: chartData.p50_balance_base,
          borderColor: 'rgba(120, 180, 220, 1)',
          backgroundColor: 'rgba(120, 180, 220, 0.1)',
          borderWidth: 2,
          tension: 0.2,
          fill: false,
          order: 1,
          yAxisID: 'y1'
        },
        {
          type: 'line',
          label: 'P50 Closing Balance (Full Funding)',
          data: chartData.p50_balance_full,
          borderColor: 'rgba(30, 80, 140, 1)',
          backgroundColor: 'rgba(30, 80, 140, 0.1)',
          borderWidth: 2,
          tension: 0.2,
          fill: false,
          order: 2,
          yAxisID: 'y1'
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { position: 'bottom' },
        tooltip: {
          callbacks: {
            label: ctx => ctx.dataset.label + ': $' +
              Number(ctx.parsed.y).toLocaleString(undefined, { maximumFractionDigits: 0 })
          }
        }
      },
      scales: {
        x: { title: { display: true, text: 'Year' } },
        y: {
          position: 'left',
          title: { display: true, text: 'Cash Flow ($)' },
          ticks: { callback: v => '$' + Number(v).toLocaleString() }
        },
        y1: {
          position: 'right',
          title: { display: true, text: 'Reserve Balance ($)' },
          ticks: { callback: v => '$' + Number(v).toLocaleString() },
          grid: { drawOnChartArea: false }
        }
      }
    }
  });
</script>

</body>
</html>
```

- [ ] **Step 2: Smoke check by running the dev server**

In one shell, start Flask in the background:

```bash
cd /Users/stevenlaidlaw/RFS_Warranty_Project
python3 app.py > /tmp/flask.log 2>&1 &
FLASK_PID=$!
sleep 2
```

POST a test submission to /run:

```bash
curl -s -o /tmp/results.html -w "%{http_code}\n" \
  -X POST http://127.0.0.1:8081/run \
  -F "reserve_csv=@/Users/stevenlaidlaw/RFS_Warranty_Project/book1_reserve_study.csv" \
  -F "property_name=Smoke Test T5" \
  -F "contact_email=test@example.com" \
  -F "property_type=Apartment" \
  -F "num_units=50" \
  -F "starting_reserve=250000" \
  -F "base_contribution=50000" \
  -F "full_funding_contribution=200000" \
  -F "inflation_rate=0.03" \
  -F "interest_rate=0.025"
```

Expected: `200`.

Grep the response for the new markers:

```bash
grep -c -E "Special Assessment Distribution|Property Deterioration|P5 \(worst 5%\)|Cash Flow|Reserve Balance|Good property to insure|Moderate risk|Not eligible" /tmp/results.html
```

Expected: ≥ 5 matches (chart axis labels + section titles + at least one verdict label).

Then check flask.log for errors:

```bash
grep -E "Traceback|Error|Exception" /tmp/flask.log || echo "no errors"
```

Expected: `no errors`.

Stop the server:

```bash
kill $FLASK_PID 2>/dev/null
sleep 1
```

- [ ] **Step 3: Commit**

```bash
git add templates/results.html
git commit -m "feat(ui): dual Y-axis chart, P5/P25/P50 distribution tables, tier warranty card"
```

---

## Task 6: Final smoke + edge cases

**Files:**
- None (verification only)

- [ ] **Step 1: Run the full test suite**

```bash
cd /Users/stevenlaidlaw/RFS_Warranty_Project
python3 -m pytest tests/ -v
```

Expected: 26 passed (8 csv_parser + 8 stress_test_mc + 10 warranty).

- [ ] **Step 2: Edge case — Moderate-risk verdict in the browser**

Start Flask again:

```bash
python3 app.py > /tmp/flask.log 2>&1 &
FLASK_PID=$!
sleep 2
```

Submit a "moderate risk" setup (low contributions trigger moderate P(SA)). Use 10 units to keep coverage_total small ($5,000) and tune contributions so the LR lands in 0.40-0.80:

```bash
curl -s -o /tmp/results_mod.html -w "%{http_code}\n" \
  -X POST http://127.0.0.1:8081/run \
  -F "reserve_csv=@/Users/stevenlaidlaw/RFS_Warranty_Project/book1_reserve_study.csv" \
  -F "property_name=Moderate Risk Test" \
  -F "contact_email=test@example.com" \
  -F "property_type=Apartment" \
  -F "num_units=10" \
  -F "starting_reserve=50000" \
  -F "base_contribution=5000" \
  -F "full_funding_contribution=100000" \
  -F "inflation_rate=0.03" \
  -F "interest_rate=0.025"
```

Expected: `200`.

Verify both verdict labels appear in the response. Read the file with a tool of your choice and confirm:
- The "Base Case" card shows one of: "Good property to insure", "Moderate risk", or "Not eligible"
- The "Full Funding" card shows the appropriate label
- If the Base card shows "Moderate risk", the premium subtitle says `× 1.25 (moderate-risk adjustment)`
- If the Full card shows "Good property to insure" AND Full Funding is the scenario, the premium subtitle says `× 0.80 (Full Funding discount)`

```bash
grep -E "Good property to insure|Moderate risk|Not eligible|moderate-risk adjustment|Full Funding discount" /tmp/results_mod.html | head -10
```

- [ ] **Step 3: Stop the server**

```bash
kill $FLASK_PID 2>/dev/null
sleep 1
pgrep -fl "python3 app.py" || echo "flask stopped"
```

- [ ] **Step 4: Git status check**

```bash
git status
```

Expected: clean working tree (pre-existing untracked files OK).

- [ ] **Step 5: No commit needed if all steps passed**

If steps 1-4 all passed cleanly, no new commit is needed — Task 6 is verification-only.

---

## Done

All six tasks committed. The app now shows:
1. A dual-Y-axis chart with cash flow bars on the left and reserve balance lines on the right.
2. A Property Deterioration block + a Probability of Special Assessment table + a Special Assessment Distribution table (P5/P25/P50).
3. A revamped Warranty Risk Analysis card with full-coverage payout, three customer-facing tiers (Good/Moderate/Not eligible), an automatic +25% Moderate adjustment, and a -20% Full Funding discount.
