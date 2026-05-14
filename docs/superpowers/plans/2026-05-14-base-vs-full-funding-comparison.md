# Base vs Full Funding Comparison Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refocus the RFS Stress Test app around comparing two funding scenarios (Base vs Full Funding) under a single Monte Carlo run; strip out shock-parameter inputs, percentile KPI tables, and the deterministic projection layer; add a Chart.js forecast and per-model KPI comparison.

**Architecture:** One MC pass (1000 trials, σ=0.30 locked). Each trial samples cost/life multipliers once, then walks the reserve balance forward twice — once per contribution stream (common random numbers for variance reduction). Per-year P50 arrays power a single mixed bar+line chart. The deterministic projection layer (`projection.py`) is deleted; the chart pulls P50 directly from MC outputs. Warranty risk analysis runs twice (once per funding model) and renders side-by-side.

**Tech Stack:** Python 3.11, Flask, NumPy, Chart.js 4.x (CDN), Bootstrap 5.3, pytest 8.x.

**Spec:** `docs/superpowers/specs/2026-05-14-base-vs-full-funding-comparison-design.md`

---

## File Structure

**Modified files** (in order they're touched):

- `stress_test.py` — `run_trial()` rewritten to return dual-balance arrays + apply inflation to outflows. `run_stress_test()` rewritten to aggregate per-model probabilities and per-year P50s; percentile KPI blocks removed. Default constants kept but most are no longer surfaced to the form.
- `app.py` — `/run` parses new form fields (`base_contribution`, `full_funding_contribution`, drops shock params and `contribution_growth`). Removes `projection.py` imports. Calls `calculate_warranty_risk_analysis()` twice. Email + Sheets payload rewritten around two scenarios. `/download-csv` rewritten for new summary shape.
- `templates/index.html` — Variability Parameters section deleted. Contribution row replaced with two dollar inputs. `contribution_growth` field deleted. `num_trials` and `horizon` deleted. Loading-overlay text updated.
- `templates/results.html` — Full rewrite. Four cards: Configuration, Chart.js forecast, KPI comparison table, dual warranty cards. All percentile tables and tabbed projections removed.

**Deleted files:**

- `projection.py` — functions no longer called.

**New files:**

- `tests/test_stress_test_mc.py` — unit tests for new MC behavior.

---

## Task 1: TDD — `run_trial` dual-balance + inflation

**Files:**
- Create: `tests/test_stress_test_mc.py`
- Modify: `stress_test.py` (`run_trial` and `schedule_track`)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_stress_test_mc.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_stress_test_mc.py -v`
Expected: All four tests FAIL with `TypeError` (run_trial signature mismatch) or `KeyError`.

- [ ] **Step 3: Update `schedule_track` to apply inflation**

Modify `stress_test.py` — replace the existing `schedule_track` function with this version (adds `inflation_rate` parameter; each event is multiplied by `(1 + inflation_rate) ** (year_index)`):

```python
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
```

- [ ] **Step 4: Rewrite `run_trial`**

Modify `stress_test.py` — replace the existing `run_trial` function with this version (drops `annual_contribution`, adds `base_contribution`, `full_funding_contribution`, `inflation_rate`, `interest_rate`; returns dual-balance arrays):

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_stress_test_mc.py -v`
Expected: All four tests PASS.

- [ ] **Step 6: Commit**

```bash
git add tests/test_stress_test_mc.py stress_test.py
git commit -m "refactor(mc): dual-balance run_trial with inflation + interest"
```

---

## Task 2: TDD — `run_stress_test` aggregator rewrite

**Files:**
- Modify: `tests/test_stress_test_mc.py` (add aggregator tests)
- Modify: `stress_test.py` (`run_stress_test`)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_stress_test_mc.py`:

```python
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
        assert "total" in m["median_total_assessment"]
        assert "per_unit" in m["median_total_assessment"]
        assert isinstance(m["median_n_assessment_years"], float)

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_stress_test_mc.py -v`
Expected: The three new tests FAIL with `TypeError` (signature mismatch) or `KeyError` on summary keys.

- [ ] **Step 3: Rewrite `run_stress_test`**

Replace the entire `run_stress_test` function in `stress_test.py` with:

```python
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
```

Also delete from `stress_test.py`:
- The `percentile_summary()` function (no longer used)
- The `print_summary()` function (CLI-only; uses removed KPI blocks)
- The `write_outputs()` function (CLI-only; uses removed KPI blocks)
- The `main()` function and `if __name__ == "__main__":` block (CLI path no longer maintained)
- Module-level constants no longer referenced: `NUM_UNITS`, `STARTING_RESERVE`, `ANNUAL_CONTRIBUTION`, `OUTPUT_JSON`, `OUTPUT_CSV` (keep `FORECAST_HORIZON_YEARS`, `NUM_TRIALS`, `LIFE_SHOCK_*`, `COST_SHOCK_*`, `MIN_LIFE_MULTIPLIER`, `MIN_COST_MULTIPLIER` — still used by the sample_* functions).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_stress_test_mc.py -v`
Expected: All seven tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_stress_test_mc.py stress_test.py
git commit -m "refactor(mc): per-model aggregator with P50 chart arrays"
```

---

## Task 3: Delete `projection.py`

**Files:**
- Delete: `projection.py`
- Modify: `app.py` (remove import + projection block)

- [ ] **Step 1: Delete `projection.py`**

```bash
git rm projection.py
```

- [ ] **Step 2: Remove import from `app.py`**

In `app.py`, delete the line:

```python
from projection import build_yearly_schedule, build_financial_projection
```

(Leave the import section otherwise intact. We'll do the larger `/run` rewrite in Task 4 — this step only removes the now-broken import so the app doesn't ImportError at startup.)

- [ ] **Step 3: Smoke check the app still imports**

Run: `python3 -c "import app"`
Expected: Either succeeds, or fails with a NameError pointing at `build_yearly_schedule`/`build_financial_projection` inside `/run` (still referenced — Task 4 fixes this). Either is acceptable here; the goal is just to confirm the import-time path is clean.

- [ ] **Step 4: Commit**

```bash
git add app.py
git commit -m "refactor: delete projection.py (replaced by MC P50 chart arrays)"
```

---

## Task 4: Rewrite `/run` handler in `app.py`

**Files:**
- Modify: `app.py` (`run()` function, roughly lines 374–594)

- [ ] **Step 1: Replace the `/run` route**

In `app.py`, replace the entire `def run():` function (the `@app.route("/run", methods=["POST"])` handler) with:

```python
@app.route("/run", methods=["POST"])
def run():
    reserve_csv = request.files.get("reserve_csv")
    if not reserve_csv or not reserve_csv.filename:
        flash("Please upload a reserve study CSV.")
        return redirect(url_for("index"))

    components = parse_reserve_csv(reserve_csv)
    if not components:
        flash("No valid components found. Check that the CSV has the required columns and at least one budgeted component.")
        return redirect(url_for("index"))

    # Capture the uploaded bytes for email/audit (parse_reserve_csv consumed the stream)
    try:
        reserve_csv.seek(0)
        user_csv_bytes = reserve_csv.read()
        user_csv_filename = reserve_csv.filename or "reserve_study.csv"
    except Exception:
        user_csv_bytes = b""
        user_csv_filename = "reserve_study.csv"

    property_name = (request.form.get("property_name") or "").strip()
    contact_name  = (request.form.get("contact_name")  or "").strip()
    contact_email = (request.form.get("contact_email") or "").strip()

    try:
        num_units                  = int(request.form["num_units"])
        starting_reserve           = float(request.form["starting_reserve"])
        base_contribution          = float(request.form["base_contribution"])
        full_funding_contribution  = float(request.form["full_funding_contribution"])
    except (KeyError, ValueError):
        flash("Please enter valid numbers for all property parameters.")
        return redirect(url_for("index"))

    try:
        inflation_rate = float(request.form.get("inflation_rate", 0.03))
        interest_rate  = float(request.form.get("interest_rate",  0.025))
    except ValueError:
        flash("Please enter valid numbers for inflation and interest rates.")
        return redirect(url_for("index"))

    # Locked: 1000 trials, 30-year horizon, σ=0.30 cost (lognormal) + life (normal),
    # both centered on the engineer's P50 (mean multiplier = 1.0).
    summary = st.run_stress_test(
        components=components,
        num_trials=1000,
        starting_reserve=starting_reserve,
        base_contribution=base_contribution,
        full_funding_contribution=full_funding_contribution,
        inflation_rate=inflation_rate,
        interest_rate=interest_rate,
        num_units=num_units,
        horizon=30,
    )
    summary["components_loaded"] = len(components)

    deterioration = calculate_annual_deterioration(components, num_units)
    det_total = deterioration["total_annual"]
    deterioration["funding_ratio"] = {
        "base": round(base_contribution / det_total, 3) if det_total > 0 else None,
        "full": round(full_funding_contribution / det_total, 3) if det_total > 0 else None,
    }
    summary["deterioration"] = deterioration

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

    # Strip raw_trials before persisting — large, not used by the template
    summary.pop("raw_trials_base", None)
    summary.pop("raw_trials_full", None)

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
    json.dump(summary, tmp)
    tmp.close()
    session["summary_path"] = tmp.name

    # ── Submission logging + email-to-admin (fail-soft) ──
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")

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
        "base_median_total_sa":      summary["base"]["median_total_assessment"]["total"],
        "full_median_total_sa":      summary["full"]["median_total_assessment"]["total"],
    }
    sheets_ok = _log_submission_to_sheets(log_payload)

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
        f"Median total SA over 30yr:\n"
        f"  Base:         ${log_payload['base_median_total_sa']:,.0f}\n"
        f"  Full funding: ${log_payload['full_median_total_sa']:,.0f}\n\n"
        f"Components loaded: {len(components)}\n"
        f"Sheet logged: {'yes' if sheets_ok else 'no (not configured or failed)'}\n"
    )
    attachments = [
        {"filename": user_csv_filename, "data": user_csv_bytes},
        {"filename": "stress_test_results.json",
         "data": json.dumps(summary, indent=2).encode("utf-8")},
    ]
    _email_admin(
        subject=f"[RFS Stress Test] {property_name or 'New submission'}"
                + (f" — {contact_email}" if contact_email else ""),
        body=body,
        attachments=attachments,
    )

    return render_template("results.html", summary=summary)
