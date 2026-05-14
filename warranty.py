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
