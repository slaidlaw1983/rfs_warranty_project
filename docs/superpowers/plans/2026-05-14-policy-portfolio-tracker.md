# Policy Portfolio Tracker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a portfolio-level view that captures warranty quotes as policies, tracks their lifecycle (`pending_quote` → `approved` / `rejected`), and computes program-wide rollup math (premium, coverage, expected claims, loss ratio, profit) across approved policies. Storage is a new "Policies" tab on the existing Google Sheet.

**Architecture:** Pure-function rollup math (`compute_program_stats`) stays independent of Sheet I/O for testability. A new `policies.py` module wraps gspread access (save / list / update_status / delete). Results page gets a "Save This Quote" card. New `/portfolio` page renders the rollup + filterable policy table with inline Approve/Reject/Delete buttons.

**Tech Stack:** Python 3.11, Flask, gspread, google-auth, Bootstrap 5.3, pytest 8.x.

**Spec:** `docs/superpowers/specs/2026-05-14-policy-portfolio-tracker-design.md`

**Base SHA:** `e7a0944` (current main)

---

## File Structure

**Created:**
- `policies.py` — Sheet I/O (`save_policy`, `list_policies`, `update_policy_status`, `delete_policy`) + pure `compute_program_stats`. No Flask deps.
- `templates/portfolio.html` — rollup card + status filter + policies table with action buttons.
- `tests/test_policies.py` — unit tests for `compute_program_stats` (pure) and for the row-payload serialization shape. gspread is mocked.

**Modified:**
- `app.py` — three new routes (`/save-policy`, `/portfolio`, `/portfolio/<policy_id>/<action>`) + import `policies` module.
- `templates/results.html` — append "Save This Quote" card at the bottom (visible only when warranty analysis succeeded).
- `templates/index.html` — add Portfolio button in the header next to the existing Pricing Calculator button.
- `templates/pricing.html` — add Portfolio button in the header.

---

## Task 1: TDD — Pure `compute_program_stats` rollup math

**Files:**
- Create: `policies.py`
- Create: `tests/test_policies.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_policies.py` with this content:

```python
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from policies import compute_program_stats


def _row(**overrides):
    base = {
        "policy_id": "pol-20260514-001",
        "status": "approved",
        "funding_model": "base",
        "coverage_total": 10_000.0,
        "claim_probability": 0.20,
        "warranty_premium_charged": 3_000.0,
    }
    base.update(overrides)
    return base


def test_compute_program_stats_empty():
    stats = compute_program_stats([])
    assert stats["n_policies"] == 0
    assert stats["total_premium"] == 0.0
    assert stats["total_coverage"] == 0.0
    assert stats["expected_claims"] == 0.0
    assert stats["program_loss_ratio"] == 0.0
    assert stats["modeled_profit"] == 0.0


def test_compute_program_stats_ignores_non_approved():
    rows = [
        _row(status="approved", coverage_total=10_000, claim_probability=0.2, warranty_premium_charged=3_000),
        _row(status="pending_quote", coverage_total=99_999, claim_probability=0.99, warranty_premium_charged=0),
        _row(status="rejected", coverage_total=99_999, claim_probability=0.99, warranty_premium_charged=0),
    ]
    stats = compute_program_stats(rows)
    assert stats["n_policies"] == 1
    assert stats["total_coverage"] == 10_000.0
    assert stats["total_premium"] == 3_000.0


def test_compute_program_stats_aggregates_approved():
    # Two approved policies; expected_claims = sum of (coverage × claim_prob)
    rows = [
        _row(status="approved", coverage_total=10_000, claim_probability=0.20, warranty_premium_charged=2_000),  # E[claim]=2000
        _row(status="approved", coverage_total=20_000, claim_probability=0.50, warranty_premium_charged=5_000),  # E[claim]=10000
        _row(status="rejected", coverage_total=99_999, claim_probability=0.99, warranty_premium_charged=99_999),
    ]
    stats = compute_program_stats(rows)
    assert stats["n_policies"] == 2
    assert stats["total_coverage"] == 30_000.0
    assert stats["total_premium"] == 7_000.0
    assert stats["expected_claims"] == 12_000.0
    assert abs(stats["program_loss_ratio"] - 12_000.0 / 7_000.0) < 1e-9
    assert stats["modeled_profit"] == 7_000.0 - 12_000.0


def test_compute_program_stats_zero_premium_no_div_by_zero():
    # All approved policies have zero premium charged — should not raise
    rows = [_row(status="approved", warranty_premium_charged=0, coverage_total=10_000, claim_probability=0.2)]
    stats = compute_program_stats(rows)
    assert stats["program_loss_ratio"] == 0.0
    assert stats["modeled_profit"] == -2_000.0  # 0 premium − 2000 expected claims


def test_compute_program_stats_string_inputs_coerced_to_float():
    # Sheet reads return strings — verify the function tolerates them
    rows = [{
        "policy_id": "pol-1",
        "status": "approved",
        "funding_model": "base",
        "coverage_total": "10000",
        "claim_probability": "0.20",
        "warranty_premium_charged": "3000",
    }]
    stats = compute_program_stats(rows)
    assert stats["total_coverage"] == 10_000.0
    assert stats["total_premium"] == 3_000.0
    assert stats["expected_claims"] == 2_000.0
```

- [ ] **Step 2: Run tests to verify they fail with ModuleNotFoundError**

```bash
cd /Users/stevenlaidlaw/RFS_Warranty_Project
python3 -m pytest tests/test_policies.py -v
```

Expected: `ModuleNotFoundError: No module named 'policies'`.

- [ ] **Step 3: Create `policies.py` with `compute_program_stats` only**

Create `policies.py` at the project root:

