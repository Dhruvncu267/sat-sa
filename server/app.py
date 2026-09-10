"""
SAT-SA web application (Firebase edition)
=============================================
The rule engine, ML model, and scoring logic below are UNCHANGED from the
original prototype (engine/*.py, ml/anomaly_model.py) -- this file only
changed how supervisors log in and where data is stored.

Auth model: this app never sees or checks a password itself. Login happens
entirely in the browser via the Firebase Auth JS SDK, which talks directly
to Google's servers. The browser then gets a short-lived ID token and sends
it as `Authorization: Bearer <token>` on every API call; this backend's
only job is to verify that token (firebase_setup.verify_id_token) and check
the matching supervisors/{uid} Firestore profile is marked active, before
doing anything.

Endpoints:
  GET  /                              -> the dashboard shell (public HTML;
                                          no data -- app.js redirects to
                                          /login.html if not signed in)
  GET  /login.html                    -> the login screen
  GET  /api/analysis                  -> current live analysis of every
                                          organization (protected)
  GET  /api/template                  -> downloadable example CSV (protected)
  POST /api/organizations             -> add one organization (protected)
  DELETE /api/organizations/<id>      -> delete one organization + its
                                          saved assessment history (protected)
  GET  /api/assessment/<id>/<date>    -> a saved dated snapshot for the
                                          calendar/date selector (protected)
  GET  /api/assessment-dates/<id>     -> which dates have a saved snapshot
                                          for this organization (protected)
  GET  /api/entity/<id>/report.pdf    -> the fixed-format professional PDF
                                          report (protected; ?date=YYYY-MM-DD
                                          for a historical snapshot, else latest)

Run locally:  python3 app.py    then open http://127.0.0.1:5000
Deploy:       see README.md -- gunicorn app:app behind any HTTPS host.
"""
import os
import secrets
import sys
import traceback
from datetime import datetime
from functools import wraps

from flask import Flask, jsonify, request, send_file, g

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")

sys.path.insert(0, os.path.join(ROOT, "engine"))
sys.path.insert(0, os.path.join(ROOT, "ml"))
sys.path.insert(0, HERE)
from pipeline import DataValidationError  # noqa: E402
import ingest  # noqa: E402
import firebase_setup  # noqa: E402
import firestore_store  # noqa: E402
import report_pdf  # noqa: E402

app = Flask(__name__, static_folder="static", static_url_path="")

# Paths reachable with no Firebase token at all -- just the public HTML/CSS/JS
# shell. None of these can read or change any organization's data; every
# route that touches Firestore requires a verified token (see below).
_PUBLIC_PATHS = {"/", "/login.html", "/login.js", "/firebase-config.js", "/favicon.ico"}