```

- [ ] **Step 2: Smoke check the app imports**

Run: `python3 -c "import app; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add app.py
git commit -m "feat(app): /run handles base + full funding scenarios"
```

---

## Task 5: Rewrite `/download-csv` for new summary shape

**Files:**
- Modify: `app.py` (`download_csv()` function, roughly lines 597–628)

- [ ] **Step 1: Replace the `/download-csv` route**

In `app.py`, replace the entire `def download_csv():` function with:

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
        med  = m.get("median_total_assessment", {})
        rows.append({
            "funding_model":            model,
            "p_sa_yr_1_5":              round(prob.get("yr_1_5", 0.0), 4),
            "p_sa_yr_1_10":             round(prob.get("yr_1_10", 0.0), 4),
            "p_sa_yr_30":               round(prob.get("yr_30", 0.0), 4),
            "median_total_assessment":  round(med.get("total", 0.0), 0),
            "median_per_unit":          round(med.get("per_unit", 0.0), 0),
            "median_n_assessment_yrs":  round(m.get("median_n_assessment_years", 0.0), 1),
            "funding_ratio":            (summary.get("deterioration", {})
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

Also delete the helper `_kpi_summary_csv_bytes()` from `app.py` (it referenced the old KPI views and is no longer used — the email body no longer attaches it).

- [ ] **Step 2: Smoke check imports**

Run: `python3 -c "import app; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add app.py
git commit -m "feat(app): /download-csv emits per-model KPI rows"
```

---

## Task 6: Rewrite input form (`templates/index.html`)

**Files:**
- Modify: `templates/index.html`

- [ ] **Step 1: Replace the entire body of the form**

Open `templates/index.html` and replace the file's content with:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>RFS Stress Test</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet" />
  <style>
    body { background: #f4f6f9; }
    .card { border: none; border-radius: 12px; box-shadow: 0 2px 12px rgba(0,0,0,0.08); }
    .card-header { background: #1a3a5c; color: #fff; border-radius: 12px 12px 0 0 !important; }
    .section-label { font-size: 0.75rem; font-weight: 600; text-transform: uppercase;
                     letter-spacing: 0.06em; color: #6c757d; margin-bottom: 0.5rem; }
    .btn-primary { background: #1a3a5c; border-color: #1a3a5c; }
    .btn-primary:hover { background: #15304d; border-color: #15304d; }
    #loading-overlay {
      display: none; position: fixed; inset: 0;
      background: rgba(255,255,255,0.88); z-index: 9999;
      flex-direction: column; align-items: center; justify-content: center;
    }
    #loading-overlay .spinner-border { width: 3rem; height: 3rem; color: #1a3a5c; }
    #loading-overlay p { margin-top: 1rem; font-size: 1.1rem; color: #1a3a5c; font-weight: 500; }
    #loading-overlay small { color: #6c757d; }
  </style>
</head>
<body>

<div id="loading-overlay">
  <div class="spinner-border" role="status"></div>
  <p>Running Monte Carlo…</p>
  <small>1,000 trials × 30 years — usually 3–8 seconds.</small>
</div>

<div class="container py-5" style="max-width: 760px;">

  <div class="text-center mb-4">
    <h2 class="fw-bold" style="color:#1a3a5c;">Reserve Fund Study — Stress Test</h2>
    <p class="text-muted">Upload a reserve study CSV and compare Base Case vs. Full Funding contributions.</p>
    <a href="/pricing" class="btn btn-outline-primary btn-sm">View RFS Pricing Calculator →</a>
  </div>

  {% with messages = get_flashed_messages() %}
    {% if messages %}
      {% for msg in messages %}
        <div class="alert alert-danger">{{ msg }}</div>
      {% endfor %}
    {% endif %}
  {% endwith %}

  <div class="card">
    <div class="card-header py-3"><h5 class="mb-0">New Stress Test</h5></div>
    <div class="card-body p-4">

      <form action="/run" method="POST" enctype="multipart/form-data" id="stress-form">

        <!-- Submitter Info -->
        <p class="section-label">Your Information</p>
        <div class="row g-3 mb-4">
          <div class="col-md-12">
            <label class="form-label">Property Name</label>
            <input type="text" class="form-control" name="property_name" placeholder="e.g. Canmore Gardens" />
          </div>
          <div class="col-md-6">
            <label class="form-label">Your Name</label>
            <input type="text" class="form-control" name="contact_name" placeholder="Jane Smith" />
          </div>
          <div class="col-md-6">
            <label class="form-label">Your Email</label>
            <input type="email" class="form-control" name="contact_email" placeholder="you@example.com" />
            <div class="form-text">Used to identify your submission. We'll save your inputs and results.</div>
          </div>
        </div>

        <!-- Reserve Study -->
        <p class="section-label">Reserve Study</p>
        <div class="mb-4">
          <label class="form-label">Component CSV <span class="text-danger">*</span></label>
          <input type="file" class="form-control" name="reserve_csv" accept=".csv" required />
          <div class="form-text">
            Export the component sheet from your reserve study workbook as CSV. Required columns: Component, Replacement Budget, Design Life, Current Age, Replacement Year, Current Replacement Cost, Maintenance Budget, Maintenance Life Cycle, Maintenance Year, Current Maintenance Cost.
            <a href="/download-template" class="ms-1">⬇ Download example template</a>
          </div>
        </div>

        <!-- Property Parameters -->
        <p class="section-label">Property Parameters</p>
        <div class="row g-3 mb-4">
          <div class="col-md-4">
            <label class="form-label">Property Type</label>
            <select class="form-select" name="property_type">
              <option value="Apartment">Apartment</option>
              <option value="Townhouse">Townhouse</option>
              <option value="Commercial">Commercial</option>
              <option value="Bareland">Bareland</option>
            </select>
            <div class="form-text">Used for warranty risk analysis</div>
          </div>
          <div class="col-md-4">
            <label class="form-label">Number of Units <span class="text-danger">*</span></label>
            <input type="number" class="form-control" name="num_units" placeholder="e.g. 24" min="1" step="1" required />
          </div>
          <div class="col-md-4">
            <label class="form-label">Opening Reserve Balance ($) <span class="text-danger">*</span></label>
            <input type="number" class="form-control" name="starting_reserve" placeholder="e.g. 125000" min="0" step="1" required />
          </div>
        </div>

        <!-- Contributions -->
        <p class="section-label">Annual Contributions</p>
        <p class="text-muted small mb-3">
          Enter the two annual contribution levels to compare. Both grow each year by the
          inflation rate. The Monte Carlo runs once and walks the reserve balance forward
          under each contribution stream, so the comparison is apples-to-apples.
        </p>
        <div class="row g-3 mb-4">
          <div class="col-md-6">
            <label class="form-label">Base Case Contribution ($/yr) <span class="text-danger">*</span></label>
            <input type="number" class="form-control" name="base_contribution" placeholder="e.g. 48000" min="0" step="1" required />
          </div>
          <div class="col-md-6">
            <label class="form-label">Full Funding Contribution ($/yr) <span class="text-danger">*</span></label>
            <input type="number" class="form-control" name="full_funding_contribution" placeholder="e.g. 120000" min="0" step="1" required />
          </div>
        </div>

        <!-- Financial Parameters -->
        <p class="section-label">Financial Parameters</p>
        <div class="row g-3 mb-4">
          <div class="col-md-6">
            <label class="form-label">Inflation Rate</label>
            <input type="number" class="form-control" name="inflation_rate" value="0.03" min="0" max="0.20" step="0.005" />
            <div class="form-text">Applies to both component costs and contribution amounts (e.g. 0.03 = 3%).</div>
          </div>
          <div class="col-md-6">
            <label class="form-label">Return on Savings</label>
            <input type="number" class="form-control" name="interest_rate" value="0.025" min="0" max="0.20" step="0.005" />
            <div class="form-text">Interest earned on the reserve balance (e.g. 0.025 = 2.5%).</div>
          </div>
        </div>

        <div class="d-grid">
          <button type="submit" class="btn btn-primary btn-lg">Run Monte Carlo</button>
        </div>

      </form>
    </div>
  </div>
</div>

<script>
  document.getElementById('stress-form').addEventListener('submit', function () {
    document.getElementById('loading-overlay').style.display = 'flex';
  });
</script>

</body>
</html>
```

