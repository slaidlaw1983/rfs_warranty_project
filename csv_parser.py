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