def require_supervisor(view):
    """Every /api/* route (except none -- there is no unauthenticated API
    route in this app) is wrapped in this. It verifies the Firebase ID
    token, then checks the supervisors/{uid} Firestore profile exists and
    is active -- so simply having ANY Firebase account is not enough; an
    account also has to have been explicitly provisioned as a supervisor
    by create_supervisor.py."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        token = auth_header[7:] if auth_header.startswith("Bearer ") else None
        decoded = firebase_setup.verify_id_token(token)
        if not decoded:
            return jsonify({"error": "Not authenticated. Please log in."}), 401
        profile = firestore_store.get_supervisor(decoded["uid"])
        if not profile or not profile.get("active", False):
            return jsonify({"error": "This account is not an authorized supervisor."}), 403
        g.supervisor_uid = decoded["uid"]
        g.supervisor_email = profile.get("email", decoded.get("email", ""))
        g.supervisor_name = profile.get("name", g.supervisor_email)
        return view(*args, **kwargs)
    return wrapped


@app.errorhandler(Exception)
def _catch_firebase_init_errors(e):
    # Surfaces a clear message (instead of a raw 500) if Firebase credentials
    # aren't configured yet -- the single most common setup mistake.
    if "Firebase service account credentials" in str(e):
        return jsonify({"error": str(e)}), 500
    traceback.print_exc()
    return jsonify({"error": f"Unexpected server error: {e}"}), 500


@app.route("/")
def index():
    return app.send_static_file("index.html")


@app.route("/login.html")
def login_page():
    return app.send_static_file("login.html")


def _setup_page(token, email="", name="", message="", ok=False):
    """Renders the browser-only 'create a supervisor account' page. Reuses
    the same CSS classes as login.html so it looks like part of the app,
    with no separate stylesheet or JS needed -- it's a plain HTML form."""
    msg_html = ""
    if message:
        cls = "upload-msg ok" if ok else "upload-msg err"
        msg_html = f'<div class="{cls}">{message}</div>'
    body = f'''
      <div class="form-row">
        <label>Supervisor Email</label>
        <input type="email" name="email" value="{email}" placeholder="you@organization.gov.in" required autofocus>
      </div>
      <div class="form-row">
        <label>Display Name</label>
        <input type="text" name="name" value="{name}" placeholder="e.g. A. Sharma" required>
      </div>
      <div class="form-row">
        <label>Password (min 8 characters)</label>
        <input type="password" name="password" placeholder="••••••••" required>
      </div>
      <div class="form-row">
        <label>Confirm Password</label>
        <input type="password" name="confirm" placeholder="••••••••" required>
      </div>
      {msg_html}
      <input type="hidden" name="token" value="{token}">
      <button type="submit" class="btn primary login-submit">Create supervisor account</button>
    '''
    if ok:
        body = f'{msg_html}<a class="btn primary login-submit" style="display:block;text-align:center;text-decoration:none" href="/login.html">Go to login</a>'
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SAT-SA — Create Supervisor Account</title>
<link rel="icon" href="data:,">
<link rel="stylesheet" href="/styles.css"></head>
<body>
<div class="login-page">
  <div class="login-card">
    <div class="login-brand">
      <div class="login-title">SAT-SA — Setup</div>
      <div class="login-subtitle">Create a new supervisor login. This page only works with the correct setup link -- nobody else can reach it.</div>
    </div>
    <form method="post" action="/setup?token={token}">
      {body}
    </form>
    <div class="login-foot">Only people you personally give this link to can create an account here.</div>
  </div>
