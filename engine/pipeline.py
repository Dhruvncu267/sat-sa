"""
SOCAssure analysis pipeline (shared core)
=========================================
Loads the data tables, runs the rule-based detectors, runs the ML anomaly
layer, blends everything into a final composite risk score, and returns one
JSON-ready `analysis` dict.

Used by BOTH:
  - engine/build.py   (offline mode: writes a static dashboard.html you can
                        double-click, no server needed)
  - server/app.py     (online mode: a real local Flask app with a REST API,
                        live CSV upload + recompute)

Keeping this logic in one place means the two delivery modes can never
silently disagree with each other.
"""
import os
import sys
from datetime import datetime, timezone

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "ml"))
import detectors as D
import scoring as S
import anomaly_model as ML

REVIEW_PERIOD_DAYS = 90

REQUIRED_FILES = {
    "entities.csv": ["entity_id", "name", "sector", "tier"],
    "assets.csv": ["asset_id", "entity_id", "asset_type", "criticality"],
    "alerts.csv": ["alert_id", "entity_id", "asset_id", "asset_type", "timestamp", "severity", "category", "disposition", "status"],
    "cases.csv": ["case_id", "alert_id", "entity_id", "opened_at", "closed_at", "closure_minutes", "escalated", "root_cause_identified", "investigation_notes_length"],
    "escalations.csv": ["escalation_id", "case_id", "entity_id", "escalated_at", "escalation_minutes", "severity_at_escalation", "escalated_to"],
}


class DataValidationError(Exception):
    pass


def validate_data_dir(data_dir):
    """Check the five required CSVs exist with the expected columns before
    we try to analyse them -- gives a clear error instead of a stack trace
    when someone points the tool at a real (differently-shaped) export."""
    problems = []
    for fname, required_cols in REQUIRED_FILES.items():
        path = os.path.join(data_dir, fname)
        if not os.path.exists(path):
            problems.append(f"Missing file: {fname}")
            continue
        try:
            cols = set(pd.read_csv(path, nrows=0).columns)
        except Exception as e:
            problems.append(f"Could not read {fname}: {e}")
            continue
        missing = [c for c in required_cols if c not in cols]
        if missing:
            problems.append(f"{fname} is missing column(s): {', '.join(missing)}")
    if problems:
        raise DataValidationError("; ".join(problems))


def load_data(data_dir):
    alerts = pd.read_csv(os.path.join(data_dir, "alerts.csv"))
    cases = pd.read_csv(os.path.join(data_dir, "cases.csv"))
    escalations = pd.read_csv(os.path.join(data_dir, "escalations.csv"))
    assets = pd.read_csv(os.path.join(data_dir, "assets.csv"))
    entities = pd.read_csv(os.path.join(data_dir, "entities.csv"))
    return alerts, cases, escalations, assets, entities


def weekly_trend(alerts, entity_id):
    a = alerts[alerts.entity_id == entity_id].copy()
    if a.empty:
        return []
    a["_ts"] = pd.to_datetime(a["timestamp"])
    a["week"] = a["_ts"].dt.to_period("W").apply(lambda p: p.start_time.strftime("%Y-%m-%d"))
    g = a.groupby("week").size().reset_index(name="count").sort_values("week")
    return g.to_dict(orient="records")


def monthly_trend(alerts, entity_id):
    # A calendar-month version of weekly_trend, for a chart that's actually
    # easy to read at a glance (a bar per month, e.g. "Jul 2026") instead of
    # one point per ISO week, which packs in far more points than a
    # supervisor needs and is hard to make sense of quickly.
    a = alerts[alerts.entity_id == entity_id].copy()
    if a.empty:
        return []
    a["_ts"] = pd.to_datetime(a["timestamp"])
    a["month"] = a["_ts"].dt.to_period("M").apply(lambda p: p.start_time.strftime("%Y-%m-%d"))
    g = a.groupby("month").size().reset_index(name="count").sort_values("month")
    out = g.to_dict(orient="records")
    for row in out:
        row["label"] = pd.Timestamp(row["month"]).strftime("%b %Y")  # e.g. "Jul 2026"
    return out


def category_breakdown(alerts, entity_id, top_n=6):
    a = alerts[alerts.entity_id == entity_id]
    g = a.groupby("category").size().reset_index(name="count").sort_values("count", ascending=False).head(top_n)
    return g.to_dict(orient="records")


def run_all_detectors(alerts, cases, escalations, assets, entities, metrics_z):
    all_flags = {}
    peer_notes_by_sector, peer_esc_by_sector = {}, {}
    for sector, grp in metrics_z.groupby("sector"):
        peer_notes_by_sector[sector] = grp.median_notes_length.median()
        peer_esc_by_sector[sector] = grp.median_escalation_minutes.median()

    for entity_id in entities.entity_id.tolist():
        sector = entities.loc[entities.entity_id == entity_id, "sector"].iloc[0]
        flags = [
            D.eg_quick_closure_no_escalation(alerts, cases, entity_id),
            D.eg_critical_no_escalation(alerts, cases, entity_id),
            D.eg_repeat_no_remediation(alerts, cases, entity_id),
            D.eg_template_investigations(cases, alerts, entity_id, peer_notes_by_sector.get(sector, 0)),
            D.eg_escalation_delay(cases, escalations, entity_id, peer_esc_by_sector.get(sector, 0)),
            D.ns_missing_telemetry(alerts, assets, entity_id),
            D.ns_no_escalation_records(alerts, cases, escalations, entity_id),
            D.ns_low_activity_vs_peers(metrics_z, entity_id, sector),
            D.ns_missing_expected_category(alerts, assets, entity_id),
        ]
        all_flags[entity_id] = [f for f in flags if f is not None]
    return all_flags


