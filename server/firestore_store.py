"""
SOCAssure Firestore data layer
==============================
This is the ONLY part of the app that talks to Firestore. Everything below
it (engine/ingest.py, engine/detectors.py, engine/scoring.py,
ml/anomaly_model.py, engine/pipeline.py) is completely unchanged from the
original file-based prototype -- it still just reads/writes the same five
CSVs in a folder. What changed is *where that folder's contents come from*:
instead of living permanently on disk, it's rebuilt on demand from what's
stored in Firestore.

Firestore layout:
  organizations/{entity_id}
      name, sector, tier, raw_csv_text (the exact CSV the supervisor
      uploaded), created_at, created_by, updated_at
  assessments/{entity_id}__{YYYY-MM-DD}
      entity_id, assessment_date, plus every field the dashboard needs for
      that organization on that date (score, plain_reasons, category
      breakdown, peer comparison, technical detail, ...) -- this is the
      saved snapshot the calendar/date selector reads.
  supervisors/{uid}
      email, name, active, created_at -- who's allowed to log in.

Why "rebuild all orgs into temp CSVs, then run the untouched pipeline"
instead of translating the rule engine to query Firestore directly: adding
or removing ONE organization changes the peer-comparison numbers for every
OTHER organization in its sector (z-scores, ML training set, etc). The
existing pipeline already handles that correctly as long as it sees
everyone's data at once -- reusing it exactly as-is, fed from a
freshly-written temp folder, is simpler and far less risky than re-deriving
that same peer-comparison math against Firestore queries.
"""
import io
import os
import shutil
import tempfile
from datetime import datetime, timezone

import pandas as pd
from google.cloud.firestore_v1 import FieldFilter

import firebase_setup
import ingest
from pipeline import run_pipeline

ORGS = "organizations"
ASSESSMENTS = "assessments"
REPORTS = "reports"
SUPERVISORS = "supervisors"
AUDIT_LOG = "audit_log"


def _today_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _assessment_doc_id(entity_id, date_str):
    return f"{entity_id}__{date_str}"


def _report_doc_id(entity_id, date_str):
    return f"{entity_id}__{date_str}"


# ------------------------------------------------------------ organizations
def generate_entity_id(name):
    """A free slug for a brand-new organization, checked against every
    entity_id already stored in Firestore (not just today's -- forever, so
    a deleted-then-re-added org with the same name still gets a fresh id
    if the old one is still referenced by historical assessments)."""
    base = ingest._slugify(name)
    existing = {d.id for d in firebase_setup.db().collection(ORGS).stream()}
    entity_id = base
    suffix = 2
    while entity_id in existing:
        entity_id = f"{base}-{suffix}"
        suffix += 1
    return entity_id


def create_organization(name, sector, tier, csv_bytes, uid, actor_email=""):
    """Validates the uploaded CSV (same validation the original prototype
    used), then stores the organization + its raw report in Firestore.
    Raises ingest.IngestError on a bad file, same as before."""
    try:
        df = pd.read_csv(io.BytesIO(csv_bytes))
    except Exception as e:
        raise ingest.IngestError(f"Could not read that file as a CSV ({e}). Make sure you exported it as .csv.")
    ingest.validate_report(df)  # raises ingest.IngestError with a clear message on bad columns

    entity_id = generate_entity_id(name)
    now = datetime.now(timezone.utc).isoformat()
    firebase_setup.db().collection(ORGS).document(entity_id).set({
        "entity_id": entity_id,
        "name": name.strip(),
        "sector": sector if sector in ingest.SECTORS else "Other",
        "tier": tier if tier in ("Tier-1", "Tier-2") else "Tier-2",
        "raw_csv_text": csv_bytes.decode("utf-8", errors="replace"),
        "created_at": now,
        "created_by": uid,
        "updated_at": now,
    })
    # Also remember this first file on its own, tagged to today's date --
    # see _save_dated_report()'s docstring for why: it's what lets today's
    # calendar entry show a score for just this file, even after later
    # reports get added for other dates.
    _save_dated_report(entity_id, _today_str(), df)
    log_audit_event(entity_id, "created",
                     f"Organization created with an initial report ({len(df)} alert row"
                     f"{'s' if len(df) != 1 else ''}).", actor_email)
    return entity_id


