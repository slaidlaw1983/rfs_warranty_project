import csv
import io
import json
import os
import smtplib
import tempfile
from datetime import datetime, timezone
from email.message import EmailMessage

from flask import (Flask, Response, flash, redirect, render_template,
                   request, send_file, session, url_for)
from dotenv import load_dotenv

load_dotenv()

import stress_test as st
from csv_parser import parse_reserve_csv
from pricing import (PROPERTY_TYPES, UNIT_BRACKETS, get_pricing,
                     get_full_pricing_table)
from warranty import calculate_warranty_risk_analysis

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "change-this-in-production")
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB


# ── Password gate ────────────────────────────────────────────────────────────
# If ACCESS_PASSWORD is set in the environment, every request must include
# HTTP Basic credentials with that password (username can be anything). If
# unset, the app is open (intended for local development).
@app.before_request
def _require_password():
    expected = os.environ.get("ACCESS_PASSWORD", "")
    if not expected:
        return  # No password configured — public/dev mode
    auth = request.authorization
    if not auth or auth.password != expected:
        return Response(
            "This site is protected. Enter the access password.",
            401,
            {"WWW-Authenticate": 'Basic realm="RFS Stress Test"'},
        )


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


# ---------------------------------------------------------------------------
# Submission persistence — Google Sheets + email-to-admin
#
# Both helpers fail soft: if env vars aren't configured (or the call errors),
# they log a message to stderr and return False. The user still gets the
# results page either way; this is server-side bookkeeping only.
# ---------------------------------------------------------------------------


def _log_submission_to_sheets(payload: dict) -> bool:
    """Append one row to the configured Google Sheet.

    Required env vars:
      GSHEETS_SHEET_ID                 — the spreadsheet ID
      GSHEETS_SERVICE_ACCOUNT_JSON     — JSON blob of the service account creds
    Optional:
      GSHEETS_WORKSHEET                — worksheet/tab name (default "Submissions")
    """
    sheet_id = os.environ.get("GSHEETS_SHEET_ID")
    creds_json = os.environ.get("GSHEETS_SERVICE_ACCOUNT_JSON")
    if not (sheet_id and creds_json):
        return False

    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        print("[sheets] gspread/google-auth not installed; skipping log")
        return False

    try:
        creds_info = json.loads(creds_json)
    except json.JSONDecodeError as e:
        print(f"[sheets] could not parse GSHEETS_SERVICE_ACCOUNT_JSON: {e}")
        return False

    try:
        scope = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_info(creds_info, scopes=scope)
        client = gspread.authorize(creds)
        sh = client.open_by_key(sheet_id)
        ws_name = os.environ.get("GSHEETS_WORKSHEET", "Submissions")
        try:
            ws = sh.worksheet(ws_name)
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(title=ws_name, rows=1000, cols=30)

        existing_headers = ws.row_values(1) if ws.row_count else []
        if not existing_headers:
            ws.append_row(list(payload.keys()))
        ws.append_row([str(v) for v in payload.values()])
        return True
    except Exception as e:
        print(f"[sheets] log failed: {e}")
        return False


