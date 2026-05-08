# RFS Stress Test Web App Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Flask web app that accepts a reserve study CSV, runs a Monte Carlo stress test with user-configurable shock parameters, and displays a KPI dashboard showing special assessment distributions.

**Architecture:** The existing `rfs_calculator.py` and `stress_test.py` are the engine — they stay unchanged except for a minimal refactor to `stress_test.py` that promotes module-level shock constants to function arguments. A new `csv_parser.py` converts the uploaded CSV to the component dict format the engine expects. `app.py` wires everything together with three routes: form, run, and CSV download.

**Tech Stack:** Python 3, Flask, NumPy, Bootstrap 5

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `stress_test.py` | Modify | Accept shock params as kwargs instead of reading module constants |
| `csv_parser.py` | Create | Parse 2-Component CSV → component dict list |
| `app.py` | Create | Flask routes: `/` (form), `/run` (POST), `/download-csv` |
| `templates/index.html` | Create | Upload form + parameter inputs |
| `templates/results.html` | Create | KPI dashboard |
| `requirements.txt` | Create | Dependency list |
| `tests/__init__.py` | Create | Test package marker |
| `tests/test_csv_parser.py` | Create | Unit tests for `parse_reserve_csv()` |

---

### Task 1: Refactor stress_test.py — shock params as kwargs

**Files:**
- Modify: `stress_test.py` lines 82–99 and 238–290

- [ ] **Step 1.1: Update sample_life_multipliers() to accept shock params**

Replace lines 82–88:

```python
def sample_life_multipliers(num_trials: int,
                            num_components: int,
                            rng: np.random.Generator,
                            life_shock_mean: float = LIFE_SHOCK_MEAN,
                            life_shock_sigma: float = LIFE_SHOCK_SIGMA) -> np.ndarray:
    """Per-trial, per-component life multiplier from Normal(mean, sigma)."""
    samples = rng.normal(life_shock_mean, life_shock_sigma,
                         size=(num_trials, num_components))
    return np.maximum(MIN_LIFE_MULTIPLIER, samples)
```

- [ ] **Step 1.2: Update sample_cost_multipliers() to accept shock params**

Replace lines 91–99:

```python
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
```

- [ ] **Step 1.3: Update run_stress_test() signature and body**

Replace the `run_stress_test` signature and the three lines that call the samplers and build the config dict.

New signature (lines 238–244):

```python
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
```

Update sampler calls (lines 249–250) to thread the shock params:

```python
    life_mults_all = sample_life_multipliers(num_trials, 2 * n, rng,
                                             life_shock_mean, life_shock_sigma)
    cost_mults_all = sample_cost_multipliers(num_trials, 2 * n, rng,
                                             cost_shock_mean, cost_shock_sigma)
```

Update config dict (lines 286–289) to use the local variable names instead of module constants:

```python
            "life_shock_mean": life_shock_mean,
            "life_shock_sigma": life_shock_sigma,
            "cost_shock_mean": cost_shock_mean,
            "cost_shock_sigma": cost_shock_sigma,
```

- [ ] **Step 1.4: Verify the existing main() still runs correctly**

```bash
cd /Users/stevenlaidlaw/RFS_Warranty_Project
python3 stress_test.py 2>&1 | head -20
```

Expected: same summary output as before (engine still works with defaults).

- [ ] **Step 1.5: Commit**

```bash
git add stress_test.py
git commit -m "refactor: accept shock params as kwargs in stress_test functions"
```

---

### Task 2: Write failing tests for parse_reserve_csv()

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/test_csv_parser.py`

- [ ] **Step 2.1: Create the test package marker**

```bash
touch /Users/stevenlaidlaw/RFS_Warranty_Project/tests/__init__.py
```

- [ ] **Step 2.2: Write the failing tests**

Create `tests/test_csv_parser.py`:

```python
import io
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from csv_parser import parse_reserve_csv

