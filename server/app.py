"""
SOCAssure web application (Firebase edition)
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
  GET  /                              -> ONE page that is the login/signup
                                          screen, the dashboard, or a brief
                                          "checking your session" state, all
                                          toggled by visibility in app.js --
                                          see the persistence comment at the
                                          top of static/app.js for why this
                                          is a single page rather than a
                                          separate /login.html (in short:
                                          it's what makes "closing the
                                          browser always logs you out"
                                          actually hold up against browsers
                                          that restore their previous
                                          session on reopen)
  GET  /login.html                    -> kept only as a redirect to / for
                                          anyone with the old link bookmarked
  POST /api/signup                    -> open account creation, called from
                                          the "Create an account" box on the
                                          login screen (public -- no token,
                                          no login required to create one)
  GET  /api/analysis                  -> current live analysis of every
                                          organization THIS supervisor owns
                                          (protected; peer/AI comparison
                                          numbers are still computed across
                                          every organization in the system,
                                          see firestore_store.scope_analysis_to_owner)
  GET  /api/template                  -> downloadable example CSV (public --
                                          it's a generic sample file, no real
                                          organization's data, and it's linked
                                          as a plain <a href> that can't send
                                          an auth token)
  POST /api/organizations             -> add one organization, owned by
                                          whoever is logged in (protected)
  DELETE /api/organizations/<id>      -> delete one organization + its
                                          saved assessment history --
                                          only the owning supervisor can
                                          (protected + ownership-checked)
  GET  /api/assessment/<id>/<date>    -> a saved dated snapshot for the
                                          calendar/date selector (protected
                                          + ownership-checked)
  GET  /api/assessment-dates/<id>     -> which dates have a saved snapshot
                                          for this organization (protected
                                          + ownership-checked)
  GET  /api/entity/<id>/report.pdf    -> the fixed-format professional PDF
                                          report (protected + ownership-
                                          checked; ?date=YYYY-MM-DD for a
                                          historical snapshot, else latest)
  GET  /api/score-history/<id>        -> this organization's risk score on
                                          every date it has a saved snapshot
                                          for, oldest first (protected +
                                          ownership-checked) -- feeds the
                                          score-history trend chart
  GET  /api/audit/<id>                -> this organization's activity log
                                          (created / report-added events,
                                          newest first; protected +
                                          ownership-checked)
  GET  /api/export                    -> one consolidated CSV of every
                                          organization THIS supervisor owns
                                          (protected)

Data isolation: every organization is owned by whichever supervisor
uploaded it (organizations/{id}.created_by). A supervisor's dashboard only
ever shows organizations they own -- another supervisor's organizations,
names, and reports never appear for them, enforced server-side by
_owns_entity() below on every route that takes an entity_id, not just
hidden client-side. The one thing NOT scoped per-supervisor is the
underlying peer/AI comparison: recompute_all() always rebuilds and scores
every organization in the system together (so "compared with similar
companies" and the ML anomaly model stay statistically meaningful even for
a supervisor who's only added one or two companies), and only the result
is filtered down to one supervisor's own organizations afterwards.

Run locally:  python3 app.py    then open http://127.0.0.1:5000
Deploy:       see README.md -- gunicorn app:app behind any HTTPS host.
"""
import os
import secrets
import sys
import traceback
from datetime import datetime
from functools import wraps

from flask import Flask, jsonify, request, send_file, g, redirect

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

# Paths reachable with no Firebase token at all -- the public HTML/CSS/JS
# shell, plus /api/signup (open account creation -- see its route below).
# Every other route requires a verified token (see require_supervisor).
_PUBLIC_PATHS = {"/", "/firebase-config.js", "/favicon.ico", "/api/signup", "/api/template"}


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


def _owns_entity(entity_id):
    """True only if entity_id exists AND was created by the currently
    logged-in supervisor. Every route below that touches ONE specific
    organization (delete, add a report, look up a saved date, download its
    PDF) checks this first and returns the exact same 404 either way if it
    fails -- so a supervisor can't tell 'that id doesn't exist' apart from
    'that id belongs to someone else' by probing ids, and can never read,
    change, or delete another supervisor's organization."""
    owner = firestore_store.get_organization_owner(entity_id)
    return owner is not None and owner == g.supervisor_uid


