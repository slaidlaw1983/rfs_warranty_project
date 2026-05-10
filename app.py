import csv
import io
import json
import os
import tempfile

from flask import (Flask, flash, redirect, render_template,
                   request, send_file, session, url_for)
from dotenv import load_dotenv

load_dotenv()

import stress_test as st
from csv_parser import parse_reserve_csv
from projection import build_yearly_schedule, build_financial_projection
from pricing import (PROPERTY_TYPES, UNIT_BRACKETS, get_pricing,
                     get_full_pricing_table)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "change-this-in-production")
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB


def calculate_warranty_risk_analysis(raw_trials: dict,
                                      num_units: int,
                                      standard_price: float,
                                      coverage_per_unit: float = 500.0,
                                      premium_markup_pct: float = 0.50,
                                      warranty_term_years: int = 5,
                                      target_loss_ratio: float = 0.50) -> dict:
    """
    Use the stress test trial outcomes to evaluate warranty risk at given target terms.

    For each trial, compute the warranty payout as min(trial_assessment, coverage_cap).
    Average across trials to get expected payout. Compare to total premium over the term.
    """
    import statistics

    # Pick the trial array matching the warranty term
    if warranty_term_years == 5:
        trials = raw_trials.get("assessment_yr_1_5", [])
    elif warranty_term_years == 10:
        trials = raw_trials.get("assessment_yr_1_10", [])
    else:
        trials = raw_trials.get("assessment_yr_1_5", [])

    if not trials:
        return None

    coverage = coverage_per_unit * num_units
    annual_premium = standard_price * premium_markup_pct
    term_premium = annual_premium * warranty_term_years
    deductible = coverage * 0.10

    # Capped expected payout — per trial, payout = min(assessment - deductible, coverage)
    # Floor at 0 (no negative payout)
    capped_payouts = [max(0.0, min(t - deductible, coverage)) if t > 0 else 0.0 for t in trials]
    expected_payout = sum(capped_payouts) / len(capped_payouts)
    expected_payout_uncapped = sum(trials) / len(trials)

    claim_count = sum(1 for t in trials if t > 0)
    claim_probability = claim_count / len(trials)
    payouts_when_claim = [p for p, t in zip(capped_payouts, trials) if t > 0 and p > 0]
    mean_payout_when_claim = (sum(payouts_when_claim) / len(payouts_when_claim)
                              if payouts_when_claim else 0.0)

    actual_loss_ratio = expected_payout / term_premium if term_premium > 0 else 0.0

    # Verdict colour
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

    return {
        "target_terms": {
            "warranty_term_years":  warranty_term_years,
            "coverage_per_unit":    coverage_per_unit,
            "coverage_total":       coverage,
            "deductible":           deductible,
            "premium_markup_pct":   premium_markup_pct,
            "annual_premium":       annual_premium,
            "term_premium":         term_premium,
            "target_loss_ratio":    target_loss_ratio,
        },
        "stress_results": {
            "n_trials":              len(trials),
            "claim_probability":     claim_probability,
            "expected_payout_capped":   round(expected_payout, 0),
            "expected_payout_uncapped": round(expected_payout_uncapped, 0),
            "mean_payout_when_claim":   round(mean_payout_when_claim, 0),
        },
        "actual_loss_ratio": round(actual_loss_ratio, 4),
        "verdict":           verdict,
        "verdict_label":     verdict_label,
    }


def calculate_annual_deterioration(components: list, num_units: int) -> dict:
    """
    Calculate theoretical annual deterioration for each component and the property total.

    Annual deterioration per component = replacement_cost / design_life
                                       + maintenance_cost / maintenance_life

    This is the unstressed (base) rate — what the property depreciates by each year
    based on the reserve study's own cost and life assumptions.
    """
    rows = []
    for c in components:
        annual = 0.0
        if c["Replacement Desi"] > 0 and c["Replacement Cost"] > 0:
            annual += c["Replacement Cost"] / c["Replacement Desi"]
        if c["Maintenance Desi"] > 0 and c["Maintenance Cost"] > 0:
            annual += c["Maintenance Cost"] / c["Maintenance Desi"]
        if annual > 0:
            rows.append({"name": c["Property Components"], "annual": round(annual, 2)})

    rows.sort(key=lambda x: x["annual"], reverse=True)
    total = sum(r["annual"] for r in rows)

    for r in rows:
        r["pct"] = round(r["annual"] / total * 100, 1) if total > 0 else 0.0

    return {
        "total_annual":   round(total, 2),
        "per_unit_annual": round(total / max(1, num_units), 2),
        "components":     rows,
    }


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/pricing")
def pricing():
    """Pricing calculator. If property_type and num_units provided, show quote."""
    quote = None
    error = None
    pt = request.args.get("property_type", "").strip()
    nu = request.args.get("num_units", "").strip()
    if pt and nu:
        try:
            quote = get_pricing(pt, int(nu))
        except ValueError as e:
            error = str(e)
    return render_template(
        "pricing.html",
        quote=quote,
        error=error,
        property_types=PROPERTY_TYPES,
        full_table=get_full_pricing_table(),
        selected_type=pt,
        selected_units=nu,
    )


@app.route("/download-template")
def download_template():
    return send_file(
        os.path.join(os.path.dirname(__file__), "static", "reserve_study_template.csv"),
        mimetype="text/csv",
        as_attachment=True,
        download_name="reserve_study_template.csv",
    )