def _email_admin(*, subject: str, body: str, attachments: list) -> bool:
    """Send the admin a notification email with attachments via SMTP.

    Required env vars:
      ADMIN_EMAIL   — recipient
      SMTP_USER     — sending account (e.g. Gmail address)
      SMTP_PASSWORD — Gmail app password (NOT your account password)
    Optional:
      SMTP_HOST     — default smtp.gmail.com
      SMTP_PORT     — default 465 (SSL)

    Each attachment is a dict: {"filename": str, "data": bytes}.
    """
    recipient = os.environ.get("ADMIN_EMAIL")
    sender    = os.environ.get("SMTP_USER")
    password  = os.environ.get("SMTP_PASSWORD")
    if not (recipient and sender and password):
        return False

    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "465"))

    try:
        msg = EmailMessage()
        msg["From"] = sender
        msg["To"] = recipient
        msg["Subject"] = subject
        msg.set_content(body)
        for att in attachments or []:
            msg.add_attachment(
                att["data"],
                maintype="application",
                subtype="octet-stream",
                filename=att["filename"],
            )
        # 10s connection timeout — without this, Render workers hang for 120s
        # if outbound SMTP is blocked, then get SIGKILLed → 500 to the user.
        with smtplib.SMTP_SSL(host, port, timeout=10) as server:
            server.login(sender, password)
            server.send_message(msg)
        return True
    except Exception as e:
        print(f"[email] send failed: {e}")
        return False


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

    # Capture the uploaded bytes for email/audit (parse_reserve_csv consumed the stream)
    try:
        reserve_csv.seek(0)
        user_csv_bytes = reserve_csv.read()
        user_csv_filename = reserve_csv.filename or "reserve_study.csv"
    except Exception:
        user_csv_bytes = b""
        user_csv_filename = "reserve_study.csv"

    property_name = (request.form.get("property_name") or "").strip()
    contact_name  = (request.form.get("contact_name")  or "").strip()
    contact_email = (request.form.get("contact_email") or "").strip()

    try:
        num_units                  = int(request.form["num_units"])
        starting_reserve           = float(request.form["starting_reserve"])
        base_contribution          = float(request.form["base_contribution"])
        full_funding_contribution  = float(request.form["full_funding_contribution"])
    except (KeyError, ValueError):
        flash("Please enter valid numbers for all property parameters.")
        return redirect(url_for("index"))

    try:
        inflation_rate = float(request.form.get("inflation_rate", 0.03))
        interest_rate  = float(request.form.get("interest_rate",  0.025))
    except ValueError:
        flash("Please enter valid numbers for inflation and interest rates.")
        return redirect(url_for("index"))

    # Locked: 1000 trials, 30-year horizon, σ=0.30 cost (lognormal) + life (normal),
    # both centered on the engineer's P50 (mean multiplier = 1.0).
    summary = st.run_stress_test(
        components=components,
        num_trials=1000,
        starting_reserve=starting_reserve,
        base_contribution=base_contribution,
        full_funding_contribution=full_funding_contribution,
        inflation_rate=inflation_rate,
        interest_rate=interest_rate,
        num_units=num_units,
        horizon=30,
    )
    summary["components_loaded"] = len(components)

    deterioration = calculate_annual_deterioration(components, num_units)
    det_total = deterioration["total_annual"]
    deterioration["funding_ratio"] = {
        "base": round(base_contribution / det_total, 3) if det_total > 0 else None,
        "full": round(full_funding_contribution / det_total, 3) if det_total > 0 else None,
    }
    summary["deterioration"] = deterioration

    # Warranty risk analysis × 2 (one per funding model)
    property_type = request.form.get("property_type", "Apartment").strip() or "Apartment"
    try:
        from pricing import BASIC_PRICES, STANDARD_MARKUP, bracket_index_for_units, _round_to_50
        idx = bracket_index_for_units(num_units)
        if property_type in BASIC_PRICES:
            basic_price = BASIC_PRICES[property_type][idx]
        else:
            basic_price = BASIC_PRICES["Apartment"][idx]
        standard_price = _round_to_50(basic_price * STANDARD_MARKUP)

        def _warranty(raw_trials_dict):
            return calculate_warranty_risk_analysis(
                raw_trials={"assessment_yr_1_5": raw_trials_dict["assessment_yr_1_5"]},
                num_units=num_units,
                standard_price=standard_price,
                coverage_per_unit=500.0,
                premium_markup_pct=0.50,
                warranty_term_years=5,
                target_loss_ratio=0.50,
            )

        wa_base = _warranty(summary["raw_trials_base"])
        wa_full = _warranty(summary["raw_trials_full"])
        for wa in (wa_base, wa_full):
            if wa:
                wa["property_type_used"] = property_type
                wa["standard_price_used"] = standard_price
                wa["methodology_note"] = (
                    "Probability computed from 1000 Monte Carlo trials. "
                    "Each component gets independent random cost (lognormal, mean=1.0) "
                    "and life (normal, mean=1.0) multipliers per trial; σ=0.30 for both."
                )
        summary["warranty_analysis"] = {"base": wa_base, "full": wa_full}
    except Exception as e:
        print(f"Warranty analysis failed: {e}")
        summary["warranty_analysis"] = {"base": None, "full": None}

    # Strip raw_trials before persisting — large, not used by the template
    summary.pop("raw_trials_base", None)
    summary.pop("raw_trials_full", None)

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
    json.dump(summary, tmp)
    tmp.close()
    session["summary_path"] = tmp.name

    # ── Submission logging + email-to-admin (fail-soft) ──
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")

    log_payload = {
        "timestamp_utc":             timestamp,
        "property_name":             property_name,
        "contact_name":              contact_name,
        "contact_email":             contact_email,
        "property_type":             property_type,
        "num_units":                 num_units,
        "starting_reserve":          starting_reserve,
        "base_contribution":         base_contribution,
        "full_funding_contribution": full_funding_contribution,
        "inflation_rate":            inflation_rate,
        "interest_rate":             interest_rate,
        "components_loaded":         len(components),
        "user_csv_filename":         user_csv_filename,
        "base_p_sa_1_5":             summary["base"]["prob_assessment"]["yr_1_5"],
        "base_p_sa_1_10":            summary["base"]["prob_assessment"]["yr_1_10"],
        "base_p_sa_30":              summary["base"]["prob_assessment"]["yr_30"],
        "full_p_sa_1_5":             summary["full"]["prob_assessment"]["yr_1_5"],
        "full_p_sa_1_10":            summary["full"]["prob_assessment"]["yr_1_10"],
        "full_p_sa_30":              summary["full"]["prob_assessment"]["yr_30"],
        "base_median_total_sa":      summary["base"]["median_total_assessment"]["total"],
        "full_median_total_sa":      summary["full"]["median_total_assessment"]["total"],
    }
    sheets_ok = _log_submission_to_sheets(log_payload)

    body = (
        f"Reserve Study Stress Test — new submission\n\n"
        f"Timestamp:    {timestamp}\n"
        f"Property:     {property_name or '(unspecified)'}\n"
        f"Contact:      {contact_name or '(unspecified)'} <{contact_email or 'no-email'}>\n"
        f"Property Type:{property_type}\n\n"
        f"Inputs:\n"
        f"  {num_units} units, ${starting_reserve:,.0f} starting reserve\n"
        f"  Base contribution:        ${base_contribution:,.0f}/yr\n"
        f"  Full funding contribution:${full_funding_contribution:,.0f}/yr\n"
        f"  Inflation: {inflation_rate:.1%}   Interest: {interest_rate:.1%}\n\n"
        f"Probability of special assessment (1-5yr / 1-10yr / 30yr):\n"
        f"  Base:         {log_payload['base_p_sa_1_5']:.1%} / "
        f"{log_payload['base_p_sa_1_10']:.1%} / {log_payload['base_p_sa_30']:.1%}\n"
        f"  Full funding: {log_payload['full_p_sa_1_5']:.1%} / "
        f"{log_payload['full_p_sa_1_10']:.1%} / {log_payload['full_p_sa_30']:.1%}\n\n"
        f"Median total SA over 30yr:\n"
        f"  Base:         ${log_payload['base_median_total_sa']:,.0f}\n"
        f"  Full funding: ${log_payload['full_median_total_sa']:,.0f}\n\n"
        f"Components loaded: {len(components)}\n"
        f"Sheet logged: {'yes' if sheets_ok else 'no (not configured or failed)'}\n"
    )
    attachments = [
        {"filename": user_csv_filename, "data": user_csv_bytes},
        {"filename": "stress_test_results.json",
         "data": json.dumps(summary, indent=2).encode("utf-8")},
    ]
    _email_admin(
        subject=f"[RFS Stress Test] {property_name or 'New submission'}"
                + (f" — {contact_email}" if contact_email else ""),
        body=body,
        attachments=attachments,
    )

    return render_template("results.html", summary=summary)


@app.route("/download-csv")
def download_csv():
    path = session.get("summary_path")
    if not path or not os.path.exists(path):
        return "No results available. Please run a stress test first.", 404

    with open(path) as f:
        summary = json.load(f)

    rows = []
    for model in ("base", "full"):
        m = summary.get(model, {}) or {}
        prob = m.get("prob_assessment", {})
        med  = m.get("median_total_assessment", {})
        rows.append({
            "funding_model":            model,
            "p_sa_yr_1_5":              round(prob.get("yr_1_5", 0.0), 4),
            "p_sa_yr_1_10":             round(prob.get("yr_1_10", 0.0), 4),
            "p_sa_yr_30":               round(prob.get("yr_30", 0.0), 4),
            "median_total_assessment":  round(med.get("total", 0.0), 0),
            "median_per_unit":          round(med.get("per_unit", 0.0), 0),
            "median_n_assessment_yrs":  round(m.get("median_n_assessment_years", 0.0), 1),
            "funding_ratio":            (summary.get("deterioration", {})
                                                 .get("funding_ratio", {})
                                                 .get(model)),
        })

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
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
