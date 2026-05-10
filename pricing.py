"""
Pricing calculator for RFS service tiers.

Three tiers (per property type and unit-count bracket):
  - Basic    — RFS only (from pricing table)
  - Standard — RFS + StelorPM (Basic × 1.20)
  - Premium  — Standard + Warranty (Standard × 1.50 total)

Premium tier is fixed at 1.50× Standard. The warranty coverage is then
calculated to satisfy actuarial constraints:

    Warranty premium = Premium − Standard = 0.50 × Standard
    Expected loss    = Warranty premium × Loss ratio target
    Coverage         = Expected loss / Claim rate = 2.0 × Standard
    Deductible       = 10% of Coverage

This keeps the Premium tier price predictable (always 50% over Standard)
while letting Stelor maintain the target loss ratio.
"""

from typing import Dict, Any, List

PROPERTY_TYPES = ["Apartment", "Townhouse", "Commercial", "Bareland"]

UNIT_BRACKETS = [
    {"label": "2-9",     "min": 2,   "max": 9},
    {"label": "10-20",   "min": 10,  "max": 20},
    {"label": "21-35",   "min": 21,  "max": 35},
    {"label": "36-50",   "min": 36,  "max": 50},
    {"label": "51-75",   "min": 51,  "max": 75},
    {"label": "76-100",  "min": 76,  "max": 100},
    {"label": "101-150", "min": 101, "max": 150},
    {"label": "151-250", "min": 151, "max": 250},
    {"label": "250+",    "min": 251, "max": 99999},
]

# Basic prices (RFS only) — from RFS Pricing Tables.xlsx
BASIC_PRICES: Dict[str, List[int]] = {
    "Apartment":  [2400, 3000, 3600, 4500, 5000, 6250, 7000, 8750, 9500],
    "Townhouse":  [1250, 1600, 2400, 3000, 3600, 4500, 5000, 6250, 7000],
    "Commercial": [1250, 2000, 2400, 3000, 3600, 4500, 5000, 6250, 7000],
    "Bareland":   [1250, 1600, 2400, 3000, 3600, 4500, 5000, 6250, 7000],
}

STANDARD_MARKUP    = 1.20    # Standard = Basic × 1.20 (RFS + StelorPM software)
PREMIUM_MARKUP     = 1.50    # Premium  = Standard × 1.50 (project cap)
DEDUCTIBLE_PCT     = 0.10    # 10% of coverage

# Actuarial parameters
EXPECTED_CLAIM_RATE = 0.10   # 10% — 1 in 10 condos files a claim
TARGET_LOSS_RATIO   = 0.40   # 40% — claims payouts ÷ warranty premium


def bracket_index_for_units(num_units: int) -> int:
    """Return the index into UNIT_BRACKETS for the given unit count."""
    for i, b in enumerate(UNIT_BRACKETS):
        if b["min"] <= num_units <= b["max"]:
            return i
    return len(UNIT_BRACKETS) - 1   # 250+ catch-all


def _round_to_50(value: float) -> int:
    return int(round(value / 50) * 50)


def calculate_warranty_terms(standard_price: float,
                              claim_rate: float = EXPECTED_CLAIM_RATE,
                              loss_ratio: float = TARGET_LOSS_RATIO,
                              premium_markup: float = PREMIUM_MARKUP,
                              deductible_pct: float = DEDUCTIBLE_PCT) -> Dict[str, int]:
    """
    Given the Standard tier price, derive all warranty terms.

    Premium tier price is fixed at standard × premium_markup. Coverage is
    solved from the actuarial relationship:
        warranty_premium = premium − standard
        coverage = warranty_premium × loss_ratio / claim_rate

    All values rounded to nearest $50.
    """
    premium_price    = _round_to_50(standard_price * premium_markup)
    warranty_premium = premium_price - standard_price
    expected_loss    = warranty_premium * loss_ratio
    coverage         = _round_to_50(expected_loss / claim_rate)
    deductible       = _round_to_50(coverage * deductible_pct)
    return {
        "premium_price":    premium_price,
        "warranty_premium": warranty_premium,
        "coverage":         coverage,
        "deductible":       deductible,
        "expected_loss":    round(expected_loss),
    }


def get_pricing(property_type: str, num_units: int) -> Dict[str, Any]:
    """
    Return the three-tier pricing structure for a given property.

    Premium = Standard × 1.50. Coverage is solved actuarially so the warranty
    portion of the premium hits the 40% loss-to-premium target at a 10% claim rate.
    """
    if property_type not in BASIC_PRICES:
        raise ValueError(f"Unknown property type: {property_type}")
    if num_units < 2:
        raise ValueError("Number of units must be at least 2")

    idx = bracket_index_for_units(num_units)
    bracket = UNIT_BRACKETS[idx]
    basic_price    = BASIC_PRICES[property_type][idx]
    standard_price = _round_to_50(basic_price * STANDARD_MARKUP)

    w = calculate_warranty_terms(standard_price)

    return {
        "property_type": property_type,
        "num_units":     num_units,
        "bracket":       {"label": bracket["label"], "min": bracket["min"], "max": bracket["max"]},
        "basic": {
            "price":    basic_price,
            "includes": ["Reserve Fund Study (RFS) report"],
        },
        "standard": {
            "price":    standard_price,
            "markup":   f"{STANDARD_MARKUP:.2f}× Basic",
            "includes": [
                "Reserve Fund Study (RFS) report",
                "StelorPM software (property management)",
            ],
        },
        "premium": {
            "price":               w["premium_price"],
            "warranty_premium":    w["warranty_premium"],
            "markup_vs_standard":  f"{PREMIUM_MARKUP:.2f}× Standard",
            "coverage":            w["coverage"],
            "deductible":          w["deductible"],
            "expected_claim_rate": EXPECTED_CLAIM_RATE,
            "target_loss_ratio":   TARGET_LOSS_RATIO,
            "expected_loss":       w["expected_loss"],
            "includes": [
                "Reserve Fund Study (RFS) report",
                "StelorPM software (property management)",
                f"Warranty coverage up to ${w['coverage']:,} per claim",
                f"${w['deductible']:,} deductible per claim",
                "Stress-test analysis included",
            ],
        },
    }


def get_full_pricing_table() -> List[Dict[str, Any]]:
    """Return the complete pricing matrix for display: every type × bracket."""
    rows = []
    for prop_type in PROPERTY_TYPES:
        for i, bracket in enumerate(UNIT_BRACKETS):
            basic    = BASIC_PRICES[prop_type][i]
            standard = _round_to_50(basic * STANDARD_MARKUP)
            w        = calculate_warranty_terms(standard)
            rows.append({
                "property_type":    prop_type,
                "bracket":          bracket["label"],
                "basic":            basic,
                "standard":         standard,
                "warranty_premium": w["warranty_premium"],
                "premium":          w["premium_price"],
                "markup_vs_std":    PREMIUM_MARKUP,
                "coverage":         w["coverage"],
                "deductible":       w["deductible"],
            })
    return rows