</div>
</body></html>'''


@app.route("/setup", methods=["GET", "POST"])
def setup_supervisor():
    """Browser-only way to create the first (and every later) supervisor
    account -- no command line needed. Only works if the SETUP_TOKEN
    environment variable is set on the server AND the visitor's link
    includes the exact matching ?token=... . This is what keeps 'no public
    signup' true even though this route exists: without the token (which
    only you set and only you hand out), the route behaves as if it does
    not exist at all."""
    configured = os.environ.get("SETUP_TOKEN", "")
    supplied = request.args.get("token") or request.form.get("token") or ""
    if not configured or not secrets.compare_digest(str(supplied), str(configured)):
        return jsonify({
            "error": "Not found. If you are trying to create a supervisor account, "
                     "you need the exact setup link (with the correct token) -- see README.md."
        }), 404

    if request.method == "GET":
        return _setup_page(configured)

    email = (request.form.get("email") or "").strip()
    name = (request.form.get("name") or "").strip()
    password = request.form.get("password") or ""
    confirm = request.form.get("confirm") or ""

    if not email or not name:
        return _setup_page(configured, email, name, "Email and display name are required.")
    if len(password) < 8:
        return _setup_page(configured, email, name, "Password must be at least 8 characters.")
    if password != confirm:
        return _setup_page(configured, email, name, "Passwords did not match -- try again.")

    try:
        user = firebase_setup.create_auth_user(email, password, name)
        firestore_store.create_supervisor_profile(user.uid, email, name)
    except Exception as e:
        return _setup_page(configured, email, name, f"Could not create account: {e}")

    return _setup_page(configured, message=f"Account created for {email}. You can log in now.", ok=True)


@app.route("/api/session")
@require_supervisor
def api_session():
    return jsonify({"authenticated": True, "name": g.supervisor_name, "email": g.supervisor_email})


@app.route("/api/analysis")
@require_supervisor
def api_analysis():
    try:
        return jsonify(firestore_store.recompute_all())
    except DataValidationError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/template")
@require_supervisor
def api_template():
    import io
    buf = io.BytesIO()
    ingest.sample_dataframe().to_csv(buf, index=False)
    buf.seek(0)
    return send_file(buf, mimetype="text/csv", as_attachment=True,
                      download_name="SAT-SA_organization_template.csv")


@app.route("/api/organizations", methods=["POST"])
@require_supervisor
def api_add_organization():
    name = (request.form.get("name") or "").strip()
    sector = (request.form.get("sector") or "").strip()
    tier = (request.form.get("tier") or "Tier-2").strip()
    f = request.files.get("report")

    if not name:
        return jsonify({"error": "Organization name is required."}), 400
    if f is None or f.filename == "":
        return jsonify({"error": "Please attach the organization's alert data file (.csv)."}), 400

    try:
        csv_bytes = f.read()
        entity_id = firestore_store.create_organization(name, sector, tier, csv_bytes, g.supervisor_uid)
        analysis = firestore_store.recompute_all()
        added = next((e for e in analysis["entities"] if e["entity_id"] == entity_id), None)
        return jsonify({
            "status": "ok", "entity_id": entity_id,
            "risk_score": added["risk_score"] if added else None,
            "totals": analysis["totals"],
        })
    except ingest.IngestError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/organizations/<entity_id>", methods=["DELETE"])
@require_supervisor
def api_delete_organization(entity_id):
    firestore_store.delete_organization(entity_id)
    analysis = firestore_store.recompute_all()
    return jsonify({"status": "ok", "totals": analysis["totals"]})


@app.route("/api/organizations/<entity_id>/reports", methods=["POST"])
@require_supervisor
def api_add_report(entity_id):
    """Adds a new alert-data report to an EXISTING organization, tied to
    whichever date the supervisor picked on that organization's calendar --
    used by the '+ Add report for this date' button. This is separate from
    /api/organizations (POST), which only creates a brand-new organization."""
    date_str = (request.form.get("date") or "").strip()
    f = request.files.get("report")

    if not date_str:
        return jsonify({"error": "Please pick a date for this report."}), 400
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return jsonify({"error": "Date must be in YYYY-MM-DD format."}), 400
    if f is None or f.filename == "":
        return jsonify({"error": "Please attach the organization's alert data file (.csv)."}), 400

    try:
        csv_bytes = f.read()
        firestore_store.add_report_to_organization(entity_id, csv_bytes)
        analysis = firestore_store.recompute_all(extra_date=date_str)
        added = next((e for e in analysis["entities"] if e["entity_id"] == entity_id), None)
        if added is None:
            return jsonify({"error": "Unknown organization."}), 404
        return jsonify({
            "status": "ok", "entity_id": entity_id, "date": date_str,
            "risk_score": added["risk_score"], "totals": analysis["totals"],
        })
    except ingest.IngestError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/assessment/<entity_id>/<date_str>")
@require_supervisor
def api_get_assessment(entity_id, date_str):
    snap = firestore_store.get_assessment(entity_id, date_str)
    if snap is None:
        return jsonify({"found": False, "message": f"No assessment was saved for this organization on {date_str}."})
    return jsonify({"found": True, "assessment": snap})


@app.route("/api/assessment-dates/<entity_id>")
@require_supervisor
def api_assessment_dates(entity_id):
    return jsonify({"dates": firestore_store.list_assessment_dates(entity_id)})


@app.route("/api/entity/<entity_id>/report.pdf")
@require_supervisor
def api_entity_report_pdf(entity_id):
    date_str = request.args.get("date")
    if date_str:
        snap = firestore_store.get_assessment(entity_id, date_str)
        if snap is None:
            return jsonify({"error": f"No assessment found for {entity_id} on {date_str}."}), 404
        entity, generated_at, review_days = snap, snap.get("generated_at", ""), 90
        assessment_date = date_str
    else:
        analysis = firestore_store.recompute_all()
        entity = next((e for e in analysis["entities"] if e["entity_id"] == entity_id), None)
        if entity is None:
            return jsonify({"error": "Unknown entity_id"}), 404
        generated_at, review_days = analysis["generated_at"], analysis["review_period_days"]
        assessment_date = analysis["generated_at"][:10]

    buf = report_pdf.build_report_pdf(entity, assessment_date, generated_at, review_days)
    return send_file(buf, mimetype="application/pdf", as_attachment=True,
                      download_name=f"SAT-SA_report_{entity_id}_{assessment_date}.pdf")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"SAT-SA running on http://127.0.0.1:{port}  (Ctrl+C to stop)")
    app.run(host="0.0.0.0", port=port, debug=False)
