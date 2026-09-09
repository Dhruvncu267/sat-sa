"""
SAT-SA machine-learning layer
===============================
The rule-based detectors (engine/detectors.py) catch KNOWN patterns of
concern that a domain expert can name in advance (quick closures, missing
telemetry, etc). This module adds an unsupervised ML layer on top, whose
job is different: catch entities that look statistically unusual across
their *combination* of behavioural metrics, even when no single rule fires
-- i.e. "suspicious operational patterns" the rule author didn't think to
write a rule for. This directly targets PS requirement 4.7 ("Identify
anomalies, outliers and suspicious operational patterns") and the
"Innovation and additional supervisory insights" scoring criterion.

Model: Isolation Forest (scikit-learn), fully offline, trains in
milliseconds, no internet/cloud/API dependency -- compliant with the PS's
air-gapped deployment requirement.

Design notes (why it's built this way, not the naive way):
  - Features are z-scored WITHIN each entity's own sector peer group first
    (a bank is only compared to other banks), THEN the model is trained on
    the combined z-scored matrix across all entities. This keeps sectors
    comparable on one scale while still respecting "normal looks different
    per sector".
  - We deliberately do NOT min-max-rescale the anomaly score within each
    small peer group. Doing that forces some entity to score close to 100
    and another close to 0 even when a whole sector is behaving normally
    (with only 4-5 peers, ordinary sampling noise gets stretched into a
    fake "high risk" entity). Instead we calibrate score_samples() against
    a FIXED reference scale, so a genuinely unremarkable entity scores low
    everywhere, and a high score means "actually unusual", not merely
    "the least normal of a small, all-normal group".
  - Explainability: every entity's score comes with the specific features
    that drove it (their own z-score vs peers), satisfying the PS's
    explainability/auditability requirement for any ML component.
"""
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

FEATURES = [
    "alerts_per_asset",
    "escalation_rate",
    "median_closure_minutes",
    "median_notes_length",
    "median_escalation_minutes",
    "root_cause_rate",
]

FEATURE_LABELS = {
    "alerts_per_asset": "alert volume per asset",
    "escalation_rate": "escalation rate on critical/high true positives",
    "median_closure_minutes": "median case closure time",
    "median_notes_length": "median investigation note length",
    "median_escalation_minutes": "median time-to-escalate",
    "root_cause_rate": "root-cause identification rate",
}

FEATURE_SENTENCES = {
    "alerts_per_asset": {
        "above": "raises far more security alerts per device than similar companies",
        "below": "raises far fewer security alerts per device than similar companies (a possible monitoring blind spot)",
    },
    "escalation_rate": {
        "above": "escalates serious alerts to management more often than similar companies",
        "below": "almost never escalates serious alerts to management, unlike similar companies",
    },
    "median_closure_minutes": {
        "above": "takes much longer than similar companies to close a case",
        "below": "closes cases far faster than similar companies (which can mean rushed handling)",
    },
    "median_notes_length": {
        "above": "writes much more detailed investigation notes than similar companies",
        "below": "writes very short, generic investigation notes compared to similar companies",
    },
    "median_escalation_minutes": {
        "above": "takes much longer than similar companies to escalate a problem once it's flagged",
        "below": "escalates problems faster than similar companies",
    },
    "root_cause_rate": {
        "above": "fixes the actual root cause of problems more often than similar companies",
        "below": "rarely identifies the actual root cause of problems, unlike similar companies",
    },
}


def to_sentence(insight):
    """Turn one ML insight dict into a single plain-English sentence, no
    numbers or statistics jargon required to understand it."""
    wording = FEATURE_SENTENCES.get(insight["feature"])
    if not wording:
        return f"Unusual {insight['label']}."
    return "This company " + wording[insight["direction"]] + "."


MIN_PEERS_FOR_ZSCORE = 3     # below this, sector z-scores aren't meaningful
# Calibration: maps IsolationForest.decision_function() (roughly centred on
# 0, positive = normal, negative = anomalous) onto a fixed 0-100 scale that
# does NOT depend on how many peers an entity happens to have. Tuned so a
# textbook-normal entity lands well under 20 and a clearly-planted anomaly
# lands 55+.
CALIBRATION_SCALE = 140


def _zscore_within_sector(metrics_df: pd.DataFrame) -> pd.DataFrame:
    df = metrics_df.copy()
    for col in FEATURES:
        df[col + "_z"] = df.groupby("sector")[col].transform(
            lambda s: (s - s.mean()) / s.std(ddof=0) if (len(s) >= MIN_PEERS_FOR_ZSCORE and s.std(ddof=0) > 0) else 0.0
        )
    return df


def run_isolation_forest(metrics_df: pd.DataFrame, contamination=0.2, random_state=42):
    """metrics_df must contain 'entity_id', 'sector' plus FEATURES. Returns
    (scores dict, explanations dict) keyed by entity_id."""
    if len(metrics_df) < 6:
        # too few entities overall for any model to be meaningful
        return {r.entity_id: 0.0 for _, r in metrics_df.iterrows()}, {r.entity_id: [] for _, r in metrics_df.iterrows()}

    zdf = _zscore_within_sector(metrics_df)
    zcols = [c + "_z" for c in FEATURES]
    X = zdf[zcols].fillna(0).values

    model = IsolationForest(n_estimators=300, contamination=contamination, random_state=random_state)
    model.fit(X)
    decision = model.decision_function(X)   # higher = more normal

    scores = {}
    explanations = {}
    for i, (_, row) in enumerate(zdf.iterrows()):
        raw_anomaly = -decision[i]                                  # higher = more anomalous
        scaled = max(0.0, min(100.0, (raw_anomaly + 0.05) * CALIBRATION_SCALE))
        scores[row.entity_id] = round(float(scaled), 1)

        z_row = X[i]
        order = np.argsort(-np.abs(z_row))[:3]
        feats = []
        for idx in order:
            if abs(z_row[idx]) < 1.0:
                continue
            fname = FEATURES[idx]
            sector_vals = metrics_df.loc[metrics_df.sector == row.sector, fname]
            feats.append({
                "feature": fname,
                "label": FEATURE_LABELS[fname],
                "z_score": round(float(z_row[idx]), 2),
                "direction": "above" if z_row[idx] > 0 else "below",
                "entity_value": round(float(row[fname]), 2),
                "peer_mean": round(float(sector_vals.mean()), 2),
            })
        explanations[row.entity_id] = feats

    return scores, explanations
