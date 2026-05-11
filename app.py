import csv
import io
import json
import os
import smtplib
import tempfile
from datetime import datetime, timezone
from email.message import EmailMessage

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
    Use UNBIASED Monte Carlo trial outcomes to evaluate warranty risk.

    Premium model: ONE-TIME premium paid at time of RFS, covers the full
    warranty term. Premium = standard_price × premium_markup_pct.

    Payout model: lump sum (coverage − deductible) on any special assessment
    occurring within the warranty term. Probability of payout = P(any assessment
    in the term) from the unbiased Monte Carlo (mean cost/life multipliers = 1.0).
    """
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
    # Warranty premium is paid ONCE upfront and covers the entire term.
    warranty_premium = standard_price * premium_markup_pct
    deductible = coverage * 0.10

    payout_per_claim   = max(0.0, coverage - deductible)
    claim_count        = sum(1 for t in trials if t > 0)
    claim_probability  = claim_count / len(trials)
    expected_payout    = payout_per_claim * claim_probability

    expected_assessment_uncapped = sum(trials) / len(trials)

    actual_loss_ratio = expected_payout / warranty_premium if warranty_premium > 0 else 0.0

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

    # Eligibility decision
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

    # Custom quote — what one-time premium would hit the target loss ratio?
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


def _kpi_summary_csv_bytes(summary: dict) -> bytes:
    """Flatten the KPI views into a CSV for email/download."""
    rows = []
    for view in ("kpis_total", "kpis_per_unit", "kpis_balance_total",
                 "kpis_balance_per_unit", "assessment_frequency"):
        for kpi, stats in summary.get(view, {}).items():
            rows.append({
                "view": view,
                "kpi": kpi,
                **{k: round(v, 2) for k, v in stats.items()},
            })
    if not rows:
        return b""
    buf = io.StringIO()
    fieldnames = ["view", "kpi"] + sorted({k for r in rows for k in r if k not in ("view", "kpi")})
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    for r in rows:
        writer.writerow(r)
    return buf.getvalue().encode("utf-8")


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

    # Capture the user's uploaded CSV bytes for later (email attachment, audit
    # trail). parse_reserve_csv consumed the stream, so seek back to the start.
    try:
        reserve_csv.seek(0)
        user_csv_bytes = reserve_csv.read()
        user_csv_filename = reserve_csv.filename or "reserve_study.csv"
    except Exception:
        user_csv_bytes = b""
        user_csv_filename = "reserve_study.csv"

    # Submitter identity (optional fields — added to the form for tracking who
    # ran the analysis; safe to leave blank if you haven't added them to index.html yet)
    property_name = (request.form.get("property_name")  or "").strip()
    contact_name  = (request.form.get("contact_name")   or "").strip()
    contact_email = (request.form.get("contact_email")  or "").strip()

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

        # Warranty analysis uses the SAME Monte Carlo as the main results.
        # With the unified methodology (means=1.0, σ≈0.30 for both), the
        # main MC's raw_trials directly give P(special assessment) — no
        # separate unbiased pass needed.
        summary["warranty_analysis"] = calculate_warranty_risk_analysis(
            raw_trials=summary.get("raw_trials", {}),
            num_units=num_units,
            standard_price=standard_price,
            coverage_per_unit=500.0,
            premium_markup_pct=0.50,
            warranty_term_years=5,
            target_loss_ratio=0.50,
        )
        if summary["warranty_analysis"]:
            summary["warranty_analysis"]["property_type_used"] = property_type
            summary["warranty_analysis"]["standard_price_used"] = standard_price
            summary["warranty_analysis"]["methodology_note"] = (
                f"Probability computed from {num_trials} Monte Carlo trials. "
                f"Each component gets independent random cost (lognormal, mean=1.0) "
                f"and life (normal, mean=1.0) multipliers per trial; "
                f"sigmas: life σ={life_shock_sigma}, cost σ={cost_shock_sigma}."
            )
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

    # ---- Submission logging + email-to-admin ----
    # Both calls fail-soft if env vars aren't configured; the user still
    # sees the results page regardless.
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _kpi(view: str, name: str, stat: str = "p50"):
        try:
            return summary[view][name][stat]
        except (KeyError, TypeError):
            return ""

    log_payload = {
        "timestamp_utc":             timestamp,
        "property_name":             property_name,
        "contact_name":              contact_name,
        "contact_email":             contact_email,
        "property_type":             property_type,
        "num_units":                 num_units,
        "starting_reserve":          starting_reserve,
        "annual_contribution":       annual_contribution,
        "horizon":                   horizon,
        "num_trials":                num_trials,
        "life_shock_mean":           life_shock_mean,
        "life_shock_sigma":          life_shock_sigma,
        "cost_shock_mean":           cost_shock_mean,
        "cost_shock_sigma":          cost_shock_sigma,
        "inflation_rate":            inflation_rate,
        "interest_rate":             interest_rate,
        "contribution_growth":       contribution_growth,
        "components_loaded":         len(components),
        "user_csv_filename":         user_csv_filename,
        "median_yr_1_5":             _kpi("kpis_total", "assessment_yr_1_5"),
        "median_yr_6_10":            _kpi("kpis_total", "assessment_yr_6_10"),
        "median_total_assessment":   _kpi("kpis_total", "assessment_total"),
        "median_per_unit_total":     _kpi("kpis_per_unit", "assessment_total"),
    }
    sheets_ok = _log_submission_to_sheets(log_payload)

    body = (
        f"Reserve Study Stress Test — new submission\n\n"
        f"Timestamp:    {timestamp}\n"
        f"Property:     {property_name or '(unspecified)'}\n"
        f"Contact:      {contact_name or '(unspecified)'} <{contact_email or 'no-email'}>\n"
        f"Property Type:{property_type}\n\n"
        f"Inputs:\n"
        f"  {num_units} units, ${starting_reserve:,.0f} starting reserve, "
        f"${annual_contribution:,.0f}/yr contribution\n"
        f"  Horizon: {horizon} yrs   Trials: {num_trials:,}\n"
        f"  Life shock:   N({life_shock_mean:.2f}, {life_shock_sigma:.2f})\n"
        f"  Cost shock:   Lognorm(mean={cost_shock_mean:.2f}, sigma={cost_shock_sigma:.2f})\n"
        f"  Inflation: {inflation_rate:.1%}   Interest: {interest_rate:.1%}   "
        f"Contribution growth: {contribution_growth:.1%}\n\n"
        f"Median KPIs:\n"
        f"  Yr 1-5 assessment:    {log_payload['median_yr_1_5']}\n"
        f"  Yr 6-10 assessment:   {log_payload['median_yr_6_10']}\n"
        f"  Total horizon:        {log_payload['median_total_assessment']}\n"
        f"  Per-unit total:       {log_payload['median_per_unit_total']}\n\n"
        f"Components loaded: {len(components)}\n"
        f"Sheet logged: {'yes' if sheets_ok else 'no (not configured or failed)'}\n"
    )
    attachments = [
        {"filename": user_csv_filename, "data": user_csv_bytes},
        {"filename": "stress_test_kpis.csv", "data": _kpi_summary_csv_bytes(summary)},
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