```python
"""
Policy portfolio storage + rollup math.

Two halves:
  - compute_program_stats(rows): PURE function — aggregates approved-status rows
    into program-level KPIs. No I/O. Unit-testable.
  - save_policy / list_policies / update_policy_status / delete_policy: gspread
    wrappers around the Policies worksheet. Fail-soft if env vars unset.

Sheet env vars (reused from the Submissions logger):
  GSHEETS_SHEET_ID, GSHEETS_SERVICE_ACCOUNT_JSON.
The worksheet name is hardcoded to "Policies".
"""

from typing import Any, Dict, List, Optional


def _coerce_float(value: Any, default: float = 0.0) -> float:
    """Tolerate string inputs from Sheets reads."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def compute_program_stats(rows: List[Dict[str, Any]]) -> Dict[str, float]:
    """Aggregate approved-status policy rows into program-level KPIs.

    Pure function — no I/O. Tolerates string inputs (Sheet reads return strings).

    Inputs that are missing or non-numeric are coerced to 0.0. Non-approved
    rows are skipped.
    """
    approved = [r for r in rows if r.get("status") == "approved"]

    total_premium  = sum(_coerce_float(r.get("warranty_premium_charged")) for r in approved)
    total_coverage = sum(_coerce_float(r.get("coverage_total")) for r in approved)
    expected_claims = sum(
        _coerce_float(r.get("coverage_total")) * _coerce_float(r.get("claim_probability"))
        for r in approved
    )

    program_loss_ratio = (expected_claims / total_premium) if total_premium > 0 else 0.0
    modeled_profit = total_premium - expected_claims

    return {
        "n_policies":        len(approved),
        "total_premium":     total_premium,
        "total_coverage":    total_coverage,
        "expected_claims":   expected_claims,
        "program_loss_ratio": program_loss_ratio,
        "modeled_profit":    modeled_profit,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_policies.py -v
```

Expected: 5 passed.

Run the full suite for no regression:

```bash
python3 -m pytest tests/ -q
```

Expected: 31 passed (26 prior + 5 new).

- [ ] **Step 5: Commit**

```bash
git add policies.py tests/test_policies.py
git commit -m "feat(policies): compute_program_stats rollup math (pure function)"
```

---

## Task 2: Sheet I/O — `save_policy` + `list_policies` + `update_policy_status` + `delete_policy`

**Files:**
- Modify: `policies.py` (add four I/O functions + helpers)
- Modify: `tests/test_policies.py` (add I/O tests using a fake-gspread injection point)

The four I/O functions take an optional `_sheet_client` parameter for test injection. Production code calls them without that parameter, in which case they construct a real gspread client from env vars (mirroring `_log_submission_to_sheets`).

- [ ] **Step 1: Add the test scaffolding for the fake gspread client**

Append this content to `tests/test_policies.py`:

```python
# ── I/O tests use a fake gspread client to avoid real Sheet access ────────

class FakeWorksheet:
    """Minimal stand-in for a gspread Worksheet — list-of-rows backing store."""
    def __init__(self, rows):
        # rows[0] = header. Each subsequent row is a list of cell values.
        self._rows = [list(r) for r in rows]
        self.row_count = len(self._rows)

    def get_all_records(self):
        if len(self._rows) < 2:
            return []
        header = self._rows[0]
        return [dict(zip(header, row)) for row in self._rows[1:]]

    def row_values(self, n):
        if n <= 0 or n > len(self._rows):
            return []
        return list(self._rows[n - 1])

    def append_row(self, row):
        self._rows.append(list(row))
        self.row_count += 1

    def update_cell(self, row, col, value):
        self._rows[row - 1][col - 1] = value

    def delete_rows(self, n):
        del self._rows[n - 1]
        self.row_count -= 1

    def find(self, value):
        for r_idx, row in enumerate(self._rows, start=1):
            for c_idx, cell in enumerate(row, start=1):
                if str(cell) == str(value):
                    class _Cell:
                        def __init__(s, row, col): s.row = row; s.col = col
                    return _Cell(r_idx, c_idx)
        from gspread.exceptions import CellNotFound
        raise CellNotFound(f"{value} not found")


def _ws(rows=None):
    """Build a FakeWorksheet preloaded with a header (and optional data rows)."""
    header = [
        "policy_id", "created_at", "status", "funding_model",
        "property_name", "contact_name", "contact_email",
        "property_type", "num_units",
        "standard_price", "coverage_per_unit", "coverage_total",
        "claim_probability", "warranty_premium_default", "warranty_premium_charged",
        "verdict",
    ]
    return FakeWorksheet([header] + (rows or []))


# ── save_policy tests ─────────────────────────────────────────────────────

def test_save_policy_returns_id_and_appends_row(monkeypatch):
    import policies as p
    ws = _ws()
    payload = {
        "status": "pending_quote",
        "funding_model": "full",
        "property_name": "Test Property",
        "contact_name": "Tester",
        "contact_email": "test@example.com",
        "property_type": "Apartment",
        "num_units": 24,
        "standard_price": 4200,
        "coverage_per_unit": 500,
        "coverage_total": 12000,
        "claim_probability": 0.18,
        "warranty_premium_default": 2100,
        "warranty_premium_charged": 1680,
        "verdict": "good",
    }
    policy_id = p.save_policy(payload, _worksheet=ws)
    assert policy_id is not None
    assert policy_id.startswith("pol-")
    # One header + one data row
    assert ws.row_count == 2
    saved = ws.get_all_records()[0]
    assert saved["policy_id"] == policy_id
    assert saved["status"] == "pending_quote"
    assert saved["funding_model"] == "full"
    assert saved["property_name"] == "Test Property"


def test_save_policy_increments_sequence_for_same_day(monkeypatch):
    import policies as p
    ws = _ws()
    payload = {
        "status": "pending_quote", "funding_model": "base",
        "property_name": "A", "contact_name": "", "contact_email": "",
        "property_type": "Apartment", "num_units": 10,
        "standard_price": 1000, "coverage_per_unit": 500, "coverage_total": 5000,
        "claim_probability": 0.10, "warranty_premium_default": 500,
        "warranty_premium_charged": 500, "verdict": "good",
    }
    id1 = p.save_policy(payload, _worksheet=ws)
    id2 = p.save_policy(payload, _worksheet=ws)
    id3 = p.save_policy(payload, _worksheet=ws)
    # All share today's prefix; sequences increment 1, 2, 3
    assert id1.split("-")[-1] == "001"
    assert id2.split("-")[-1] == "002"
    assert id3.split("-")[-1] == "003"


# ── list_policies tests ──────────────────────────────────────────────────

def test_list_policies_returns_all_with_no_filter():
    import policies as p
    ws = _ws([
        ["pol-1", "", "pending_quote", "base", "A", "", "", "Apartment", 10,
         1000, 500, 5000, 0.1, 500, 500, "good"],
        ["pol-2", "", "approved", "full", "B", "", "", "Townhouse", 20,
         2000, 500, 10000, 0.2, 1000, 800, "good"],
    ])
    rows = p.list_policies(_worksheet=ws)
    assert len(rows) == 2
    assert {r["policy_id"] for r in rows} == {"pol-1", "pol-2"}


def test_list_policies_filters_by_status():
    import policies as p
    ws = _ws([
        ["pol-1", "", "pending_quote", "base", "A", "", "", "Apartment", 10,
         1000, 500, 5000, 0.1, 500, 500, "good"],
        ["pol-2", "", "approved", "full", "B", "", "", "Townhouse", 20,
         2000, 500, 10000, 0.2, 1000, 800, "good"],
        ["pol-3", "", "rejected", "base", "C", "", "", "Apartment", 30,
         3000, 500, 15000, 0.3, 1500, 1200, "moderate"],
    ])
    rows = p.list_policies(status_filter=["approved"], _worksheet=ws)
    assert len(rows) == 1
    assert rows[0]["policy_id"] == "pol-2"

    rows = p.list_policies(status_filter=["pending_quote", "rejected"], _worksheet=ws)
    assert {r["policy_id"] for r in rows} == {"pol-1", "pol-3"}


def test_list_policies_search_matches_property_or_email_case_insensitive():
    import policies as p
    ws = _ws([
        ["pol-1", "", "approved", "base", "Canmore Gardens", "", "alice@x.com",
         "Apartment", 10, 1000, 500, 5000, 0.1, 500, 500, "good"],
        ["pol-2", "", "approved", "full", "Sunrise", "", "BOB@example.com",
         "Townhouse", 20, 2000, 500, 10000, 0.2, 1000, 800, "good"],
    ])
    rows = p.list_policies(search="canmore", _worksheet=ws)
    assert {r["policy_id"] for r in rows} == {"pol-1"}
    rows = p.list_policies(search="bob", _worksheet=ws)
    assert {r["policy_id"] for r in rows} == {"pol-2"}


# ── update_policy_status / delete_policy ─────────────────────────────────

def test_update_policy_status_changes_status():
    import policies as p
    ws = _ws([
        ["pol-1", "", "pending_quote", "base", "A", "", "", "Apartment", 10,
         1000, 500, 5000, 0.1, 500, 500, "good"],
    ])
    ok = p.update_policy_status("pol-1", "approved", _worksheet=ws)
    assert ok is True
    rows = ws.get_all_records()
    assert rows[0]["status"] == "approved"


def test_update_policy_status_missing_id_returns_false():
    import policies as p
    ws = _ws()
    assert p.update_policy_status("pol-missing", "approved", _worksheet=ws) is False


def test_delete_policy_removes_row():
    import policies as p
    ws = _ws([
        ["pol-1", "", "approved", "base", "A", "", "", "Apartment", 10,
         1000, 500, 5000, 0.1, 500, 500, "good"],
        ["pol-2", "", "approved", "full", "B", "", "", "Townhouse", 20,
         2000, 500, 10000, 0.2, 1000, 800, "good"],
    ])
    ok = p.delete_policy("pol-1", _worksheet=ws)
    assert ok is True
    rows = ws.get_all_records()
    assert len(rows) == 1
    assert rows[0]["policy_id"] == "pol-2"


def test_delete_policy_missing_id_returns_false():
    import policies as p
    ws = _ws()
    assert p.delete_policy("pol-missing", _worksheet=ws) is False
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/stevenlaidlaw/RFS_Warranty_Project
python3 -m pytest tests/test_policies.py -v
```

Expected: tests pass the 5 from Task 1, and the 11 new ones fail with `AttributeError: module 'policies' has no attribute 'save_policy'` (and the other three functions).

- [ ] **Step 3: Implement the four I/O functions in `policies.py`**

Append this to `policies.py` (after the existing `compute_program_stats`):