HEADERS = (
    "Component,Replacement Budget,Design Life,Current Age,Replacement Year,"
    "Current Replacement Cost,Maintenance Budget,Maintenance Life Cycle,"
    "Remaining Maintenance Life,Maintenance Year,Current Maintenance Cost\n"
)


def _csv(rows: str) -> io.BytesIO:
    buf = io.BytesIO((HEADERS + rows).encode("utf-8"))
    buf.name = "test.csv"
    return buf


def test_replacement_only_component():
    result = parse_reserve_csv(_csv(
        "Roof - Asphalt Shingle,Yes,25,1,25,16500,No,,,,\n"
    ))
    assert len(result) == 1
    r = result[0]
    assert r["Property Components"] == "Roof - Asphalt Shingle"
    assert r["Replacement Desi"] == 25.0
    assert r["Replacement Curr"] == 1.0
    assert r["Replacement Year"] == 25.0
    assert r["Replacement Cost"] == 16500.0
    assert r["Maintenance Desi"] == 0.0
    assert r["Maintenance Cost"] == 0.0


def test_maintenance_only_component():
    result = parse_reserve_csv(_csv(
        "Exterior Painting,No,,,,,Yes,8,7,2,12000\n"
    ))
    assert len(result) == 1
    r = result[0]
    assert r["Maintenance Desi"] == 8.0
    assert r["Maintenance Year"] == 2.0
    assert r["Maintenance Cost"] == 12000.0
    assert r["Replacement Cost"] == 0.0
    assert r["Replacement Desi"] == 0.0


def test_both_tracks_active():
    result = parse_reserve_csv(_csv(
        "Fence - Wood,Yes,20,3,18,4200,Yes,5,3,3,2000\n"
    ))
    assert len(result) == 1
    r = result[0]
    assert r["Replacement Cost"] == 4200.0
    assert r["Replacement Desi"] == 20.0
    assert r["Maintenance Cost"] == 2000.0
    assert r["Maintenance Desi"] == 5.0


def test_both_no_budget_dropped():
    result = parse_reserve_csv(_csv(
        "Foundation,No,,,,,No,,,,\n"
    ))
    assert result == []


def test_empty_file_returns_empty():
    result = parse_reserve_csv(io.BytesIO(b""))
    assert result == []


def test_missing_required_columns_returns_empty():
    buf = io.BytesIO(b"Component,Design Life\nRoof,25\n")
    buf.name = "test.csv"
    result = parse_reserve_csv(buf)
    assert result == []


def test_column_names_case_insensitive_and_whitespace():
    headers = (
        " COMPONENT , REPLACEMENT BUDGET , DESIGN LIFE , CURRENT AGE ,"
        " REPLACEMENT YEAR , CURRENT REPLACEMENT COST , MAINTENANCE BUDGET ,"
        " MAINTENANCE LIFE CYCLE , REMAINING MAINTENANCE LIFE ,"
        " MAINTENANCE YEAR , CURRENT MAINTENANCE COST \n"
    )
    buf = io.BytesIO((headers + "Asphalt,Yes,25,6,20,112000,Yes,9,3,4,56000\n").encode())
    buf.name = "test.csv"
    result = parse_reserve_csv(buf)
    assert len(result) == 1
    assert result[0]["Replacement Desi"] == 25.0
    assert result[0]["Maintenance Desi"] == 9.0


def test_multiple_components_correct_count():
    result = parse_reserve_csv(_csv(
        "Roof,Yes,25,1,25,16500,No,,,,\n"
        "Fence,Yes,20,3,18,4200,Yes,5,3,3,2000\n"
        "Foundation,No,,,,,No,,,,\n"
    ))
    assert len(result) == 2
    assert result[0]["Property Components"] == "Roof"
    assert result[1]["Property Components"] == "Fence"
