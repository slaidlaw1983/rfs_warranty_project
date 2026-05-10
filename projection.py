"""
30-year cash flow projection for the RFS stress test web app.

Two functions:
  - build_yearly_schedule(): per-component yearly expenditure with optional life/cost
    multipliers (1.0/1.0 = unstressed; e.g. 0.80/1.30 = stressed median).
  - build_financial_projection(): year-by-year financial table with opening balance,
    contributions, interest, special assessments, closing balance, monthly per-unit.

The schedule logic mirrors stress_test.schedule_track() — same offset preservation
so year-1 events still happen in year 1 — with two additions:
  1. Costs are inflated by (1 + inflation_rate) ** (year-1) at each event
  2. Multipliers can stress life and cost uniformly for a deterministic stressed view
"""

from typing import Any, Dict, List


def _schedule_component_track(life: float, year: float, cost: float,
                               horizon: int, inflation_rate: float) -> List[float]:
    """
    Build year-by-year outflow array (length=horizon) for one track of one component.

    Three cases:
      - life=0 and cost=0: zeros
      - life=0 and cost>0: lump sum in year 1, inflated to year 1
      - life>0 and cost>0: events on a recurring cycle, each inflated to its year

    `year` is the years-until-next-event (1-indexed; year=1 means next year).
    """
    outflows = [0.0] * horizon

    if life <= 0 and cost <= 0:
        return outflows

    if life <= 0 and cost > 0:
        outflows[0] = cost  # year 1, no inflation
        return outflows

    if cost <= 0:
        return outflows

    life = max(1.0, life)
    t = max(1.0, year)
    while t <= horizon:
        idx = int(round(t)) - 1   # year 1 → index 0
        if 0 <= idx < horizon:
            inflated = cost * ((1 + inflation_rate) ** idx)
            outflows[idx] += inflated
        t += life

    return outflows


def build_yearly_schedule(components: List[Dict[str, Any]],
                          horizon: int,
                          inflation_rate: float,
                          life_mult: float = 1.0,
                          cost_mult: float = 1.0) -> Dict[str, List[float]]:
    """
    Per-component yearly expenditure dict. Skips components with zero events
    over the horizon. life_mult and cost_mult applied uniformly to all components
    (e.g. life_mult=0.80, cost_mult=1.30 = median stressed scenario).
    """
    schedule: Dict[str, List[float]] = {}

    for comp in components:
        name = comp["Property Components"]
        yearly = [0.0] * horizon

        # Replacement track
        rep_life = comp["Replacement Desi"] * life_mult
        rep_year = comp["Replacement Year"]
        rep_cost = comp["Replacement Cost"] * cost_mult
        if rep_life > 0 and rep_year > 0:
            # Preserve offset: shocked_year = max(1, original_year - (original_life - shocked_life))
            delta = comp["Replacement Desi"] - rep_life
            adj_year = max(1.0, rep_year - delta)
        else:
            adj_year = rep_year
        rep_outflows = _schedule_component_track(
            rep_life, adj_year, rep_cost, horizon, inflation_rate
        )

        # Maintenance track
        mnt_life = comp["Maintenance Desi"] * life_mult
        mnt_year = comp["Maintenance Year"]
        mnt_cost = comp["Maintenance Cost"] * cost_mult
        if mnt_life > 0 and mnt_year > 0:
            delta = comp["Maintenance Desi"] - mnt_life
            adj_year = max(1.0, mnt_year - delta)
        else:
            adj_year = mnt_year
        mnt_outflows = _schedule_component_track(
            mnt_life, adj_year, mnt_cost, horizon, inflation_rate
        )

        for y in range(horizon):
            yearly[y] = rep_outflows[y] + mnt_outflows[y]

        if any(v > 0 for v in yearly):
            schedule[name] = yearly

    return schedule


def build_financial_projection(yearly_total_outflow: List[float],
                               starting_reserve: float,
                               annual_contribution: float,
                               contribution_growth: float,
                               interest_rate: float,
                               num_units: int,
                               horizon: int) -> List[Dict[str, Any]]:
    """
    Year-by-year financial projection. Returns list of dicts (one per year) with:
      year, expenditure, opening_balance, contribution, return_on_savings,
      closing_balance, monthly_per_unit, special_assessment

    Interest is calculated on (opening_balance + contribution), reflecting that
    new contributions earn interest during the year.
    """
    projection: List[Dict[str, Any]] = []
    balance = float(starting_reserve)
    contribution = float(annual_contribution)
    n_units = max(1, num_units)

    for y in range(horizon):
        opening = balance
        expenditure = yearly_total_outflow[y] if y < len(yearly_total_outflow) else 0.0
        interest = (opening + contribution) * interest_rate
        running = opening + contribution + interest - expenditure

        if running < 0:
            special_assessment = -running
            closing = 0.0
        else:
            special_assessment = 0.0
            closing = running

        projection.append({
            "year": y + 1,
            "expenditure": round(expenditure, 0),
            "opening_balance": round(opening, 0),
            "contribution": round(contribution, 0),
            "return_on_savings": round(interest, 0),
            "closing_balance": round(closing, 0),
            "monthly_per_unit": round(contribution / 12 / n_units, 0),
            "special_assessment": round(special_assessment, 0),
        })

        balance = closing
        contribution *= (1 + contribution_growth)

    return projection
