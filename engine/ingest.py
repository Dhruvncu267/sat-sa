"""
SAT-SA simple ingestion (one file per organization)
======================================================
This replaces the old "upload 5 technical CSVs" flow with something a
non-technical user can actually fill in: one CSV where each row is one
security alert the organization handled, plus a small form (name, sector,
tier) describing the organization itself.

The simple per-alert columns (see TEMPLATE_COLUMNS below) are converted
internally into the five relational tables the analysis engine
(engine/detectors.py, engine/scoring.py, ml/anomaly_model.py) expects.
That engine doesn't change -- only how data gets INTO it changes.
"""
import os
import re
import uuid
from datetime import datetime

import pandas as pd

TEMPLATE_COLUMNS = [
    "asset_name",           # e.g. "Core Banking Server" -- any name you use internally
    "asset_criticality",    # Critical / High / Medium / Low  (leave blank -> Medium)
    "severity",             # Critical / High / Medium / Low
    "category",             # e.g. Malware, Phishing, Unauthorized Access ...
    "disposition",          # True Positive / False Positive / Benign
    "opened_at",            # when the alert was raised, e.g. 2026-06-01 14:30
    "closed_at",            # when the case was closed
    "escalated",            # Yes / No -- was it escalated to a supervisor/manager?
    "escalated_at",         # only if escalated = Yes, otherwise leave blank
    "root_cause_fixed",     # Yes / No -- was the actual root cause identified & fixed?
    "investigation_notes",  # just paste/write the investigator's notes -- any length
]

REQUIRED_COLUMNS = ["asset_name", "severity", "category", "disposition", "opened_at", "closed_at", "escalated", "root_cause_fixed"]

SECTORS = ["Banking", "Payments", "Power", "Telecom", "Insurance", "Healthcare", "Government", "Other"]


class IngestError(Exception):
    pass


def sample_dataframe():
    """A ready-to-fill example file, so a user knows exactly what's expected."""
    return pd.DataFrame([
        {
            "asset_name": "Core Banking Server", "asset_criticality": "Critical",
            "severity": "Critical", "category": "Unauthorized Access", "disposition": "True Positive",
            "opened_at": "2026-06-01 14:30", "closed_at": "2026-06-01 14:40",
            "escalated": "No", "escalated_at": "", "root_cause_fixed": "No",
            "investigation_notes": "Closed as routine, no further action.",
        },
        {
            "asset_name": "Email Gateway", "asset_criticality": "High",
            "severity": "High", "category": "Phishing", "disposition": "True Positive",
            "opened_at": "2026-06-02 09:10", "closed_at": "2026-06-02 11:45",
            "escalated": "Yes", "escalated_at": "2026-06-02 09:40", "root_cause_fixed": "Yes",
            "investigation_notes": "Confirmed phishing campaign targeting finance team; sender domain "
                                    "blocked, affected users reset passwords, staff notified.",
        },
    ], columns=TEMPLATE_COLUMNS)


def write_template(path):
    sample_dataframe().to_csv(path, index=False)


def _slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", str(text).strip().lower()).strip("-") or "org"


def _parse_dt(val):
    if val is None or (isinstance(val, float) and pd.isna(val)) or str(val).strip() == "":
        return None
    try:
        return pd.to_datetime(val)
    except Exception:
        return None


def _yesno(val):
    return str(val).strip().lower() in ("yes", "y", "true", "1")


def validate_report(df: pd.DataFrame):
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise IngestError(f"Your file is missing required column(s): {', '.join(missing)}. "
                           f"Download the template to see the exact format expected.")
    if len(df) == 0:
        raise IngestError("Your file has no alert rows in it.")


def _read_table(path, columns):
    if os.path.exists(path):
        return pd.read_csv(path)
    return pd.DataFrame(columns=columns)