```python
# ── Sheet I/O ─────────────────────────────────────────────────────────────

import json
import os
from datetime import datetime, timezone


POLICIES_WORKSHEET = "Policies"

POLICY_HEADER = [
    "policy_id", "created_at", "status", "funding_model",
    "property_name", "contact_name", "contact_email",
    "property_type", "num_units",
    "standard_price", "coverage_per_unit", "coverage_total",
    "claim_probability", "warranty_premium_default", "warranty_premium_charged",
    "verdict",
]


def _get_worksheet():
    """Construct a gspread worksheet handle from env vars, or return None.

    Creates the Policies worksheet with the header row if it doesn't exist.
    """
    sheet_id = os.environ.get("GSHEETS_SHEET_ID")
    creds_json = os.environ.get("GSHEETS_SERVICE_ACCOUNT_JSON")
    if not (sheet_id and creds_json):
        return None
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        print("[policies] gspread/google-auth not installed")
        return None
    try:
        creds_info = json.loads(creds_json)
    except json.JSONDecodeError as e:
        print(f"[policies] could not parse GSHEETS_SERVICE_ACCOUNT_JSON: {e}")
        return None
    try:
        scope = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_info(creds_info, scopes=scope)
        client = gspread.authorize(creds)
        sh = client.open_by_key(sheet_id)
        try:
            ws = sh.worksheet(POLICIES_WORKSHEET)
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(title=POLICIES_WORKSHEET, rows=1000, cols=len(POLICY_HEADER))
        # Ensure header row exists
        if not ws.row_values(1):
            ws.append_row(POLICY_HEADER)
        return ws
    except Exception as e:
        print(f"[policies] worksheet handle failed: {e}")
        return None


def _next_policy_id(ws) -> str:
    """Generate pol-YYYYMMDD-NNN with NNN incremented based on today's count."""
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    prefix = f"pol-{today}-"
    try:
        records = ws.get_all_records()
    except Exception:
        records = []
    n_today = sum(1 for r in records if str(r.get("policy_id", "")).startswith(prefix))
    return f"{prefix}{n_today + 1:03d}"


def save_policy(payload: Dict[str, Any], _worksheet=None) -> Optional[str]:
    """Append a policy row. Returns the new policy_id, or None on failure."""
    ws = _worksheet if _worksheet is not None else _get_worksheet()
    if ws is None:
        return None
    policy_id = _next_policy_id(ws)
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    row = [
        policy_id,
        created_at,
        payload.get("status", "pending_quote"),
        payload.get("funding_model", ""),
        payload.get("property_name", ""),
        payload.get("contact_name", ""),
        payload.get("contact_email", ""),
        payload.get("property_type", ""),
        payload.get("num_units", ""),
        payload.get("standard_price", ""),
        payload.get("coverage_per_unit", ""),
        payload.get("coverage_total", ""),
        payload.get("claim_probability", ""),
        payload.get("warranty_premium_default", ""),
        payload.get("warranty_premium_charged", ""),
        payload.get("verdict", ""),
    ]
    try:
        ws.append_row(row)
        return policy_id
    except Exception as e:
        print(f"[policies] save_policy failed: {e}")
        return None


def list_policies(
    status_filter: Optional[List[str]] = None,
    search: Optional[str] = None,
    _worksheet=None,
) -> List[Dict[str, Any]]:
    """Return all policy rows (optionally filtered)."""
    ws = _worksheet if _worksheet is not None else _get_worksheet()
    if ws is None:
        return []
    try:
        records = ws.get_all_records()
    except Exception as e:
        print(f"[policies] list_policies failed: {e}")
        return []

    if status_filter:
        records = [r for r in records if r.get("status") in status_filter]

    if search:
        s = search.lower()
        records = [
            r for r in records
            if s in str(r.get("property_name", "")).lower()
            or s in str(r.get("contact_email", "")).lower()
        ]

    return records


def _find_row_index(ws, policy_id: str) -> Optional[int]:
    """Return the 1-based row number of the policy_id, or None."""
    try:
        cell = ws.find(policy_id)
        return cell.row
    except Exception:
        return None


def update_policy_status(policy_id: str, new_status: str, _worksheet=None) -> bool:
    """Update the status column for policy_id. Returns False if not found."""
    ws = _worksheet if _worksheet is not None else _get_worksheet()
    if ws is None:
        return False
    row_idx = _find_row_index(ws, policy_id)
    if row_idx is None:
        return False
    status_col = POLICY_HEADER.index("status") + 1  # 1-based
    try:
        ws.update_cell(row_idx, status_col, new_status)
        return True
    except Exception as e:
        print(f"[policies] update_policy_status failed: {e}")
        return False


def delete_policy(policy_id: str, _worksheet=None) -> bool:
    """Delete the policy row. Returns False if not found."""
    ws = _worksheet if _worksheet is not None else _get_worksheet()
    if ws is None:
        return False
    row_idx = _find_row_index(ws, policy_id)
    if row_idx is None:
        return False
    try:
        ws.delete_rows(row_idx)
        return True
    except Exception as e:
        print(f"[policies] delete_policy failed: {e}")
        return False
```

- [ ] **Step 4: Run tests**

```bash
python3 -m pytest tests/test_policies.py -v
```

Expected: 16 tests pass (5 from Task 1 + 11 new).

Full suite:

```bash
python3 -m pytest tests/ -q
```

Expected: 42 passed.

- [ ] **Step 5: Commit**

```bash
git add policies.py tests/test_policies.py
git commit -m "feat(policies): Sheet I/O (save, list, update_status, delete)"
```

---

## Task 3: Save flow — `/save-policy` route + "Save This Quote" card on results page

**Files:**
- Modify: `app.py` (add import + `/save-policy` route)
- Modify: `templates/results.html` (append the save card)

- [ ] **Step 1: Add policies import to `app.py`**

In `app.py`, near the existing `from warranty import calculate_warranty_risk_analysis` line, add:

```python
import policies
```

- [ ] **Step 2: Add the `/save-policy` route**

Append this route to `app.py` (just before `if __name__ == "__main__":`):

