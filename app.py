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

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "change-this-in-production")
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB


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