@app.route("/run", methods=["POST"])
def run():
    reserve_csv = request.files.get("reserve_csv")
    if not reserve_csv or not reserve_csv.filename:
        flash("Please upload a reserve study CSV.")
        return redirect(url_for("index"))

    components = parse_reserve_csv(reserve_csv)
    if not components:
        flash("No valid components found. Check that the CSV has the required columns and at least one budgeted component.")
        return redirect(url_for("index"))

    try:
        num_units           = int(request.form["num_units"])
        starting_reserve    = float(request.form["starting_reserve"])
        annual_contribution = float(request.form["annual_contribution"])
    except (KeyError, ValueError):
        flash("Please enter valid numbers for all property parameters.")
        return redirect(url_for("index"))

    try:
        life_shock_mean  = float(request.form.get("life_shock_mean",  0.80))
        life_shock_sigma = float(request.form.get("life_shock_sigma", 0.10))
        cost_shock_mean  = float(request.form.get("cost_shock_mean",  1.30))
        cost_shock_sigma = float(request.form.get("cost_shock_sigma", 0.15))
        num_trials       = int(request.form.get("num_trials",         2000))
        horizon          = int(request.form.get("horizon",            30))
        inflation_rate       = float(request.form.get("inflation_rate",       0.03))
        interest_rate        = float(request.form.get("interest_rate",        0.025))
        contribution_growth  = float(request.form.get("contribution_growth",  0.04))
    except ValueError:
        flash("Please enter valid numbers for all shock parameters.")
        return redirect(url_for("index"))

    summary = st.run_stress_test(
        components=components,
        num_trials=num_trials,
        starting_reserve=starting_reserve,
        annual_contribution=annual_contribution,
        num_units=num_units,
        horizon=horizon,
        life_shock_mean=life_shock_mean,
        life_shock_sigma=life_shock_sigma,
        cost_shock_mean=cost_shock_mean,
        cost_shock_sigma=cost_shock_sigma,
    )
    summary["components_loaded"] = len(components)
    summary["deterioration"] = calculate_annual_deterioration(components, num_units)

    # 30-year cash flow projection — unstressed and stressed
    def _build_proj(life_mult: float, cost_mult: float):
        sched = build_yearly_schedule(
            components, horizon, inflation_rate,
            life_mult=life_mult, cost_mult=cost_mult,
        )
        total_outflow = [
            sum(sched[c][y] for c in sched) for y in range(horizon)
        ]
        fin = build_financial_projection(
            total_outflow,
            starting_reserve=starting_reserve,
            annual_contribution=annual_contribution,
            contribution_growth=contribution_growth,
            interest_rate=interest_rate,
            num_units=num_units,
            horizon=horizon,
        )
        return {"schedule": sched, "financials": fin}

    summary["projection"] = {
        "unstressed": _build_proj(1.0, 1.0),
        "stressed":   _build_proj(life_shock_mean, cost_shock_mean),
        "params": {
            "inflation_rate":      inflation_rate,
            "interest_rate":       interest_rate,
            "contribution_growth": contribution_growth,
        },
    }

    # Warranty risk analysis — uses the property's Standard price (defaults to
    # Apartment pricing as a baseline; the user can pass property_type to override)
    property_type = request.form.get("property_type", "Apartment").strip() or "Apartment"
    try:
        from pricing import BASIC_PRICES, STANDARD_MARKUP, bracket_index_for_units, _round_to_50
        idx = bracket_index_for_units(num_units)
        if property_type in BASIC_PRICES:
            basic_price = BASIC_PRICES[property_type][idx]
        else:
            basic_price = BASIC_PRICES["Apartment"][idx]
        standard_price = _round_to_50(basic_price * STANDARD_MARKUP)

        summary["warranty_analysis"] = calculate_warranty_risk_analysis(
            raw_trials=summary.get("raw_trials", {}),
            num_units=num_units,
            standard_price=standard_price,
            coverage_per_unit=500.0,
            premium_markup_pct=0.50,
            warranty_term_years=5,
            target_loss_ratio=0.50,
        )
        summary["warranty_analysis"]["property_type_used"] = property_type
        summary["warranty_analysis"]["standard_price_used"] = standard_price
    except Exception as e:
        print(f"Warranty analysis failed: {e}")
        summary["warranty_analysis"] = None

    # Strip raw_trials from the summary before storing — large, only needed during analysis
    summary.pop("raw_trials", None)
    # Funding ratio: contribution vs total annual deterioration
    det_total = summary["deterioration"]["total_annual"]
    summary["deterioration"]["funding_ratio"] = round(
        annual_contribution / det_total, 3
    ) if det_total > 0 else None

    # Persist for download — write to tempfile, store path in session
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
    json.dump(summary, tmp)
    tmp.close()
    session["summary_path"] = tmp.name

    return render_template("results.html", summary=summary)


@app.route("/download-csv")
def download_csv():
    path = session.get("summary_path")
    if not path or not os.path.exists(path):
        return "No results available. Please run a stress test first.", 404

    with open(path) as f:
        summary = json.load(f)

    rows = []
    for view in ("kpis_total", "kpis_per_unit", "kpis_balance_total", "kpis_balance_per_unit", "assessment_frequency"):
        for kpi, stats in summary.get(view, {}).items():
            rows.append({
                "view": view,
                "kpi": kpi,
                **{k: round(v, 2) for k, v in stats.items()},
            })

    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=["view", "kpi", "mean", "p25", "p50", "p75", "p90", "p95"],
    )
    writer.writeheader()
    writer.writerows(rows)

    return send_file(
        io.BytesIO(buf.getvalue().encode()),
        mimetype="text/csv",
        as_attachment=True,
        download_name="stress_test_kpis.csv",
    )


if __name__ == "__main__":
    app.run(debug=True, port=8081)
