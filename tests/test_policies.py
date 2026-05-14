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