- [ ] **Step 2: Commit**

```bash
git add templates/index.html
git commit -m "feat(ui): replace shock-param form with base/full funding inputs"
```

---

## Task 7: Rewrite results page with Chart.js + dual layout

**Files:**
- Modify: `templates/results.html`

- [ ] **Step 1: Replace the entire results template**

Open `templates/results.html` and replace its content with:

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
      <canvas id="forecast-chart" height="100"></canvas>
    </div>
  </div>

  <!-- 3. KPI Comparison -->
  <div class="card mb-4">
    <div class="card-header py-2"><h6 class="mb-0">KPI Comparison</h6></div>
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
            <td>Median total special assessment (30 yr)</td>
            <td class="text-end scenario-col-base">${{ "{:,.0f}".format(summary.base.median_total_assessment.total) }}</td>
            <td class="text-end scenario-col-full">${{ "{:,.0f}".format(summary.full.median_total_assessment.total) }}</td>
          </tr>
          <tr>
            <td>Median total SA per unit (30 yr)</td>
            <td class="text-end scenario-col-base">${{ "{:,.0f}".format(summary.base.median_total_assessment.per_unit) }}</td>
            <td class="text-end scenario-col-full">${{ "{:,.0f}".format(summary.full.median_total_assessment.per_unit) }}</td>
          </tr>
          <tr>
            <td>Median # of assessment years (of 30)</td>
            <td class="text-end scenario-col-base">{{ "{:.1f}".format(summary.base.median_n_assessment_years) }}</td>
            <td class="text-end scenario-col-full">{{ "{:.1f}".format(summary.full.median_n_assessment_years) }}</td>
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

  <!-- 4. Warranty Risk Analysis -->
  {% if summary.warranty_analysis and (summary.warranty_analysis.base or summary.warranty_analysis.full) %}
  <div class="card mb-4">
    <div class="card-header gold py-2"><h6 class="mb-0 text-white">Warranty Risk Analysis</h6></div>
    <div class="card-body">
      <div class="row g-3">
        {% for scenario in ['base', 'full'] %}
          {% set wa = summary.warranty_analysis[scenario] %}
          {% if wa %}
          {% set verdict_color = {'highly_profitable':'success','on_target':'success','marginal':'warning','underpriced':'danger'}[wa.verdict] %}
          <div class="col-md-6">
            <div class="border rounded p-3 h-100 {% if scenario == 'base' %}scenario-col-base{% else %}scenario-col-full{% endif %}">
              <div class="fw-bold mb-2">
                {{ 'Base Case' if scenario == 'base' else 'Full Funding' }}
                <small class="text-muted">
                  · {{ wa.property_type_used }} · Standard ${{ "{:,.0f}".format(wa.standard_price_used) }}
                </small>
              </div>
              <div class="row g-2 text-center mb-2">
                <div class="col-6">
                  <div class="text-muted small">Premium (one-time)</div>
                  <div class="fw-semibold">${{ "{:,.0f}".format(wa.target_terms.warranty_premium) }}</div>
                </div>
                <div class="col-6">
                  <div class="text-muted small">Coverage / Deductible</div>
                  <div class="fw-semibold">${{ "{:,.0f}".format(wa.target_terms.coverage_total) }} / ${{ "{:,.0f}".format(wa.target_terms.deductible) }}</div>
                </div>
                <div class="col-6">
                  <div class="text-muted small">Claim Probability (5 yr)</div>
                  <div class="fw-semibold">{{ "{:.1%}".format(wa.stress_results.claim_probability) }}</div>
                </div>
                <div class="col-6">
                  <div class="text-muted small">Expected Payout</div>
                  <div class="fw-semibold">${{ "{:,.0f}".format(wa.stress_results.expected_payout) }}</div>
                </div>
                <div class="col-6">
                  <div class="text-muted small">Loss Ratio</div>
                  <div class="fw-semibold">{{ "{:.0%}".format(wa.actual_loss_ratio) }}</div>
                </div>
                <div class="col-6">
                  <div class="text-muted small">Verdict</div>
                  <div class="fw-semibold text-{{ verdict_color }}">{{ wa.verdict.replace('_', ' ') }}</div>
                </div>
              </div>
              <div class="alert alert-{{ wa.eligibility.color }} mb-0 py-2">
                <strong>{{ wa.eligibility.label }}</strong>
                <div class="small">{{ wa.eligibility.reason }}</div>
                {% if wa.custom_quote %}
                <hr class="my-2"/>
                <div class="small">
                  Required premium for {{ "{:.0%}".format(wa.target_terms.target_loss_ratio) }} loss ratio:
                  <strong>${{ "{:,.0f}".format(wa.custom_quote.required_premium) }}</strong>
                  ({{ wa.custom_quote.required_premium_tier_mult }}× Standard)
                </div>
                {% endif %}
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
          order: 3
        },
        {
          type: 'bar',
          label: 'Base Contribution',
          data: chartData.base_contribution_stream,
          backgroundColor: 'rgba(120, 180, 220, 0.7)',
          borderColor: 'rgba(120, 180, 220, 1)',
          borderWidth: 1,
          order: 4
        },
        {
          type: 'bar',
          label: 'Full Funding Contribution',
          data: chartData.full_contribution_stream,
          backgroundColor: 'rgba(30, 80, 140, 0.7)',
          borderColor: 'rgba(30, 80, 140, 1)',
          borderWidth: 1,
          order: 5
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
          order: 1
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
          order: 2
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
          title: { display: true, text: 'Dollars' },
          ticks: { callback: v => '$' + Number(v).toLocaleString() }
        }
      }
    }
  });