```python
@app.route("/save-policy", methods=["POST"])
def save_policy_route():
    funding_model = request.form.get("funding_model", "").strip()
    if funding_model not in ("base", "full"):
        flash("Please select a funding model (Base Case or Full Funding).")
        return redirect(url_for("index"))

    status = request.form.get("status", "pending_quote").strip()
    if status not in ("pending_quote", "approved", "rejected"):
        status = "pending_quote"

    payload = {
        "status":                    status,
        "funding_model":             funding_model,
        "property_name":             request.form.get("property_name", "").strip(),
        "contact_name":              request.form.get("contact_name", "").strip(),
        "contact_email":             request.form.get("contact_email", "").strip(),
        "property_type":             request.form.get("property_type", "").strip(),
        "num_units":                 request.form.get("num_units", ""),
        "standard_price":            request.form.get("standard_price", ""),
        "coverage_per_unit":         request.form.get("coverage_per_unit", ""),
        "coverage_total":            request.form.get("coverage_total", ""),
        "claim_probability":         request.form.get(f"claim_probability_{funding_model}", ""),
        "warranty_premium_default":  request.form.get("warranty_premium_default", ""),
        "warranty_premium_charged":  request.form.get(f"warranty_premium_charged_{funding_model}", ""),
        "verdict":                   request.form.get(f"verdict_{funding_model}", ""),
    }
    policy_id = policies.save_policy(payload)
    if policy_id is None:
        flash("Could not save policy — check that GSHEETS_* env vars are set.")
        return redirect(url_for("index"))

    return redirect(url_for("portfolio_route", just_saved=policy_id))
```

(The `portfolio_route` endpoint is added in Task 4. Flask will resolve it at request time; static analysis won't flag this even though the function isn't defined yet — but the route also won't work end-to-end until Task 4 ships. That's fine; Task 4 is next.)

- [ ] **Step 3: Add Sheet-configured detection helper in `app.py`**

Near the top of `app.py` (after the imports), add:

```python
def _sheet_configured() -> bool:
    return bool(os.environ.get("GSHEETS_SHEET_ID")
                and os.environ.get("GSHEETS_SERVICE_ACCOUNT_JSON"))
```

Then in the `/run` route, just before the final `return render_template("results.html", summary=summary)` line, set a flag on the summary:

```python
    summary["sheet_configured"] = _sheet_configured()
```

- [ ] **Step 4: Append the "Save This Quote" card to `templates/results.html`**

In `templates/results.html`, find the closing `</div>` of the warranty-analysis card (just before the `<script>` that builds the chart). Insert this card AFTER the warranty card's `{% endif %}` and BEFORE the closing `</div>` of the page container:

```html
  <!-- 7. Save This Quote -->
  {% if summary.sheet_configured and summary.warranty_analysis
        and (summary.warranty_analysis.base or summary.warranty_analysis.full) %}
  {% set wa_base = summary.warranty_analysis.base %}
  {% set wa_full = summary.warranty_analysis.full %}
  <div class="card mb-4">
    <div class="card-header py-2"><h6 class="mb-0">Save This Quote</h6></div>
    <div class="card-body">
      <form action="/save-policy" method="POST" class="row g-3 align-items-end">
        <div class="col-md-5">
          <label class="form-label fw-semibold mb-1">Funding model (required)</label>
          <div class="form-check">
            <input class="form-check-input" type="radio" id="fm_base" name="funding_model" value="base" required />
            <label class="form-check-label" for="fm_base">Base Case</label>
          </div>
          <div class="form-check">
            <input class="form-check-input" type="radio" id="fm_full" name="funding_model" value="full" required />
            <label class="form-check-label" for="fm_full">Full Funding</label>
          </div>
        </div>
        <div class="col-md-4">
          <label class="form-label fw-semibold mb-1">Initial status</label>
          <select name="status" class="form-select">
            <option value="pending_quote" selected>Pending quote</option>
            <option value="approved">Approved</option>
            <option value="rejected">Rejected</option>
          </select>
        </div>
        <div class="col-md-3">
          <button type="submit" class="btn btn-primary w-100">Save policy</button>
        </div>

        <!-- Hidden payload carriers -->
        <input type="hidden" name="property_name"  value="{{ summary.property_name | default('') }}" />
        <input type="hidden" name="contact_name"   value="{{ summary.contact_name  | default('') }}" />
        <input type="hidden" name="contact_email"  value="{{ summary.contact_email | default('') }}" />
        <input type="hidden" name="property_type"  value="{{ (wa_base or wa_full).property_type_used | default('') }}" />
        <input type="hidden" name="num_units"      value="{{ summary.config.num_units }}" />
        <input type="hidden" name="standard_price" value="{{ (wa_base or wa_full).standard_price_used | default('') }}" />
        <input type="hidden" name="coverage_per_unit" value="{{ (wa_base or wa_full).target_terms.coverage_per_unit }}" />
        <input type="hidden" name="coverage_total" value="{{ (wa_base or wa_full).target_terms.coverage_total }}" />

        <input type="hidden" name="claim_probability_base" value="{{ summary.base.prob_assessment.yr_1_5 if summary.base else '' }}" />
        <input type="hidden" name="claim_probability_full" value="{{ summary.full.prob_assessment.yr_1_5 if summary.full else '' }}" />

        <input type="hidden" name="warranty_premium_default" value="{{ (wa_base or wa_full).target_terms.warranty_premium_default }}" />
        <input type="hidden" name="warranty_premium_charged_base" value="{{ wa_base.target_terms.warranty_premium_displayed if wa_base else '' }}" />
        <input type="hidden" name="warranty_premium_charged_full" value="{{ wa_full.target_terms.warranty_premium_displayed if wa_full else '' }}" />

        <input type="hidden" name="verdict_base" value="{{ wa_base.verdict if wa_base else '' }}" />
        <input type="hidden" name="verdict_full" value="{{ wa_full.verdict if wa_full else '' }}" />
      </form>
    </div>
  </div>
  {% endif %}
```