```

- [ ] **Step 2.3: Run tests — expect ImportError**

```bash
cd /Users/stevenlaidlaw/RFS_Warranty_Project
python3 -m pytest tests/test_csv_parser.py -v 2>&1 | head -15
```

Expected: `ImportError: cannot import name 'parse_reserve_csv' from 'csv_parser'` (file doesn't exist yet).

---

### Task 3: Implement csv_parser.py

**Files:**
- Create: `csv_parser.py`

- [ ] **Step 3.1: Create csv_parser.py**

```python
import csv
import io
from typing import Any, Dict, List


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        return float(str(val).strip().replace(",", "") or 0)
    except (ValueError, TypeError):
        return default


def parse_reserve_csv(file_obj) -> List[Dict[str, Any]]:
    """
    Parse a 2-Component CSV export and return component dicts matching
    the reserve_study_components.json schema used by stress_test.py.
    Returns [] on any error or if no valid components are found.

    Required CSV columns (case-insensitive, whitespace-trimmed):
      Component, Replacement Budget, Design Life, Current Age,
      Replacement Year, Current Replacement Cost,
      Maintenance Budget, Maintenance Life Cycle,
      Remaining Maintenance Life, Maintenance Year,
      Current Maintenance Cost
    """
    try:
        file_obj.seek(0)
        text = file_obj.read().decode("utf-8-sig")
        if not text.strip():
            return []

        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            return []

        reader.fieldnames = [f.lower().strip() for f in reader.fieldnames]

        required = {"component", "replacement budget", "maintenance budget"}
        if not required.issubset(set(reader.fieldnames)):
            return []

        components = []
        for row in reader:
            name = row.get("component", "").strip()
            if not name:
                continue

            rep_budget = row.get("replacement budget", "").strip().lower() == "yes"
            mnt_budget = row.get("maintenance budget", "").strip().lower() == "yes"

            rep_desi = _safe_float(row.get("design life")) if rep_budget else 0.0
            rep_curr = _safe_float(row.get("current age")) if rep_budget else 0.0
            rep_year = _safe_float(row.get("replacement year")) if rep_budget else 0.0
            rep_cost = _safe_float(row.get("current replacement cost")) if rep_budget else 0.0

            mnt_desi = _safe_float(row.get("maintenance life cycle")) if mnt_budget else 0.0
            mnt_curr = _safe_float(row.get("remaining maintenance life")) if mnt_budget else 0.0
            mnt_year = _safe_float(row.get("maintenance year")) if mnt_budget else 0.0
            mnt_cost = _safe_float(row.get("current maintenance cost")) if mnt_budget else 0.0

            if not (rep_desi or rep_cost or mnt_desi or mnt_cost):
                continue

            components.append({
                "Property Components": name,
                "Replacement Curr":    rep_curr,
                "Replacement Desi":    rep_desi,
                "Replacement Year":    rep_year,
                "Replacement Cost":    rep_cost,
                "Maintenance Curr":    mnt_curr,
                "Maintenance Desi":    mnt_desi,
                "Maintenance Year":    mnt_year,
                "Maintenance Cost":    mnt_cost,
            })

        return components

    except Exception:
        return []
```

- [ ] **Step 3.2: Run tests — expect all 8 to pass**

```bash
python3 -m pytest tests/test_csv_parser.py -v
```

Expected:
```
tests/test_csv_parser.py::test_replacement_only_component PASSED
tests/test_csv_parser.py::test_maintenance_only_component PASSED
tests/test_csv_parser.py::test_both_tracks_active PASSED
tests/test_csv_parser.py::test_both_no_budget_dropped PASSED
tests/test_csv_parser.py::test_empty_file_returns_empty PASSED
tests/test_csv_parser.py::test_missing_required_columns_returns_empty PASSED
tests/test_csv_parser.py::test_column_names_case_insensitive_and_whitespace PASSED
tests/test_csv_parser.py::test_multiple_components_correct_count PASSED

8 passed
```

- [ ] **Step 3.3: Commit**

```bash
git add csv_parser.py tests/__init__.py tests/test_csv_parser.py
git commit -m "feat: add csv_parser with unit tests"
```

---

### Task 4: Create requirements.txt and app.py

**Files:**
- Create: `requirements.txt`
- Create: `app.py`

- [ ] **Step 4.1: Create requirements.txt**

```
flask
python-dotenv
numpy
```

- [ ] **Step 4.2: Create app.py**

```python
import csv
import io
import json
import os
import tempfile

