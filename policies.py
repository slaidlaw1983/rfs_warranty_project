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