- [ ] **Step 5: Pass submitter info into the summary for template hidden inputs**

In `app.py` `/run`, find the block that assigns `property_name`, `contact_name`, `contact_email` from the form. Immediately after `summary["sheet_configured"] = _sheet_configured()` (added in Step 3), add:

```python
    summary["property_name"]  = property_name
    summary["contact_name"]   = contact_name
    summary["contact_email"]  = contact_email
```

- [ ] **Step 6: Smoke check imports**

```bash
cd /Users/stevenlaidlaw/RFS_Warranty_Project
python3 -c "import app; print('ok')"
python3 -m pytest tests/ -q
```

Expected: `ok` and `42 passed`.

- [ ] **Step 7: Commit**

```bash
git add app.py templates/results.html
git commit -m "feat(app): Save This Quote card + /save-policy route"
```

---

## Task 4: `/portfolio` route + `templates/portfolio.html`

**Files:**
- Modify: `app.py` (add `/portfolio` route)
- Create: `templates/portfolio.html`

- [ ] **Step 1: Add the `/portfolio` route to `app.py`**

Append this route just before `if __name__ == "__main__":` (and after the `/save-policy` route from Task 3):

```python
@app.route("/portfolio")
def portfolio_route():
    if not _sheet_configured():
        return render_template("portfolio.html",
                               configured=False,
                               policies=[],
                               stats=policies.compute_program_stats([]),
                               status_filter=["approved"],
                               search="",
                               just_saved=None)

    # Approved-only for the rollup (always)
    approved_rows = policies.list_policies(status_filter=["approved"])
    stats = policies.compute_program_stats(approved_rows)

    # Filterable table (default to approved if no filter supplied)
    sel = request.args.getlist("status") or ["approved"]
    search = (request.args.get("search") or "").strip()
    rows = policies.list_policies(
        status_filter=sel if sel != ["all"] else None,
        search=search or None,
    )
    rows.sort(key=lambda r: r.get("created_at", ""), reverse=True)

    return render_template("portfolio.html",
                           configured=True,
                           policies=rows,
                           stats=stats,
                           status_filter=sel,
                           search=search,
                           just_saved=request.args.get("just_saved"))
```

- [ ] **Step 2: Create `templates/portfolio.html`**

