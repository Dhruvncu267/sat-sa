"""
SAT-SA Firestore data layer
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
SUPERVISORS = "supervisors"


def _today_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _assessment_doc_id(entity_id, date_str):
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


def create_organization(name, sector, tier, csv_bytes, uid):
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
    return entity_id


def list_organizations():
    return [d.to_dict() for d in firebase_setup.db().collection(ORGS).stream()]


def add_report_to_organization(entity_id, csv_bytes):
    """Adds a NEW alert-data report to an organization that already exists --
    used by the '+ Add report for this date' button on an organization's own
    page (tied to whichever date is selected on its calendar), as opposed to
    create_organization() above which is only used once, when the
    organization is first created. Unlike create_organization, this does not
    replace the organization's history -- the new rows are merged in on top
    of everything already stored, so nothing already assessed is lost.
    Raises ingest.IngestError on a bad file (same validation as the very
    first upload) or if the organization does not exist."""
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


def delete_organization(entity_id):
    """Removes the organization AND every saved assessment snapshot for it
    -- used by the Delete button. Does not touch other organizations."""
    db = firebase_setup.db()
    db.collection(ORGS).document(entity_id).delete()
    q = db.collection(ASSESSMENTS).where(filter=FieldFilter("entity_id", "==", entity_id))
    batch = db.batch()
    n = 0
    for doc in q.stream():
        batch.delete(doc.reference)
        n += 1
    if n:
        batch.commit()
    return n


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
def recompute_all(extra_date=None):
    """Rebuilds the five CSVs the original engine expects from every
    organization's raw_csv_text in Firestore, runs the untouched pipeline,
    saves a snapshot dated today for every organization (since adding or
    removing one org can shift everyone else's peer-comparison numbers),
    and returns the full analysis dict (same shape /api/analysis always
    returned).

    extra_date: when a supervisor adds a new report for a specific calendar
    date (via add_report_to_organization), pass that date here so a snapshot
    is ALSO saved under that exact date for every organization -- not just
    today's date -- since the new data can shift peer numbers for everyone,
    not only the organization the report was added to."""
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

    dates_to_save = {_today_str()}
    if extra_date:
        dates_to_save.add(extra_date)
    for entity in analysis["entities"]:
        for date_str in dates_to_save:
            save_assessment(entity["entity_id"], date_str, entity, analysis["totals"], analysis["generated_at"])
    return analysis