def empty_analysis():
    """The 'nothing added yet' state -- everything at zero, no organizations.
    Returned instead of erroring so a brand-new install (or one where the
    user hasn't added an organization yet) shows a clean empty screen."""
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "review_period_days": REVIEW_PERIOD_DAYS,
        "totals": {"entities": 0, "alerts": 0, "cases": 0, "escalations": 0, "assets": 0, "flags_raised": 0},
        "sector_stats": [],
        "entities": [],
    }


def has_any_organizations(data_dir):
    path = os.path.join(data_dir, "entities.csv")
    if not os.path.exists(path):
        return False
    try:
        return len(pd.read_csv(path)) > 0
    except Exception:
        return False


def run_pipeline(data_dir):
    if not has_any_organizations(data_dir):
        return empty_analysis()

    validate_data_dir(data_dir)
    alerts, cases, escalations, assets, entities = load_data(data_dir)

    metrics = S.compute_entity_metrics(alerts, cases, escalations, assets)
    metrics = S.with_sector(metrics, entities)
    metrics_z = S.peer_zscores(metrics, "alerts_per_asset")

    all_flags = run_all_detectors(alerts, cases, escalations, assets, entities, metrics_z)

    # ---- ML anomaly layer ----
    ml_scores, ml_features = ML.run_isolation_forest(metrics_z)

    entity_out = []
    for _, erow in entities.iterrows():
        entity_id = erow["entity_id"]
        flags = all_flags.get(entity_id, [])
        rule_score = S.composite_score(flags)
        ml_score = ml_scores.get(entity_id, 0.0)
        # hybrid composite: interpretable rules carry the majority weight
        # (they map directly to named PS concerns and are fully explainable),
        # the ML anomaly score contributes the rest so entities that are
        # statistically unusual but didn't trip a specific rule still surface.
        final_score = min(100, round(rule_score * 0.75 + ml_score * 0.25))
        label = S.risk_label(final_score)
        mrow = metrics_z[metrics_z.entity_id == entity_id].iloc[0]

        peer_group = metrics_z[metrics_z.sector == erow["sector"]]
        peer_count = len(peer_group) - 1
        peer_comparison = {
            "sector": erow["sector"],
            "peer_count": peer_count,
            "alerts_per_asset": round(float(mrow.alerts_per_asset), 2),
            "sector_avg_alerts_per_asset": round(float(peer_group.alerts_per_asset.mean()), 2),
            "escalation_rate": round(float(mrow.escalation_rate), 2),
            "sector_avg_escalation_rate": round(float(peer_group.escalation_rate.mean()), 2),
        }

        ml_insight_list = ml_features.get(entity_id, [])
        # one merged, plain-English list: every rule finding's rationale is
        # already written in plain sentences; every ML insight gets turned
        # into one too. This is what the simple UI shows front-and-centre.
        plain_reasons = [f.rationale for f in flags] + [ML.to_sentence(ins) for ins in ml_insight_list]
        if not plain_reasons:
            plain_reasons = ["No concerning patterns found in the data reviewed for this company."]

        entity_out.append({
            "entity_id": entity_id,
            "name": erow["name"],
            "sector": erow["sector"],
            "tier": erow["tier"],
            "risk_score": int(final_score),
            "risk_label": label,
            "simple_label": S.simple_label(label),
            "plain_reasons": plain_reasons,
            "n_alerts": int(mrow.n_alerts),
            "n_assets": int(mrow.n_assets),
            "n_flags": len(flags),
            "peer_comparison": peer_comparison,
            "weekly_trend": weekly_trend(alerts, entity_id),
            "monthly_trend": monthly_trend(alerts, entity_id),
            "category_breakdown": category_breakdown(alerts, entity_id),
            # kept for anyone who wants the technical detail (rule ids,
            # evidence rows, raw ML z-scores) -- hidden behind a toggle in the UI
            "technical": {
                "rule_score": int(rule_score),
                "ml_anomaly_score": ml_score,
                "flags": [f.to_dict() for f in flags],
                "ml_insights": ml_insight_list,
            },
        })

    entity_out.sort(key=lambda e: e["risk_score"], reverse=True)

    sector_stats = (
        metrics_z.groupby("sector")
        .agg(entities=("entity_id", "count"), avg_alerts_per_asset=("alerts_per_asset", "mean"))
        .reset_index()
        .round(2)
        .to_dict(orient="records")
    )

    analysis = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "review_period_days": REVIEW_PERIOD_DAYS,
        "totals": {
            "entities": len(entities),
            "alerts": len(alerts),
            "cases": len(cases),
            "escalations": len(escalations),
            "assets": len(assets),
            "flags_raised": sum(e["n_flags"] for e in entity_out),
        },
        "sector_stats": sector_stats,
        "entities": entity_out,
    }
    return analysis
