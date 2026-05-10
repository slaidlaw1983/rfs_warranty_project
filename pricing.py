"""
Pricing calculator for RFS service tiers.

Three tiers (per property type and unit-count bracket):
  - Basic    — RFS only (from pricing table)
  - Standard — RFS + StelorPM (Basic × 1.20)
  - Premium  — Standard + Warranty (actuarial pricing)

Warranty premium is calculated to maintain a target loss-to-premium ratio:
    Warranty premium = Expected loss / target loss ratio
                     = (Coverage × Claim rate) / Loss ratio

Premium tier price = Standard + Warranty premium.

Default actuarial parameters:
  - Claim rate:       10%  (1 in 10 condos files a claim)
  - Loss ratio target: 40% (40 cents of every premium dollar goes to claims)

Warranty coverage and deductibles scale with property size.
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

STANDARD_MARKUP = 1.20  # Standard = Basic × 1.20 (RFS + StelorPM software)

# Warranty coverage by bracket (Premium tier)
WARRANTY_COVERAGE = [
    {"coverage": 10000,  "deductible": 1000},
    {"coverage": 20000,  "deductible": 2000},
    {"coverage": 30000,  "deductible": 3000},
    {"coverage": 40000,  "deductible": 4000},
    {"coverage": 50000,  "deductible": 5000},
    {"coverage": 60000,  "deductible": 6000},
    {"coverage": 70000,  "deductible": 7000},
    {"coverage": 80000,  "deductible": 8000},
    {"coverage": 100000, "deductible": 10000},
]

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


def calculate_warranty_premium(coverage: float,
                                claim_rate: float = EXPECTED_CLAIM_RATE,
                                loss_ratio: float = TARGET_LOSS_RATIO) -> int:
    """
    Actuarial warranty premium for a given coverage amount.

    Warranty premium = Expected loss / Target loss ratio
                     = (Coverage × Claim rate) / Loss ratio

    Rounded to nearest $50.
    """
    expected_loss = coverage * claim_rate
    premium = expected_loss / loss_ratio
    return _round_to_50(premium)


def get_pricing(property_type: str, num_units: int) -> Dict[str, Any]:
    """
    Return the three-tier pricing structure for a given property.

    Output structure:
      {
        "property_type": ..., "num_units": ...,
        "bracket": {"label": ..., "min": ..., "max": ...},
        "basic":    {"price": ..., "includes": [...]},
        "standard": {"price": ..., "markup": "1.20x Basic", "includes": [...]},
        "premium":  {"price": ..., "markup_vs_standard": "X.XXx",
                     "warranty_premium": ..., "coverage": ..., "deductible": ...,
                     "expected_claim_rate": ..., "target_loss_ratio": ...,
                     "expected_loss": ...,
                     "includes": [...]}
      }
    """
    if property_type not in BASIC_PRICES:
        raise ValueError(f"Unknown property type: {property_type}")
    if num_units < 2:
        raise ValueError("Number of units must be at least 2")

    idx = bracket_index_for_units(num_units)
    bracket = UNIT_BRACKETS[idx]
    basic_price    = BASIC_PRICES[property_type][idx]
    standard_price = _round_to_50(basic_price * STANDARD_MARKUP)

    coverage   = WARRANTY_COVERAGE[idx]["coverage"]
    deductible = WARRANTY_COVERAGE[idx]["deductible"]
    warranty_premium = calculate_warranty_premium(coverage)
    expected_loss    = coverage * EXPECTED_CLAIM_RATE
    premium_price    = standard_price + warranty_premium
    markup_vs_std    = premium_price / standard_price if standard_price else 0.0

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
            "price":               premium_price,
            "warranty_premium":    warranty_premium,
            "markup_vs_standard":  f"{markup_vs_std:.2f}× Standard",
            "coverage":            coverage,
            "deductible":          deductible,
            "expected_claim_rate": EXPECTED_CLAIM_RATE,
            "target_loss_ratio":   TARGET_LOSS_RATIO,
            "expected_loss":       expected_loss,
            "includes": [
                "Reserve Fund Study (RFS) report",
                "StelorPM software (property management)",
                f"Warranty coverage up to ${coverage:,} per claim",
                f"${deductible:,} deductible per claim",
                "Stress-test analysis included",
            ],
        },
    }


def get_full_pricing_table() -> List[Dict[str, Any]]:
    """Return the complete pricing matrix for display: every type × bracket."""
    rows = []
    for prop_type in PROPERTY_TYPES:
        for i, bracket in enumerate(UNIT_BRACKETS):
            basic            = BASIC_PRICES[prop_type][i]
            standard         = _round_to_50(basic * STANDARD_MARKUP)
            coverage         = WARRANTY_COVERAGE[i]["coverage"]
            warranty_premium = calculate_warranty_premium(coverage)
            premium          = standard + warranty_premium
            rows.append({
                "property_type":    prop_type,
                "bracket":          bracket["label"],
                "basic":            basic,
                "standard":         standard,
                "warranty_premium": warranty_premium,
                "premium":          premium,
                "markup_vs_std":    premium / standard if standard else 0.0,
                "coverage":         coverage,
                "deductible":       WARRANTY_COVERAGE[i]["deductible"],
            })
    return rows