from flask import (Flask, flash, redirect, render_template,
                   request, send_file, session, url_for)
from dotenv import load_dotenv

load_dotenv()

import stress_test as st
from csv_parser import parse_reserve_csv

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "change-this-in-production")
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB


@app.route("/")
def index():
    return render_template("index.html")


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

    try:
        num_units           = int(request.form["num_units"])
        starting_reserve    = float(request.form["starting_reserve"])
        annual_contribution = float(request.form["annual_contribution"])
    except (KeyError, ValueError):
        flash("Please enter valid numbers for all property parameters.")
        return redirect(url_for("index"))

    try:
        life_shock_mean  = float(request.form.get("life_shock_mean",  0.80))
        life_shock_sigma = float(request.form.get("life_shock_sigma", 0.10))
        cost_shock_mean  = float(request.form.get("cost_shock_mean",  1.30))
        cost_shock_sigma = float(request.form.get("cost_shock_sigma", 0.15))
        num_trials       = int(request.form.get("num_trials",         2000))
        horizon          = int(request.form.get("horizon",            30))
    except ValueError:
        flash("Please enter valid numbers for all shock parameters.")
        return redirect(url_for("index"))

    summary = st.run_stress_test(
        components=components,
        num_trials=num_trials,
        starting_reserve=starting_reserve,
        annual_contribution=annual_contribution,
        num_units=num_units,
        horizon=horizon,
        life_shock_mean=life_shock_mean,
        life_shock_sigma=life_shock_sigma,
        cost_shock_mean=cost_shock_mean,
        cost_shock_sigma=cost_shock_sigma,
    )
    summary["components_loaded"] = len(components)

    # Persist for download — write to tempfile, store path in session
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
    json.dump(summary, tmp)
    tmp.close()
    session["summary_path"] = tmp.name

    return render_template("results.html", summary=summary)


@app.route("/download-csv")
def download_csv():
    path = session.get("summary_path")
    if not path or not os.path.exists(path):
        return "No results available. Please run a stress test first.", 404

    with open(path) as f:
        summary = json.load(f)

    rows = []
    for view in ("kpis_total", "kpis_per_unit"):
        for kpi, stats in summary[view].items():
            rows.append({
                "view": view,
                "kpi": kpi,
                **{k: round(v, 2) for k, v in stats.items()},
            })

    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=["view", "kpi", "mean", "p25", "p50", "p75", "p90", "p95"],
    )
    writer.writeheader()
    writer.writerows(rows)

    return send_file(
        io.BytesIO(buf.getvalue().encode()),
        mimetype="text/csv",
        as_attachment=True,
        download_name="stress_test_kpis.csv",
    )


if __name__ == "__main__":
    app.run(debug=True, port=8081)