def list_organizations():
    return [d.to_dict() for d in firebase_setup.db().collection(ORGS).stream()]


def get_organization_owner(entity_id):
    """Returns the uid of whichever supervisor created this organization, or
    None if it doesn't exist. Every /api/* route that touches one specific
    organization (delete, add a report, look up an assessment date, download
    its PDF) calls this first and refuses to proceed unless it matches the
    logged-in supervisor's own uid -- that's what keeps one supervisor's
    organizations invisible to every other supervisor."""
    snap = firebase_setup.db().collection(ORGS).document(entity_id).get()
    if not snap.exists:
        return None
    return snap.to_dict().get("created_by")


def add_report_to_organization(entity_id, csv_bytes, date_str, actor_email=""):
    """Adds a NEW alert-data report to an organization that already exists --
    used by the '+ Add report for this date' button on an organization's own
    page (tied to whichever date is selected on its calendar), as opposed to
    create_organization() above which is only used once, when the
    organization is first created. Unlike create_organization, this does not
    replace the organization's history -- the new rows are merged in on top
    of everything already stored, so nothing already assessed is lost (this
    merged copy is the organization's overall CURRENT state, shown as
    "today"/"latest" and used for peer comparisons). The new rows are ALSO
    kept on their own, tagged to `date_str` (see _save_dated_report) -- that
    separate copy is what lets that ONE calendar date show a score for just
    what was uploaded for it, instead of the organization's whole combined
    history. Raises ingest.IngestError on a bad file (same validation as the
    very first upload) or if the organization does not exist."""
    doc_ref = firebase_setup.db().collection(ORGS).document(entity_id)
    snap = doc_ref.get()
    if not snap.exists:
        raise ingest.IngestError("Unknown organization -- it may have been deleted.")
    org = snap.to_dict()

    try:
        df_new = pd.read_csv(io.BytesIO(csv_bytes))
    except Exception as e:
        raise ingest.IngestError(f"Could not read that file as a CSV ({e}). Make sure you exported it as .csv.")
    ingest.validate_report(df_new)

    df_old = pd.read_csv(io.StringIO(org["raw_csv_text"]))
    merged = pd.concat([df_old, df_new], ignore_index=True, sort=False)

    doc_ref.update({
        "raw_csv_text": merged.to_csv(index=False),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
    _save_dated_report(entity_id, date_str, df_new)
    log_audit_event(entity_id, "report_added",
                     f"Added a report for {date_str} ({len(df_new)} alert row"
                     f"{'s' if len(df_new) != 1 else ''}).", actor_email)


def delete_organization(entity_id):
    """Removes the organization AND every saved assessment snapshot AND
    every saved per-date report for it -- used by the Delete button. Does
    not touch other organizations."""
    db = firebase_setup.db()
    db.collection(ORGS).document(entity_id).delete()
    batch = db.batch()
    n = 0
    for collection in (ASSESSMENTS, REPORTS, AUDIT_LOG):
        q = db.collection(collection).where(filter=FieldFilter("entity_id", "==", entity_id))
        for doc in q.stream():
            batch.delete(doc.reference)
            n += 1
    if n:
        batch.commit()
    return n


# ------------------------------------------------------- dated raw reports
def _save_dated_report(entity_id, date_str, df_new):
    """Stores the alert rows for ONE specific calendar date's evaluation, on
    their own -- kept separate from the organization's overall cumulative
    history (organizations/{id}.raw_csv_text). This is what lets each date
    on the calendar show its OWN score (based only on what was uploaded for
    that date), instead of every date converging on the same combined
    score. If a report was already saved for this exact date, the new rows
    are merged into just that date's copy (so uploading two files for the
    same date combines them) -- but never mixed with any OTHER date's
    rows."""
    doc_ref = firebase_setup.db().collection(REPORTS).document(_report_doc_id(entity_id, date_str))
    snap = doc_ref.get()
    if snap.exists:
        df_old = pd.read_csv(io.StringIO(snap.to_dict()["csv_text"]))
        df_out = pd.concat([df_old, df_new], ignore_index=True, sort=False)
    else:
        df_out = df_new
    doc_ref.set({
        "entity_id": entity_id,
        "date": date_str,
        "csv_text": df_out.to_csv(index=False),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })


def get_dated_report_csv(entity_id, date_str):
    """The raw CSV text uploaded specifically for this organization+date (not
    the organization's full merged history), or None if nothing was ever
    uploaded for that exact date."""
    snap = firebase_setup.db().collection(REPORTS).document(_report_doc_id(entity_id, date_str)).get()
    return snap.to_dict()["csv_text"] if snap.exists else None


# -------------------------------------------------------------- assessments
def save_assessment(entity_id, date_str, entity_analysis, totals, generated_at):
    doc = dict(entity_analysis)
    doc["assessment_date"] = date_str
    doc["totals_at_assessment"] = totals
    doc["generated_at"] = generated_at
    firebase_setup.db().collection(ASSESSMENTS).document(_assessment_doc_id(entity_id, date_str)).set(doc)


def get_assessment(entity_id, date_str):
    snap = firebase_setup.db().collection(ASSESSMENTS).document(_assessment_doc_id(entity_id, date_str)).get()
    return snap.to_dict() if snap.exists else None


def list_assessment_dates(entity_id):
    q = (firebase_setup.db().collection(ASSESSMENTS)
         .where(filter=FieldFilter("entity_id", "==", entity_id)))
    dates = sorted({d.to_dict().get("assessment_date") for d in q.stream()}, reverse=True)
    return [d for d in dates if d]


def get_latest_assessment(entity_id):
    dates = list_assessment_dates(entity_id)
    return get_assessment(entity_id, dates[0]) if dates else None


def get_score_history(entity_id):
    """{date, risk_score, risk_label} for every saved assessment date of
    this organization, oldest first -- feeds the score-history trend chart
    on that organization's page. Reuses the same saved snapshots the
    calendar/date selector already reads (assessments/{id}__{date}); this
    just lists every one of them instead of just one."""
    history = []
    for date_str in sorted(list_assessment_dates(entity_id)):
        snap = get_assessment(entity_id, date_str)
        if snap:
            history.append({
                "date": date_str,
                "risk_score": snap.get("risk_score"),
                "risk_label": snap.get("risk_label"),
            })
    return history


# ------------------------------------------------------------- activity log
def log_audit_event(entity_id, event, detail, actor_email=""):
    """Appends one entry to an organization's activity history (shown in the
    'Activity log' section of its page): who did what, and when. Kept
    intentionally simple -- a plain record of creation and report additions,
    not a full compliance audit trail. Deletion isn't logged here, since
    delete_organization() purges this organization's whole audit log along
    with its assessments/reports (there is no page left to show it on)."""
    firebase_setup.db().collection(AUDIT_LOG).document().set({
        "entity_id": entity_id,
        "event": event,               # "created" | "report_added"
        "detail": detail,
        "actor_email": actor_email or "unknown",
        "at": datetime.now(timezone.utc).isoformat(),
    })


def get_audit_log(entity_id):
    q = (firebase_setup.db().collection(AUDIT_LOG)
         .where(filter=FieldFilter("entity_id", "==", entity_id)))
    events = [d.to_dict() for d in q.stream()]
    events.sort(key=lambda ev: ev.get("at", ""), reverse=True)
    return events


# ------------------------------------------------------------- supervisors
def get_supervisor(uid):
    snap = firebase_setup.db().collection(SUPERVISORS).document(uid).get()
    return snap.to_dict() if snap.exists else None


def create_supervisor_profile(uid, email, name):
    firebase_setup.db().collection(SUPERVISORS).document(uid).set({
        "email": email, "name": name or email, "active": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })


# --------------------------------------------------------------- recompute
def recompute_all():
    """Rebuilds the five CSVs the original engine expects from every
    organization's CURRENT, full raw_csv_text in Firestore, runs the
    untouched pipeline, saves a snapshot dated TODAY for every organization
    (since adding or removing one org can shift everyone else's
    peer-comparison numbers), and returns the full analysis dict (same
    shape /api/analysis always returned). This is the organization's
    overall, up-to-date standing -- "today" / "Back to latest" -- built from
    everything ever uploaded for it. A specific PAST or FUTURE calendar date
    is a different thing entirely: see recompute_report_date() below, which
    is what the '+ Add report for this date' feature actually saves under
    that date, using only what was uploaded for it."""
    orgs = list_organizations()
    tmp_dir = tempfile.mkdtemp(prefix="sat_sa_")
    try:
        for org in sorted(orgs, key=lambda o: o["entity_id"]):  # stable order
            csv_path = os.path.join(tmp_dir, f"_src_{org['entity_id']}.csv")
            with open(csv_path, "w", encoding="utf-8") as f:
                f.write(org["raw_csv_text"])
            ingest.add_organization(
                tmp_dir, org["name"], org["sector"], org["tier"], csv_path,
                forced_entity_id=org["entity_id"],
            )
        analysis = run_pipeline(tmp_dir)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    today = _today_str()
    for entity in analysis["entities"]:
        save_assessment(entity["entity_id"], today, entity, analysis["totals"], analysis["generated_at"])
    return analysis


def recompute_report_date(entity_id, date_str):
    """Recomputes and saves the assessment snapshot for ONE organization on
    ONE specific calendar date, using ONLY the report(s) uploaded for that
    exact date (via _save_dated_report) -- NOT the organization's full
    merged history. This is the fix for a real bug: previously, adding a
    report for a given date re-ran the pipeline on the organization's whole
    combined history and saved that same combined result under every date
    touched so far, so picking a different date on the calendar just kept
    showing the same number. Now date 10's score reflects only what was
    uploaded for date 10, and date 11's reflects only what was uploaded for
    date 11, exactly like two independent evaluations.

    Every OTHER organization is still scored using its own CURRENT full
    data (not isolated to this date), so peer/AI comparison numbers stay
    grounded in real, up-to-date companies -- only the ONE organization
    actually being evaluated for this date is isolated to just that
    date's report. Returns the saved entity dict, or None if nothing was
    ever uploaded for this organization on this exact date."""
    dated_csv = get_dated_report_csv(entity_id, date_str)
    if dated_csv is None:
        return None
    orgs = list_organizations()
    tmp_dir = tempfile.mkdtemp(prefix="sat_sa_date_")
    try:
        for org in sorted(orgs, key=lambda o: o["entity_id"]):
            csv_text = dated_csv if org["entity_id"] == entity_id else org["raw_csv_text"]
            csv_path = os.path.join(tmp_dir, f"_src_{org['entity_id']}.csv")
            with open(csv_path, "w", encoding="utf-8") as f:
                f.write(csv_text)
            ingest.add_organization(
                tmp_dir, org["name"], org["sector"], org["tier"], csv_path,
                forced_entity_id=org["entity_id"],
            )
        analysis = run_pipeline(tmp_dir)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    entity = next((e for e in analysis["entities"] if e["entity_id"] == entity_id), None)
    if entity is None:
        return None
    save_assessment(entity_id, date_str, entity, analysis["totals"], analysis["generated_at"])
    return entity


def scope_analysis_to_owner(analysis, uid):
    """Every supervisor's data is private -- one supervisor's organizations
    are never shown to another. But the score for any ONE organization still
    has to be worked out by comparing it against every other organization in
    its sector (peer averages, and the Isolation Forest ML model), or that
    comparison becomes meaningless for a supervisor who's only added one or
    two companies. So recompute_all() above always runs across EVERY
    organization in the system, and this function is what narrows the
    result down afterwards to only the organizations `uid` owns, right
    before it's sent to that supervisor's browser -- the full peer/AI
    comparison numbers stay in each visible organization's own record
    (they're just statistics, not another supervisor's actual data), but no
    other supervisor's organization, name, or report ever appears."""
    owned_ids = {o["entity_id"] for o in list_organizations() if o.get("created_by") == uid}
    entities = [e for e in analysis["entities"] if e["entity_id"] in owned_ids]
    totals = {
        "entities": len(entities),
        "alerts": sum(e.get("n_alerts", 0) for e in entities),
        "flags_raised": sum(len(e.get("technical", {}).get("flags", [])) for e in entities),
    }
    return {
        "generated_at": analysis.get("generated_at"),
        "review_period_days": analysis.get("review_period_days"),
        "entities": entities,
        "totals": totals,
    }
