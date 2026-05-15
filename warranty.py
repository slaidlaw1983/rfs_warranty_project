"""
Warranty risk analysis for the RFS Warranty Project.

Pure functions — no Flask, no IO. Takes the per-trial 1-5yr special-assessment
totals from the Monte Carlo run and produces a quote + verdict for the warranty
product.

Model (post-2026-05-15 spec):
  - Premium tier price = standard_price × (1 + premium_markup_pct).
  - Coverage           = 2 × Premium tier price (no per-unit input).
  - Payout per claim   = full coverage_total on ANY special assessment in the
    term (no deductible).
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
COVERAGE_MULTIPLE     = 2.0    # Coverage = 2× Premium tier price


def calculate_warranty_risk_analysis(
    raw_trials: Dict[str, Any],
    num_units: int,
    standard_price: float,
    premium_markup_pct: float = 0.50,
    warranty_term_years: int = 5,
    funding_model: str = "base",
    coverage_multiple: float = COVERAGE_MULTIPLE,
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

    premium_tier_price       = standard_price * (1.0 + premium_markup_pct)
    warranty_premium_default = standard_price * premium_markup_pct
    coverage_total           = premium_tier_price * coverage_multiple
    coverage_per_unit        = coverage_total / num_units if num_units else 0.0

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
            "premium_tier_price":          round(premium_tier_price, 0),
            "coverage_per_unit":           round(coverage_per_unit, 0),
            "coverage_total":              round(coverage_total, 0),
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