```

- [ ] **Step 4.3: Verify app imports cleanly**

```bash
cd /Users/stevenlaidlaw/RFS_Warranty_Project
python3 -c "import app; print('OK')"
```

Expected: `OK`

- [ ] **Step 4.4: Commit**

```bash
git add requirements.txt app.py
git commit -m "feat: add Flask app with /run and /download-csv routes"
```

---

### Task 5: Create templates/index.html

**Files:**
- Create: `templates/index.html`

- [ ] **Step 5.1: Create the form template**

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
  <p>Running stress test…</p>
  <small>2,000 trials × 30 years — usually 5–15 seconds.</small>
</div>

<div class="container py-5" style="max-width: 760px;">

  <div class="text-center mb-4">
    <h2 class="fw-bold" style="color:#1a3a5c;">Reserve Fund Study — Stress Test</h2>
    <p class="text-muted">Upload a reserve study CSV and configure shock parameters to model special assessment risk.</p>
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

        <!-- Reserve Study -->
        <p class="section-label">Reserve Study</p>
        <div class="mb-4">
          <label class="form-label">Component CSV <span class="text-danger">*</span></label>
          <input type="file" class="form-control" name="reserve_csv" accept=".csv" required />
          <div class="form-text">Export the component sheet from your reserve study workbook as CSV. Required columns: Component, Replacement Budget, Design Life, Current Age, Replacement Year, Current Replacement Cost, Maintenance Budget, Maintenance Life Cycle, Maintenance Year, Current Maintenance Cost.</div>
        </div>

        <!-- Property Parameters -->
        <p class="section-label">Property Parameters</p>
        <div class="row g-3 mb-4">
          <div class="col-md-4">
            <label class="form-label">Number of Units <span class="text-danger">*</span></label>
            <input type="number" class="form-control" name="num_units" placeholder="e.g. 24" min="1" step="1" required />
          </div>
          <div class="col-md-4">
            <label class="form-label">Opening Reserve Balance ($) <span class="text-danger">*</span></label>
            <input type="number" class="form-control" name="starting_reserve" placeholder="e.g. 125000" min="0" step="1" required />
          </div>
          <div class="col-md-4">
            <label class="form-label">Annual Contribution ($) <span class="text-danger">*</span></label>
            <input type="number" class="form-control" name="annual_contribution" placeholder="e.g. 48000" min="0" step="1" required />
          </div>
        </div>

        <!-- Shock Parameters -->
        <p class="section-label">Shock Parameters</p>
        <p class="text-muted small mb-3">Life multiplier &lt;1 shortens design life; cost multiplier &gt;1 increases costs. Defaults model a moderate stress scenario.</p>

        <div class="row g-3 mb-3">
          <div class="col-md-3">
            <label class="form-label">Life Shock Mean</label>
            <input type="number" class="form-control" name="life_shock_mean" value="0.80" min="0.1" max="2.0" step="0.01" />
            <div class="form-text">e.g. 0.80 = 20% shorter life</div>
          </div>
          <div class="col-md-3">
            <label class="form-label">Life Shock Std Dev</label>
            <input type="number" class="form-control" name="life_shock_sigma" value="0.10" min="0.01" max="1.0" step="0.01" />
          </div>
          <div class="col-md-3">
            <label class="form-label">Cost Shock Mean</label>
            <input type="number" class="form-control" name="cost_shock_mean" value="1.30" min="0.5" max="5.0" step="0.01" />
            <div class="form-text">e.g. 1.30 = 30% cost increase</div>
          </div>
          <div class="col-md-3">
            <label class="form-label">Cost Shock Std Dev</label>
            <input type="number" class="form-control" name="cost_shock_sigma" value="0.15" min="0.01" max="1.0" step="0.01" />
          </div>
        </div>

        <div class="row g-3 mb-4">
          <div class="col-md-4">
            <label class="form-label">Number of Trials</label>
            <input type="number" class="form-control" name="num_trials" value="2000" min="100" max="10000" step="100" />
          </div>
          <div class="col-md-4">
            <label class="form-label">Forecast Horizon (years)</label>
            <input type="number" class="form-control" name="horizon" value="30" min="10" max="50" step="1" />
          </div>
        </div>

        <div class="d-grid">
          <button type="submit" class="btn btn-primary btn-lg">Run Stress Test</button>
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

- [ ] **Step 5.2: Quick render check**

```bash
python3 app.py &
sleep 2
curl -s http://localhost:8081 | grep -c "stress-form"
kill %1
```

Expected: `1`

---

### Task 6: Create templates/results.html

**Files:**
- Create: `templates/results.html`

- [ ] **Step 6.1: Create the KPI dashboard template**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Stress Test Results</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet" />
  <style>
    body { background: #f4f6f9; }
    .card { border: none; border-radius: 12px; box-shadow: 0 2px 12px rgba(0,0,0,0.08); }
    .card-header { background: #1a3a5c; color: #fff; border-radius: 12px 12px 0 0 !important; }
    .card-header.green { background: #1e6b3c; }
    .btn-primary { background: #1a3a5c; border-color: #1a3a5c; }
    .btn-success { background: #1e6b3c; border-color: #1e6b3c; }
    .kpi-table th { background: #D9E1F2; font-size: 0.85rem; }
    .kpi-table td { font-size: 0.9rem; }
    .kpi-label { font-weight: 500; }
  </style>
</head>
<body>
<div class="container py-5" style="max-width: 1100px;">

  <div class="d-flex align-items-center justify-content-between mb-4 flex-wrap gap-2">
    <div>
      <h2 class="fw-bold mb-0" style="color:#1a3a5c;">Stress Test Results</h2>
      <p class="text-muted mb-0">{{ summary.components_loaded }} components · {{ summary.config.num_trials | int }} trials · {{ summary.config.forecast_horizon_years }} yr horizon</p>
    </div>
    <div class="d-flex gap-2">
      <a href="/download-csv" class="btn btn-success">⬇ Download CSV</a>
      <a href="/" class="btn btn-outline-secondary">← New Test</a>
    </div>
  </div>

  <!-- Configuration Summary -->
  <div class="card mb-4">
    <div class="card-header py-2"><h6 class="mb-0">Configuration</h6></div>
    <div class="card-body py-2 px-4">
      <div class="row g-2 text-center">
        <div class="col-md-2 col-6">
          <div class="text-muted small">Units</div>
          <div class="fw-semibold">{{ summary.config.num_units }}</div>
        </div>
        <div class="col-md-2 col-6">
          <div class="text-muted small">Opening Reserve</div>
          <div class="fw-semibold">${{ "{:,.0f}".format(summary.config.starting_reserve_total) }}</div>
        </div>
        <div class="col-md-2 col-6">
          <div class="text-muted small">Annual Contribution</div>
          <div class="fw-semibold">${{ "{:,.0f}".format(summary.config.annual_contribution_total) }}</div>
        </div>
        <div class="col-md-2 col-6">
          <div class="text-muted small">Life Shock</div>
          <div class="fw-semibold">×{{ summary.config.life_shock_mean }} (σ {{ summary.config.life_shock_sigma }})</div>
        </div>
        <div class="col-md-2 col-6">
          <div class="text-muted small">Cost Shock</div>
          <div class="fw-semibold">×{{ summary.config.cost_shock_mean }} (σ {{ summary.config.cost_shock_sigma }})</div>
        </div>
        <div class="col-md-2 col-6">
          <div class="text-muted small">Reserve / Unit</div>
          <div class="fw-semibold">${{ "{:,.0f}".format(summary.config.starting_reserve_per_unit) }}</div>
        </div>
      </div>
    </div>
  </div>

  {% set kpi_labels = {
    "assessment_yr_1_5":    "Special Assessment — Years 1–5",
    "assessment_yr_6_10":   "Special Assessment — Years 6–10",
    "assessment_yr_1_10":   "Special Assessment — Years 1–10",
    "assessment_30yr_total":"Special Assessment — 30yr Total"
  } %}

  <!-- KPI Tables -->
  <div class="row g-4 mb-4">

    <!-- Total $ -->
    <div class="col-lg-6">
      <div class="card h-100">
        <div class="card-header py-2"><h6 class="mb-0">Special Assessments — Total ($)</h6></div>
        <div class="card-body p-0">
          <table class="table table-sm table-hover mb-0 kpi-table">
            <thead>
              <tr>
                <th>KPI</th><th class="text-end">Mean</th><th class="text-end">P25</th>
                <th class="text-end">P50</th><th class="text-end">P75</th>
                <th class="text-end">P90</th><th class="text-end">P95</th>
              </tr>
            </thead>
            <tbody>
              {% for key, label in kpi_labels.items() %}
                {% if key in summary.kpis_total %}
                {% set s = summary.kpis_total[key] %}
                <tr>
                  <td class="kpi-label">{{ label }}</td>
                  <td class="text-end">${{ "{:,.0f}".format(s.mean) }}</td>
                  <td class="text-end">${{ "{:,.0f}".format(s.p25) }}</td>
                  <td class="text-end">${{ "{:,.0f}".format(s.p50) }}</td>
                  <td class="text-end">${{ "{:,.0f}".format(s.p75) }}</td>
                  <td class="text-end">${{ "{:,.0f}".format(s.p90) }}</td>
                  <td class="text-end">${{ "{:,.0f}".format(s.p95) }}</td>
                </tr>
                {% endif %}
              {% endfor %}
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- Per Unit $ -->
    <div class="col-lg-6">
      <div class="card h-100">
        <div class="card-header py-2"><h6 class="mb-0">Special Assessments — Per Unit ($)</h6></div>
        <div class="card-body p-0">
          <table class="table table-sm table-hover mb-0 kpi-table">
            <thead>
              <tr>
                <th>KPI</th><th class="text-end">Mean</th><th class="text-end">P25</th>
                <th class="text-end">P50</th><th class="text-end">P75</th>
                <th class="text-end">P90</th><th class="text-end">P95</th>
              </tr>
            </thead>
            <tbody>
              {% for key, label in kpi_labels.items() %}
                {% if key in summary.kpis_per_unit %}
                {% set s = summary.kpis_per_unit[key] %}
                <tr>
                  <td class="kpi-label">{{ label }}</td>
                  <td class="text-end">${{ "{:,.0f}".format(s.mean) }}</td>
                  <td class="text-end">${{ "{:,.0f}".format(s.p25) }}</td>
                  <td class="text-end">${{ "{:,.0f}".format(s.p50) }}</td>
                  <td class="text-end">${{ "{:,.0f}".format(s.p75) }}</td>
                  <td class="text-end">${{ "{:,.0f}".format(s.p90) }}</td>
                  <td class="text-end">${{ "{:,.0f}".format(s.p95) }}</td>
                </tr>
                {% endif %}
              {% endfor %}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </div>

  <!-- Risk Indicators -->
  <div class="card">
    <div class="card-header green py-2"><h6 class="mb-0 text-white">Risk Indicators</h6></div>
    <div class="card-body p-0">
      <table class="table table-sm mb-0">
        <tbody>
          <tr>
            <td class="text-muted">Starting reserve (total)</td>
            <td class="fw-semibold">${{ "{:,.0f}".format(summary.config.starting_reserve_total) }}</td>
            <td class="text-muted">Starting reserve per unit</td>
            <td class="fw-semibold">${{ "{:,.0f}".format(summary.config.starting_reserve_per_unit) }}</td>
          </tr>
          <tr>
            <td class="text-muted">Annual contribution (total)</td>
            <td class="fw-semibold">${{ "{:,.0f}".format(summary.config.annual_contribution_total) }}</td>
            <td class="text-muted">Annual contribution per unit</td>
            <td class="fw-semibold">${{ "{:,.0f}".format(summary.config.annual_contribution_per_unit) }}</td>
          </tr>
          <tr>
            <td class="text-muted">Probability of assessment — yrs 1–5</td>
            <td class="fw-semibold">{{ "{:.1%}".format(summary.probability_of_assessment.yr_1_5) }}</td>
            <td class="text-muted">Probability of assessment — yrs 1–10</td>
            <td class="fw-semibold">{{ "{:.1%}".format(summary.probability_of_assessment.yr_1_10) }}</td>
          </tr>
          <tr>
            <td class="text-muted">Probability of assessment — 30yr</td>
            <td class="fw-semibold">{{ "{:.1%}".format(summary.probability_of_assessment["30yr"]) }}</td>
            <td class="text-muted">Components in simulation</td>
            <td class="fw-semibold">{{ summary.components_loaded }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>

</div>
</body>
</html>
```

