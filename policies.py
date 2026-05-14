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