Create the file with this content:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Policy Portfolio</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet" />
  <style>
    body { background: #f4f6f9; }
    .card { border: none; border-radius: 12px; box-shadow: 0 2px 12px rgba(0,0,0,0.08); }
    .card-header { background: #1a3a5c; color: #fff; border-radius: 12px 12px 0 0 !important; }
    .stat { text-align: center; padding: 0.5rem; }
    .stat .label { color: #6c757d; font-size: 0.78rem; }
    .stat .value { font-weight: 600; font-size: 1.15rem; }
    .badge-pending { background: #6c757d; }
    .badge-approved { background: #1e6b3c; }
    .badge-rejected { background: #b71c1c; }
    .badge-funding { background: #1a3a5c; }
    table { font-size: 0.88rem; }
  </style>
</head>
<body>
<div class="container py-5" style="max-width: 1300px;">

  <div class="d-flex align-items-center justify-content-between mb-4 flex-wrap gap-2">
    <div>
      <h2 class="fw-bold mb-0" style="color:#1a3a5c;">Policy Portfolio</h2>
      <p class="text-muted mb-0">Captured quotes + lifecycle + program-level rollup.</p>
    </div>
    <div class="d-flex gap-2">
      <a href="/" class="btn btn-outline-secondary">← Back to Stress Test</a>
      <a href="/pricing" class="btn btn-outline-primary">Pricing Calculator</a>
    </div>
  </div>

  {% if not configured %}
    <div class="alert alert-warning">
      Policy tracker is not configured. Set <code>GSHEETS_SHEET_ID</code> and
      <code>GSHEETS_SERVICE_ACCOUNT_JSON</code> env vars to enable saving and
      reading policies.
    </div>
  {% endif %}

  {% if just_saved %}
    <div class="alert alert-success">
      Policy <strong>{{ just_saved }}</strong> saved.
    </div>
  {% endif %}

  {% with messages = get_flashed_messages() %}
    {% if messages %}
      {% for msg in messages %}
        <div class="alert alert-danger">{{ msg }}</div>
      {% endfor %}
    {% endif %}
  {% endwith %}

  <!-- Program Summary -->
  <div class="card mb-4">
    <div class="card-header py-2"><h6 class="mb-0">Program Summary (approved only)</h6></div>
    <div class="card-body">
      <div class="row g-2">
        <div class="col-md-2 col-6 stat">
          <div class="label">Active policies</div>
          <div class="value">{{ stats.n_policies }}</div>
        </div>
        <div class="col-md-2 col-6 stat">
          <div class="label">Total premium</div>
          <div class="value">${{ "{:,.0f}".format(stats.total_premium) }}</div>
        </div>
        <div class="col-md-2 col-6 stat">
          <div class="label">Total coverage</div>
          <div class="value">${{ "{:,.0f}".format(stats.total_coverage) }}</div>
        </div>
        <div class="col-md-2 col-6 stat">
          <div class="label">Σ expected claims</div>
          <div class="value">${{ "{:,.0f}".format(stats.expected_claims) }}</div>
        </div>
        <div class="col-md-2 col-6 stat">
          <div class="label">Loss ratio</div>
          <div class="value">{{ "{:.1%}".format(stats.program_loss_ratio) }}</div>
        </div>
        <div class="col-md-2 col-6 stat">
          <div class="label">Modeled profit</div>
          <div class="value">${{ "{:,.0f}".format(stats.modeled_profit) }}</div>
        </div>
      </div>
      <div class="text-muted mt-2" style="font-size:0.72rem;">
        Modeled — based on claim probabilities at quote time, not realized claims.
      </div>
    </div>
  </div>

  <!-- Filters -->
  <div class="card mb-4">
    <div class="card-header py-2"><h6 class="mb-0">Filters</h6></div>
    <div class="card-body">
      <form method="GET" class="row g-3 align-items-end">
        <div class="col-md-6">
          <label class="form-label fw-semibold">Status</label>
          <div>
            {% for s, label in [('pending_quote','Pending quote'),
                                ('approved','Approved'),
                                ('rejected','Rejected')] %}
              <div class="form-check form-check-inline">
                <input class="form-check-input" type="checkbox"
                       name="status" value="{{ s }}"
                       id="st_{{ s }}"
                       {% if s in status_filter %}checked{% endif %} />
                <label class="form-check-label" for="st_{{ s }}">{{ label }}</label>
              </div>
            {% endfor %}
          </div>
        </div>
        <div class="col-md-4">
          <label class="form-label fw-semibold">Search</label>
          <input type="text" name="search" value="{{ search }}" class="form-control" placeholder="Property name or email" />
        </div>
        <div class="col-md-2">
          <button type="submit" class="btn btn-primary w-100">Apply</button>
        </div>
      </form>
    </div>
  </div>

  <!-- Policies Table -->
  <div class="card mb-4">
    <div class="card-header py-2"><h6 class="mb-0">Policies ({{ policies|length }})</h6></div>
    <div class="card-body p-0">
      <div class="table-responsive">
        <table class="table table-hover mb-0">
          <thead>
            <tr>
              <th>ID</th><th>Created</th><th>Property</th><th>Model</th>
              <th>Status</th><th class="text-end">Premium</th>
              <th class="text-end">Coverage</th><th class="text-end">P(claim)</th>
              <th>Verdict</th><th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {% for p in policies %}
            <tr>
              <td><code>{{ p.policy_id }}</code></td>
              <td>{{ p.created_at[:10] if p.created_at else '' }}</td>
              <td>{{ p.property_name }}<br/><small class="text-muted">{{ p.contact_email }}</small></td>
              <td><span class="badge badge-funding">{{ p.funding_model }}</span></td>
              <td>
                {% if p.status == 'approved' %}
                  <span class="badge badge-approved">approved</span>
                {% elif p.status == 'rejected' %}
                  <span class="badge badge-rejected">rejected</span>
                {% else %}
                  <span class="badge badge-pending">pending</span>
                {% endif %}
              </td>
              <td class="text-end">${{ "{:,.0f}".format(p.warranty_premium_charged|float) }}</td>
              <td class="text-end">${{ "{:,.0f}".format(p.coverage_total|float) }}</td>
              <td class="text-end">{{ "{:.1%}".format(p.claim_probability|float) }}</td>
              <td><small>{{ p.verdict }}</small></td>
              <td>
                <div class="d-flex gap-1">
                  {% if p.status != 'approved' %}
                    <form action="/portfolio/{{ p.policy_id }}/approve" method="POST" class="d-inline">
                      <button class="btn btn-sm btn-success">Approve</button>
                    </form>
                  {% endif %}
                  {% if p.status != 'rejected' %}
                    <form action="/portfolio/{{ p.policy_id }}/reject" method="POST" class="d-inline">
                      <button class="btn btn-sm btn-warning">Reject</button>
                    </form>
                  {% endif %}
                  <form action="/portfolio/{{ p.policy_id }}/delete" method="POST" class="d-inline"
                        onsubmit="return confirm('Delete policy {{ p.policy_id }}?');">
                    <button class="btn btn-sm btn-outline-danger">Delete</button>
                  </form>
                </div>
              </td>
            </tr>
            {% else %}
            <tr><td colspan="10" class="text-center text-muted py-3">No policies match the current filter.</td></tr>
            {% endfor %}
          </tbody>
        </table>
      </div>
    </div>
  </div>

</div>
</body>
</html>
```

- [ ] **Step 3: Smoke check the route**

```bash
cd /Users/stevenlaidlaw/RFS_Warranty_Project
python3 -c "import app; print('ok')"
python3 app.py > /tmp/flask.log 2>&1 &
FLASK_PID=$!
sleep 2

curl -s -o /tmp/portfolio.html -w "HTTP %{http_code}\n" http://127.0.0.1:8081/portfolio
grep -c -E "Policy Portfolio|Program Summary|Filters|Policies \(" /tmp/portfolio.html

kill $FLASK_PID 2>/dev/null
sleep 1
grep -E "Traceback|Error|Exception" /tmp/flask.log || echo "no errors"
```

Expected: HTTP 200, grep count ≥ 4, "no errors".

(The page will show "Policy tracker is not configured" because GSHEETS env vars aren't set locally — that's the expected fail-soft path.)

- [ ] **Step 4: Commit**

```bash
git add app.py templates/portfolio.html
git commit -m "feat(portfolio): /portfolio page with rollup + filter + table"
```

---

## Task 5: Action endpoints — `/portfolio/<id>/approve | reject | delete`

**Files:**
- Modify: `app.py` (three new routes)

- [ ] **Step 1: Add the three action routes**

Append to `app.py` (just before `if __name__ == "__main__":`):

```python
@app.route("/portfolio/<policy_id>/approve", methods=["POST"])
def portfolio_approve(policy_id):
    ok = policies.update_policy_status(policy_id, "approved")
    if not ok:
        flash(f"Could not approve policy {policy_id} — not found or Sheet not configured.")
    return redirect(url_for("portfolio_route"))


@app.route("/portfolio/<policy_id>/reject", methods=["POST"])
def portfolio_reject(policy_id):
    ok = policies.update_policy_status(policy_id, "rejected")
    if not ok:
        flash(f"Could not reject policy {policy_id} — not found or Sheet not configured.")
    return redirect(url_for("portfolio_route"))


@app.route("/portfolio/<policy_id>/delete", methods=["POST"])
def portfolio_delete(policy_id):
    ok = policies.delete_policy(policy_id)
    if not ok:
        flash(f"Could not delete policy {policy_id} — not found or Sheet not configured.")
    return redirect(url_for("portfolio_route"))
```

- [ ] **Step 2: Smoke check**

```bash
cd /Users/stevenlaidlaw/RFS_Warranty_Project
python3 -c "import app; print('ok')"
python3 -m pytest tests/ -q
```

Expected: `ok` and `42 passed`.

- [ ] **Step 3: Commit**

```bash
git add app.py
git commit -m "feat(portfolio): approve/reject/delete action endpoints"
```

---

## Task 6: Header navigation — add Portfolio link on index and pricing pages

**Files:**
- Modify: `templates/index.html`
- Modify: `templates/pricing.html`

- [ ] **Step 1: Update `templates/index.html`**

Find this line in `templates/index.html`:

```html
    <a href="/pricing" class="btn btn-outline-primary btn-sm">View RFS Pricing Calculator →</a>
```

Replace it with these two buttons:

```html
    <a href="/pricing" class="btn btn-outline-primary btn-sm">RFS Pricing Calculator</a>
    <a href="/portfolio" class="btn btn-outline-primary btn-sm">📊 Policy Portfolio</a>
```

- [ ] **Step 2: Update `templates/pricing.html`**

Find this line in `templates/pricing.html`:

```html
    <a href="/" class="btn btn-outline-secondary">← Back to Stress Test</a>
```

Replace it with a wrapped pair of buttons (the parent flex container expects a single child here):

```html
    <div class="d-flex gap-2">
      <a href="/" class="btn btn-outline-secondary">← Back to Stress Test</a>
      <a href="/portfolio" class="btn btn-outline-primary">📊 Policy Portfolio</a>
    </div>
```

- [ ] **Step 3: Smoke check**

```bash
cd /Users/stevenlaidlaw/RFS_Warranty_Project
python3 app.py > /tmp/flask.log 2>&1 &
FLASK_PID=$!
sleep 2

curl -s -o /tmp/landing.html http://127.0.0.1:8081/
grep -c "Policy Portfolio" /tmp/landing.html

curl -s -o /tmp/pricing.html "http://127.0.0.1:8081/pricing"
grep -c "Policy Portfolio" /tmp/pricing.html

kill $FLASK_PID 2>/dev/null
sleep 1
```

Expected: ≥ 1 match on each page.

- [ ] **Step 4: Commit**

```bash
git add templates/index.html templates/pricing.html
git commit -m "feat(ui): Portfolio link in landing + pricing headers"
```

---

## Task 7: Final smoke + end-to-end happy path (without real Sheet)

**Files:**
- None (verification only)

- [ ] **Step 1: Run the full test suite**

```bash
cd /Users/stevenlaidlaw/RFS_Warranty_Project
python3 -m pytest tests/ -v
```

Expected: 42 passed (8 csv_parser + 8 stress_test_mc + 10 warranty + 16 policies).

- [ ] **Step 2: End-to-end smoke**

```bash
python3 app.py > /tmp/flask.log 2>&1 &
FLASK_PID=$!
sleep 2

# Submit a stress test
curl -s -o /tmp/results.html -w "results: %{http_code}\n" \
  -X POST http://127.0.0.1:8081/run \
  -F "reserve_csv=@/Users/stevenlaidlaw/RFS_Warranty_Project/book1_reserve_study.csv" \
  -F "property_name=Portfolio Smoke" \
  -F "contact_email=test@example.com" \
  -F "property_type=Apartment" \
  -F "num_units=24" \
  -F "starting_reserve=120000" \
  -F "base_contribution=40000" \
  -F "full_funding_contribution=140000" \
  -F "inflation_rate=0.03" \
  -F "interest_rate=0.025"

# Without GSHEETS_*, the Save card should NOT render
grep -c "Save This Quote" /tmp/results.html   # expect 0 locally
echo "---"
grep -c "Save policy" /tmp/results.html       # expect 0 locally

# Visit portfolio
curl -s -o /tmp/portfolio.html -w "portfolio: %{http_code}\n" http://127.0.0.1:8081/portfolio
grep -c "Policy tracker is not configured" /tmp/portfolio.html   # expect 1 locally

kill $FLASK_PID 2>/dev/null
sleep 1
grep -E "Traceback|Error|Exception" /tmp/flask.log || echo "no errors"
```

Expected:
- `results: 200`, `portfolio: 200`
- Save card NOT shown locally (no GSHEETS env vars)
- Portfolio page shows the "not configured" warning
- "no errors"

(On Render with real env vars, the Save card will appear and policies will persist. We're verifying the local fail-soft path here.)

- [ ] **Step 3: Git status check**

```bash
git status
```

Expected: clean working tree (pre-existing untracked files OK).

- [ ] **Step 4: No commit needed if all steps passed**

Task 7 is verification-only.

---

## Done

All seven tasks committed. The app now:
1. Captures warranty quotes as policies on explicit save (results page card)
2. Persists them in the existing Google Sheet (new Policies tab)
3. Shows `/portfolio` with rollup math and a filterable lifecycle table
4. Supports Approve / Reject / Delete inline
5. Fails-soft when GSHEETS env vars aren't set

On Render, the env vars are already configured (used by `_log_submission_to_sheets`), so on deploy the feature is immediately live with the existing service account.