def add_organization(data_dir, org_name, sector, tier, report_path, forced_entity_id=None):
    """Appends one organization + its alert report into the five internal
    tables under data_dir. Returns the new entity_id.

    forced_entity_id: used only by the Firestore-backed rebuild path
    (server/firestore_store.py), which already assigned a permanent
    entity_id to this organization when it was first created, and needs
    every rebuild to reproduce that exact same id rather than re-deriving
    a fresh slug each time. Any other caller leaves this as None and gets
    the original auto-slugify behavior, unchanged."""
    if not org_name or not org_name.strip():
        raise IngestError("Organization name is required.")
    if sector not in SECTORS:
        sector = "Other"
    tier = tier if tier in ("Tier-1", "Tier-2") else "Tier-2"

    try:
        df = pd.read_csv(report_path)
    except Exception as e:
        raise IngestError(f"Could not read that file as a CSV ({e}). Make sure you exported it as .csv.")
    validate_report(df)

    entities = _read_table(os.path.join(data_dir, "entities.csv"), ["entity_id", "name", "sector", "tier"])
    assets = _read_table(os.path.join(data_dir, "assets.csv"), ["asset_id", "entity_id", "asset_type", "criticality"])
    alerts = _read_table(os.path.join(data_dir, "alerts.csv"),
                          ["alert_id", "entity_id", "asset_id", "asset_type", "timestamp", "severity", "category", "disposition", "status"])
    cases = _read_table(os.path.join(data_dir, "cases.csv"),
                         ["case_id", "alert_id", "entity_id", "opened_at", "closed_at", "closure_minutes", "escalated", "root_cause_identified", "investigation_notes_length"])
    escalations = _read_table(os.path.join(data_dir, "escalations.csv"),
                               ["escalation_id", "case_id", "entity_id", "escalated_at", "escalation_minutes", "severity_at_escalation", "escalated_to"])

    if forced_entity_id:
        entity_id = forced_entity_id
    else:
        base_slug = _slugify(org_name)
        entity_id = base_slug
        suffix = 2
        existing_ids = set(entities.entity_id.astype(str)) if len(entities) else set()
        while entity_id in existing_ids:
            entity_id = f"{base_slug}-{suffix}"
            suffix += 1

    entities = pd.concat([entities, pd.DataFrame([{
        "entity_id": entity_id, "name": org_name.strip(), "sector": sector, "tier": tier,
    }])], ignore_index=True)

    asset_lookup = {}   # asset_name -> asset_id (within this org)
    new_assets, new_alerts, new_cases, new_escalations = [], [], [], []

    for i, row in df.reset_index(drop=True).iterrows():
        asset_name = str(row.get("asset_name") or "Unspecified Asset").strip()
        if asset_name not in asset_lookup:
            asset_id = f"{entity_id}-{_slugify(asset_name)}"
            asset_lookup[asset_name] = asset_id
            criticality = str(row.get("asset_criticality") or "").strip().title()
            if criticality not in ("Critical", "High", "Medium", "Low"):
                criticality = "Medium"
            new_assets.append({"asset_id": asset_id, "entity_id": entity_id,
                                "asset_type": asset_name, "criticality": criticality})
        asset_id = asset_lookup[asset_name]

        severity = str(row.get("severity") or "Medium").strip().title()
        if severity not in ("Critical", "High", "Medium", "Low"):
            severity = "Medium"
        disposition = str(row.get("disposition") or "").strip().title()
        if disposition not in ("True Positive", "False Positive", "Benign"):
            disposition = "True Positive" if "true" in disposition.lower() else "Benign"

        opened_at = _parse_dt(row.get("opened_at")) or pd.Timestamp.now()
        closed_at = _parse_dt(row.get("closed_at"))
        closure_minutes = int((closed_at - opened_at).total_seconds() / 60) if closed_at is not None else None
        if closure_minutes is not None and closure_minutes < 0:
            closure_minutes = 0

        escalated = _yesno(row.get("escalated"))
        root_cause = _yesno(row.get("root_cause_fixed"))
        notes = str(row.get("investigation_notes") or "")
        notes_length = len(notes.strip())

        alert_id = f"{entity_id}-AL{i+1:05d}"
        case_id = f"{entity_id}-CS{i+1:05d}"
        category = str(row.get("category") or "Uncategorised").strip()

        new_alerts.append({
            "alert_id": alert_id, "entity_id": entity_id, "asset_id": asset_id, "asset_type": asset_name,
            "timestamp": opened_at.isoformat(timespec="minutes"), "severity": severity, "category": category,
            "disposition": disposition, "status": "Closed" if closed_at is not None else "Open",
        })
        new_cases.append({
            "case_id": case_id, "alert_id": alert_id, "entity_id": entity_id,
            "opened_at": opened_at.isoformat(timespec="minutes"),
            "closed_at": closed_at.isoformat(timespec="minutes") if closed_at is not None else "",
            "closure_minutes": closure_minutes if closure_minutes is not None else 0,
            "escalated": "Yes" if escalated else "No",
            "root_cause_identified": "Yes" if root_cause else "No",
            "investigation_notes_length": notes_length,
        })
        if escalated:
            escalated_at = _parse_dt(row.get("escalated_at")) or opened_at
            escalation_minutes = max(0, int((escalated_at - opened_at).total_seconds() / 60))
            new_escalations.append({
                "escalation_id": f"{entity_id}-ES{i+1:05d}", "case_id": case_id, "entity_id": entity_id,
                "escalated_at": escalated_at.isoformat(timespec="minutes"),
                "escalation_minutes": escalation_minutes, "severity_at_escalation": severity,
                "escalated_to": "Supervisor",
            })

    assets = pd.concat([assets, pd.DataFrame(new_assets)], ignore_index=True)
    alerts = pd.concat([alerts, pd.DataFrame(new_alerts)], ignore_index=True)
    cases = pd.concat([cases, pd.DataFrame(new_cases)], ignore_index=True)
    escalations = pd.concat([escalations, pd.DataFrame(new_escalations)], ignore_index=True)

    entities.to_csv(os.path.join(data_dir, "entities.csv"), index=False)
    assets.to_csv(os.path.join(data_dir, "assets.csv"), index=False)
    alerts.to_csv(os.path.join(data_dir, "alerts.csv"), index=False)
    cases.to_csv(os.path.join(data_dir, "cases.csv"), index=False)
    escalations.to_csv(os.path.join(data_dir, "escalations.csv"), index=False)

    return entity_id, len(new_alerts)