- [ ] **Step 6.2: Commit**

```bash
git add templates/index.html templates/results.html
git commit -m "feat: add index and results templates"
```

---

### Task 7: End-to-end test and push

- [ ] **Step 7.1: Run full test suite**

```bash
cd /Users/stevenlaidlaw/RFS_Warranty_Project
python3 -m pytest tests/ -v
```

Expected: `8 passed`

- [ ] **Step 7.2: Start the app and do a smoke test with sample data**

```bash
python3 app.py
```

Open http://localhost:8081 in a browser. Upload `reserve_study_components.json` is not CSV — use the real 2-Component CSV export, or create a minimal test CSV:

```bash
python3 -c "
import csv, io
rows = [
  ['Component','Replacement Budget','Design Life','Current Age','Replacement Year',
   'Current Replacement Cost','Maintenance Budget','Maintenance Life Cycle',
   'Remaining Maintenance Life','Maintenance Year','Current Maintenance Cost'],
  ['Roof - Asphalt Shingle','Yes','25','1','25','16500','No','','','',''],
  ['Asphalt','Yes','25','6','20','112000','Yes','9','6','4','56000'],
  ['Fence - Wood','Yes','20','3','18','4200','Yes','5','3','3','2000'],
  ['Foundation','No','','','','','No','','','',''],
]
with open('/tmp/test_reserve.csv', 'w', newline='') as f:
    csv.writer(f).writerows(rows)
print('Written /tmp/test_reserve.csv')
"
```

