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


@app.route("/")
def index():
    return render_template("index.html")


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
    for view in ("kpis_total", "kpis_per_unit"):
        for kpi, stats in summary[view].items():
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