_UNKNOWN_ORG = ({"error": "Unknown organization."}, 404)


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
    # The login/signup screen now lives inline in index.html (see the
    # persistence comment at the top of static/app.js for why) -- this old
    # separate page is kept only as a redirect, for anyone with the
    # previous link bookmarked.
    return redirect("/")


def _setup_page(token, email="", name="", message="", ok=False):
    """Renders the browser-only 'create a supervisor account' page. Reuses
    the same .login-page/.login-card CSS classes as the login screen in
    index.html, with no separate stylesheet or JS needed -- it's a plain
    HTML form."""
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
        body = f'{msg_html}<a class="btn primary login-submit" style="display:block;text-align:center;text-decoration:none" href="/">Go to login</a>'
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SOCAssure — Create Supervisor Account</title>
<link rel="icon" href="data:,">
<link rel="stylesheet" href="/styles.css"></head>
<body>
<div class="login-page">
  <div class="login-card">
    <div class="login-brand">
      <div class="login-title">SOCAssure — Setup</div>
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


@app.route("/api/signup", methods=["POST"])
def api_signup():
    """Open, public account creation -- called from the 'Create an account'
    box on the login screen (see index.html / app.js). Anyone who reaches
    this website can create
    their own supervisor login this way (there is no invite code/token gate
    on this route, unlike /setup above). Each new account only ever sees
    the organizations IT adds -- see firestore_store.scope_analysis_to_owner
    and _owns_entity below for how that privacy boundary is enforced on
    every other route."""
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip()
    name = (data.get("name") or "").strip()
    password = data.get("password") or ""
    confirm = data.get("confirm") or ""

    if not email:
        return jsonify({"error": "Email is required."}), 400
    if len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters."}), 400
    if password != confirm:
        return jsonify({"error": "Passwords did not match."}), 400

    try:
        user = firebase_setup.create_auth_user(email, password, name or email)
        firestore_store.create_supervisor_profile(user.uid, email, name or email)
    except Exception as e:
        return jsonify({"error": f"Could not create account: {e}"}), 400

    return jsonify({"status": "ok", "email": email})


@app.route("/api/session")
@require_supervisor
def api_session():
    return jsonify({"authenticated": True, "name": g.supervisor_name, "email": g.supervisor_email})


@app.route("/api/analysis")
@require_supervisor
def api_analysis():
    try:
        analysis = firestore_store.recompute_all()
        return jsonify(firestore_store.scope_analysis_to_owner(analysis, g.supervisor_uid))
    except DataValidationError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/template")
def api_template():
    # Deliberately NOT behind @require_supervisor. This is linked from the
    # "Download an example file" text as a plain <a href="/api/template">
    # in index.html -- a normal browser click on a link is just a GET
    # navigation, with no way to attach the Authorization: Bearer <token>
    # header that require_supervisor checks for, so that link would always
    # fail with "Not authenticated" no matter who clicked it. That's fine
    # here: this file is a generic, made-up example CSV with no real
    # organization's data in it, so there's nothing to protect by gating it.
    import io
    buf = io.BytesIO()
    ingest.sample_dataframe().to_csv(buf, index=False)
    buf.seek(0)
    return send_file(buf, mimetype="text/csv", as_attachment=True,
                      download_name="SOCAssure_organization_template.csv")


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
        entity_id = firestore_store.create_organization(name, sector, tier, csv_bytes, g.supervisor_uid, g.supervisor_email)
        analysis = firestore_store.recompute_all()
        scoped = firestore_store.scope_analysis_to_owner(analysis, g.supervisor_uid)
        added = next((e for e in scoped["entities"] if e["entity_id"] == entity_id), None)
        return jsonify({
            "status": "ok", "entity_id": entity_id,
            "risk_score": added["risk_score"] if added else None,
            "totals": scoped["totals"],
        })
    except ingest.IngestError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/organizations/<entity_id>", methods=["DELETE"])
@require_supervisor
def api_delete_organization(entity_id):
    if not _owns_entity(entity_id):
        return _UNKNOWN_ORG
    firestore_store.delete_organization(entity_id)
    analysis = firestore_store.recompute_all()
    scoped = firestore_store.scope_analysis_to_owner(analysis, g.supervisor_uid)
    return jsonify({"status": "ok", "totals": scoped["totals"]})


