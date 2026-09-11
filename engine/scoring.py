"""
SAT-SA scoring & peer benchmarking
====================================
Turns per-entity metrics into:
  1. peer benchmark tables (z-scores within a sector), used both by the
     detectors (ns_low_activity_vs_peers) and by the dashboard's peer
     comparison charts.
  2. a composite 0-100 "Supervisory Risk Score" aggregating all flags
     raised for an entity, with a Low/Medium/High/Critical label.
"""
import pandas as pd


def compute_entity_metrics(alerts, cases, escalations, assets):
    """One row per entity with the core metrics used for peer benchmarking."""
    rows = []
    for entity_id, ent_assets in assets.groupby("entity_id"):
        a = alerts[alerts.entity_id == entity_id]
        c = cases[cases.entity_id == entity_id]
        e = escalations[escalations.entity_id == entity_id]
        n_assets = len(ent_assets)
        crit_tp = a[(a.severity.isin(["Critical", "High"])) & (a.disposition == "True Positive")]
        rows.append({
            "entity_id": entity_id,
            "n_assets": n_assets,
            "n_alerts": len(a),
            "alerts_per_asset": len(a) / n_assets if n_assets else 0,
            "n_cases": len(c),
            "escalation_rate": (len(e) / len(crit_tp)) if len(crit_tp) else 0,
            "median_closure_minutes": c.closure_minutes.median() if len(c) else 0,
            "median_notes_length": c.investigation_notes_length.median() if len(c) else 0,
            "median_escalation_minutes": e.escalation_minutes.median() if len(e) else 0,
            "root_cause_rate": (c.root_cause_identified == "Yes").mean() if len(c) else 0,
        })
    return pd.DataFrame(rows)


def with_sector(metrics_df, entities_df):
    return metrics_df.merge(entities_df[["entity_id", "name", "sector", "tier"]], on="entity_id")


def peer_zscores(metrics_with_sector, column):
    """Return a copy with a z-score column for `column`, computed within
    each sector peer group."""
    df = metrics_with_sector.copy()
    df[f"{column}_z"] = (
        df.groupby("sector")[column]
        .transform(lambda s: (s - s.mean()) / s.std(ddof=0) if s.std(ddof=0) > 0 else 0.0)
    )
    return df


RISK_BANDS = [
    (70, "Critical"),
    (45, "High"),
    (20, "Medium"),
    (0, "Low"),
]


def risk_label(score):
    for threshold, label in RISK_BANDS:
        if score >= threshold:
            return label
    return "Low"


# Plain-English version of the same four bands, for a non-technical reader.
SIMPLE_LABELS = {
    "Critical": "Very poor handling",
    "High": "Poor handling",
    "Medium": "Needs attention",
    "Low": "Good",
}


def simple_label(label):
    return SIMPLE_LABELS.get(label, label)


def composite_score(flags_for_entity):
    """Sum flag weights, then compress into 0-100 with diminishing returns
    (so 5 minor flags don't blow past a single severe one unfairly, but
    multiple independent weaknesses still compound)."""
    raw = sum(f.weight for f in flags_for_entity)
    # soft cap via sqrt-ish compression
    score = min(100, round(raw * 0.9 + (raw ** 0.5) * 3))
    return score