</script>

</body>
</html>
```

- [ ] **Step 2: Start the dev server**

Run: `python3 app.py` (in a separate terminal; leaves on port 8081)

- [ ] **Step 3: Smoke test in the browser**

Open `http://127.0.0.1:8081/`, upload `book1_reserve_study.csv` (already in repo), fill in:
- Property Name: `Smoke Test`
- Email: anything
- Property Type: Apartment
- Num Units: 50
- Opening Reserve: 250000
- Base Contribution: 50000
- Full Funding Contribution: 200000
- Inflation: 0.03
- Interest: 0.025

Click "Run Monte Carlo".

Expected:
- Loading overlay appears briefly (~3–8s)
- Results page renders with all four cards
- Chart displays three bar series and two line series
- KPI comparison shows Full Funding has lower P(SA) than Base
- Both warranty cards render (or one with the same content twice if Full Funding fully eliminates claims)

If any card is missing or broken: check `python3 app.py` output for tracebacks; check browser DevTools console for Chart.js errors. Fix issues before committing.

- [ ] **Step 4: Commit**

```bash
git add templates/results.html
git commit -m "feat(ui): Chart.js forecast + dual-scenario KPI/warranty layout"
```

---

## Task 8: Final smoke + clean shutdown

**Files:**
- None (verification only)

- [ ] **Step 1: Re-run the test suite end-to-end**

Run: `python3 -m pytest tests/ -v`
Expected: All tests pass — both `test_csv_parser.py` (unchanged) and `test_stress_test_mc.py` (new).

- [ ] **Step 2: Manual edge case in the browser**

Same setup as Task 7 Step 3, but with **base_contribution = 0** and **full_funding_contribution = 500000**.

Expected:
- Base Case shows P(SA in 1-5yr) ≈ 100% (no contributions → SA almost certain on any cost event)
- Full Funding shows near 0% across all windows
- Chart line for Base balance is flat near 0; Full Funding line climbs
- Warranty card for Base shows verdict "underpriced" or eligibility "decline"
- Warranty card for Full Funding shows "highly profitable" or "on target"

If the page renders, the redesign is functioning end-to-end.

- [ ] **Step 3: Final commit if any cleanup was needed**

If Step 2 surfaced a regression and you made a fix, commit it. Otherwise, no commit.

```bash
git status   # expect clean
```

---

## Done

All eight tasks committed. The app now compares Base vs Full Funding under one Monte Carlo pass, displays a single P50 forecast chart, and runs warranty analysis side-by-side for both scenarios.