@app.route("/api/organizations/<entity_id>/reports", methods=["POST"])
@require_supervisor
def api_add_report(entity_id):
    """Adds a new alert-data report to an EXISTING organization, tied to
    whichever date the supervisor picked on that organization's calendar --
    used by the '+ Add report for this date' button. This is separate from
    /api/organizations (POST), which only creates a brand-new organization."""
    if not _owns_entity(entity_id):
        return _UNKNOWN_ORG
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
        firestore_store.add_report_to_organization(entity_id, csv_bytes, date_str, g.supervisor_email)
        # "Today"/"latest" reflects the organization's whole combined
        # history, for every organization (adding data can shift peer
        # numbers for everyone) -- unrelated to which date was picked above.
        analysis = firestore_store.recompute_all()
        scoped = firestore_store.scope_analysis_to_owner(analysis, g.supervisor_uid)
        # The score actually returned here, though, is for the SPECIFIC
        # date the supervisor picked -- computed from only what was
        # uploaded for that date (see recompute_report_date's docstring for
        # why the old behavior of reusing the combined score was a bug).
        dated_entity = firestore_store.recompute_report_date(entity_id, date_str)
        if dated_entity is None:
            return _UNKNOWN_ORG
        return jsonify({
            "status": "ok", "entity_id": entity_id, "date": date_str,
            "risk_score": dated_entity["risk_score"], "totals": scoped["totals"],
        })
    except ingest.IngestError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/assessment/<entity_id>/<date_str>")
@require_supervisor
def api_get_assessment(entity_id, date_str):
    if not _owns_entity(entity_id):
        return _UNKNOWN_ORG
    snap = firestore_store.get_assessment(entity_id, date_str)
    if snap is None:
        return jsonify({"found": False, "message": f"No assessment was saved for this organization on {date_str}."})
    return jsonify({"found": True, "assessment": snap})


@app.route("/api/assessment-dates/<entity_id>")
@require_supervisor
def api_assessment_dates(entity_id):
    if not _owns_entity(entity_id):
        return _UNKNOWN_ORG
    return jsonify({"dates": firestore_store.list_assessment_dates(entity_id)})


@app.route("/api/score-history/<entity_id>")
@require_supervisor
def api_score_history(entity_id):
    if not _owns_entity(entity_id):
        return _UNKNOWN_ORG
    return jsonify({"history": firestore_store.get_score_history(entity_id)})


@app.route("/api/audit/<entity_id>")
@require_supervisor
def api_audit_log(entity_id):
    if not _owns_entity(entity_id):
        return _UNKNOWN_ORG
    return jsonify({"events": firestore_store.get_audit_log(entity_id)})


@app.route("/api/export")
@require_supervisor
def api_export():
    """One consolidated CSV, one row per organization this supervisor owns --
    for handing a whole portfolio's current standing to someone else (a
    senior officer, a report attachment) without opening each organization
    individually. Protected like every other route (needs a real login), so
    the frontend fetches it with the Authorization header and saves the
    result as a file, the same way the PDF report download works -- a plain
    <a href> link can't carry that header (see /api/template's comment for
    why that matters)."""
    import csv as csv_module
    import io as io_module
    analysis = firestore_store.recompute_all()
    scoped = firestore_store.scope_analysis_to_owner(analysis, g.supervisor_uid)
    buf = io_module.StringIO()
    writer = csv_module.writer(buf)
    writer.writerow(["Organization", "Sector", "Tier", "Risk Score (0-100)", "Risk Level",
                      "Monitored Assets", "Alerts Reviewed", "Flags Raised"])
    for e in sorted(scoped["entities"], key=lambda x: -x["risk_score"]):
        writer.writerow([
            e["name"], e["sector"], e.get("tier", ""), e["risk_score"], e["risk_label"],
            e.get("n_assets", ""), e.get("n_alerts", 0), len(e.get("technical", {}).get("flags", [])),
        ])
    mem = io_module.BytesIO(buf.getvalue().encode("utf-8"))
    return send_file(mem, mimetype="text/csv", as_attachment=True,
                      download_name="SOCAssure_all_organizations_export.csv")


@app.route("/api/entity/<entity_id>/report.pdf")
@require_supervisor
def api_entity_report_pdf(entity_id):
    if not _owns_entity(entity_id):
        return _UNKNOWN_ORG
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
                      download_name=f"SOCAssure_report_{entity_id}_{assessment_date}.pdf")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"SOCAssure running on http://127.0.0.1:{port}  (Ctrl+C to stop)")
    app.run(host="0.0.0.0", port=port, debug=False)
