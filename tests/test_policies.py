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
