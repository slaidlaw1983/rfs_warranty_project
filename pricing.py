"""
Pricing calculator for RFS service tiers.

Three tiers (per property type and unit-count bracket):
  - Basic    — RFS only (from pricing table)
  - Standard — RFS + StelorPM (Basic × 1.20)
  - Premium  — Standard + Warranty (Standard × bracket markup, capped at 1.50)

The Premium markup varies by bracket (1.30 for smallest, ramping to 1.50 for
largest) to reflect higher relative risk and complexity at larger properties,
while keeping the price ≤ 50% above Standard per project requirements.

Warranty coverage and deductibles scale with property size.
"""

from typing import Dict, Any, List

PROPERTY_TYPES = ["Apartment", "Townhouse", "Commercial", "Bareland"]

UNIT_BRACKETS = [
    {"label": "2-9",     "min": 2,   "max": 9,     "premium_markup": 1.30},
    {"label": "10-20",   "min": 10,  "max": 20,    "premium_markup": 1.35},
    {"label": "21-35",   "min": 21,  "max": 35,    "premium_markup": 1.40},
    {"label": "36-50",   "min": 36,  "max": 50,    "premium_markup": 1.43},
    {"label": "51-75",   "min": 51,  "max": 75,    "premium_markup": 1.45},
    {"label": "76-100",  "min": 76,  "max": 100,   "premium_markup": 1.47},
    {"label": "101-150", "min": 101, "max": 150,   "premium_markup": 1.48},
    {"label": "151-250", "min": 151, "max": 250,   "premium_markup": 1.49},
    {"label": "250+",    "min": 251, "max": 99999, "premium_markup": 1.50},
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

EXPECTED_CLAIM_RATE = 0.0625   # 6.25% per Warranty Coverage Premium Loss Outline


def bracket_index_for_units(num_units: int) -> int:
    """Return the index into UNIT_BRACKETS for the given unit count."""
    for i, b in enumerate(UNIT_BRACKETS):
        if b["min"] <= num_units <= b["max"]:
            return i
    return len(UNIT_BRACKETS) - 1   # 250+ catch-all


def _round_to_50(value: float) -> int:
    return int(round(value / 50) * 50)


def get_pricing(property_type: str, num_units: int) -> Dict[str, Any]:
    """
    Return the three-tier pricing structure for a given property.

    Output structure:
      {
        "property_type": ...,
        "num_units": ...,
        "bracket": {"label": ..., "min": ..., "max": ...},
        "basic":    {"price": ..., "includes": [...]},
        "standard": {"price": ..., "markup": "1.20x Basic", "includes": [...]},
        "premium":  {"price": ..., "markup": "1.XXx Standard",
                     "coverage": ..., "deductible": ...,
                     "expected_claim_rate": ..., "includes": [...]}
      }
    """
    if property_type not in BASIC_PRICES:
        raise ValueError(f"Unknown property type: {property_type}")
    if num_units < 2:
        raise ValueError("Number of units must be at least 2")

    idx = bracket_index_for_units(num_units)
    bracket = UNIT_BRACKETS[idx]
    basic_price = BASIC_PRICES[property_type][idx]
    standard_price = _round_to_50(basic_price * STANDARD_MARKUP)
    premium_markup = bracket["premium_markup"]
    premium_price = _round_to_50(standard_price * premium_markup)

    coverage = WARRANTY_COVERAGE[idx]

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
            "price":              premium_price,
            "markup":             f"{premium_markup:.2f}× Standard",
            "coverage":           coverage["coverage"],
            "deductible":         coverage["deductible"],
            "expected_claim_rate": EXPECTED_CLAIM_RATE,
            "includes": [
                "Reserve Fund Study (RFS) report",
                "StelorPM software (property management)",
                f"Warranty coverage up to ${coverage['coverage']:,} per claim",
                f"${coverage['deductible']:,} deductible per claim",
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
            premium  = _round_to_50(standard * bracket["premium_markup"])
            rows.append({
                "property_type":   prop_type,
                "bracket":         bracket["label"],
                "basic":           basic,
                "standard":        standard,
                "premium":         premium,
                "premium_markup":  bracket["premium_markup"],
                "coverage":        WARRANTY_COVERAGE[i]["coverage"],
                "deductible":      WARRANTY_COVERAGE[i]["deductible"],
            })
    return rows