Upload `/tmp/test_reserve.csv` with: 6 units, opening balance \$50,000, annual contribution \$18,000. Click **Run Stress Test**. Verify:
- Loading overlay appears
- Results page shows two KPI tables
- All P25/P50/P75/P90/P95 values are populated
- Risk Indicators panel shows probabilities
- "Download CSV" works

- [ ] **Step 7.3: Run tests one final time**

```bash
python3 -m pytest tests/ -v
```

Expected: `8 passed`

- [ ] **Step 7.4: Commit remaining files and push**

```bash
git add app.py requirements.txt templates/ tests/ csv_parser.py stress_test.py
git status  # confirm nothing untracked is missed
git push origin main
```

---

## Self-Review

**Spec coverage:**

| Spec requirement | Task |
|---|---|
| stress_test.py: shock params as kwargs on samplers | Task 1 |
| stress_test.py: shock params as kwargs on run_stress_test() | Task 1 |
| stress_test.py: config dict uses local variables | Task 1 |
| parse_reserve_csv() returns component dict list | Task 3 |
| Column normalisation (lowercase + strip) | Task 3 |
| Rows with both budgets "No" dropped | Task 3 (+ tested Task 2) |
| Returns [] on error / empty file / missing columns | Task 3 (+ tested Task 2) |
| Flask app: / route renders form | Task 4 |
| Flask app: /run route parses CSV + runs stress test | Task 4 |
| Flask app: /download-csv returns KPI CSV | Task 4 |
| Flash error on invalid CSV or params | Task 4 |
| Loading overlay on submit | Task 5 |
| CSV file upload, property params, shock params with defaults | Task 5 |
| Config summary card | Task 6 |
| Total $ KPI table (P25/P50/P75/P90/P95) | Task 6 |
| Per Unit $ KPI table | Task 6 |
| Risk indicators panel | Task 6 |
| Download CSV button | Task 6 |

**Placeholder scan:** No TBD, TODO, or incomplete code blocks. ✓

**Type consistency:**
- `parse_reserve_csv(file_obj) -> list` defined in Task 3, imported and called in Task 4 ✓
- `st.run_stress_test(components, ..., life_shock_mean, ...)` signature updated in Task 1, called in Task 4 ✓
- `summary["components_loaded"]` set in Task 4 (`app.py`), read in Task 6 (`results.html`) ✓
- `summary.probability_of_assessment["30yr"]` uses bracket notation in Jinja2 because `30yr` is not a valid Python identifier — correct ✓
- `session["summary_path"]` set in `/run`, read in `/download-csv` ✓
