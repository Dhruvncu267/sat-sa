"""
SAT-SA detection engine
========================
Rule-based detectors for the two supervisory weakness categories defined in
the problem statement:

  A. Execution Gaps  -- documented/reported capability looks fine, but
     operational evidence (alerts/cases/escalations) says otherwise.
  B. Negative Space   -- expected evidence is simply ABSENT.

Every detector returns Flag objects that carry a plain-language rationale
and a pointer to the underlying evidence rows, so a supervisor can always
see *why* something was flagged (explainability / auditability requirement
in section 4 of the PS).

NOTE: detectors only ever look at alerts/cases/escalations/assets -- never
at the "_sim_profile" ground-truth column in entities.csv. That column
exists purely so we can validate detector accuracy against known planted
anomalies; a real deployment would not have it.
"""
from dataclasses import dataclass, field
from typing import List, Dict, Any
import pandas as pd


@dataclass
class Flag:
    entity_id: str
    rule_id: str
    category: str          # "Execution Gap" | "Negative Space"
    title: str
    rationale: str
    weight: int             # contribution to composite risk score
    evidence: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self):
        return {
            "rule_id": self.rule_id,
            "category": self.category,
            "title": self.title,
            "rationale": self.rationale,
            "weight": self.weight,
            "evidence": self.evidence,
        }


# ---------------------------------------------------------------- helpers --
def _evi(df: pd.DataFrame, cols, limit=8):
    """Trim a dataframe to a small, JSON-friendly evidence sample."""
    return df[cols].head(limit).to_dict(orient="records")


# =====================================================  EXECUTION GAPS  ===

def eg_quick_closure_no_escalation(alerts, cases, entity_id, min_minutes=15):
    """EG1: Critical/High True-Positive alerts closed almost instantly with
    no escalation -- looks like the alert was dismissed rather than
    investigated."""
    df = alerts.merge(cases, on=["alert_id", "entity_id"])
    df = df[(df.entity_id == entity_id) &
            (df.severity.isin(["Critical", "High"])) &
            (df.disposition == "True Positive") &
            (df.closure_minutes <= min_minutes) &
            (df.escalated == "No")]
    if len(df) < 3:
        return None
    return Flag(
        entity_id=entity_id, rule_id="EG1", category="Execution Gap",
        title="High-severity alerts closed unusually quickly, without escalation",
        rationale=(f"{len(df)} Critical/High-severity, confirmed true-positive alerts were closed "
                    f"within {min_minutes} minutes and never escalated. This closure speed is not "
                    f"consistent with meaningful investigation and suggests alerts may be dismissed "
                    f"to satisfy closure-time metrics rather than resolved."),
        weight=15,
        evidence=_evi(df, ["alert_id", "asset_type", "severity", "category", "closure_minutes", "timestamp"]),
    )


def eg_critical_no_escalation(alerts, cases, entity_id):
    """EG2: Critical alerts confirmed true-positive but never escalated at all."""
    df = alerts.merge(cases, on=["alert_id", "entity_id"])
    df = df[(df.entity_id == entity_id) &
            (df.severity == "Critical") &
            (df.disposition == "True Positive") &
            (df.escalated == "No")]
    if len(df) < 2:
        return None
    return Flag(
        entity_id=entity_id, rule_id="EG2", category="Execution Gap",
        title="Critical alerts closed without escalation",
        rationale=(f"{len(df)} confirmed Critical-severity alerts were closed without any escalation "
                    f"record. Per standard SOC practice, critical incidents should be escalated for "
                    f"supervisory / management visibility regardless of how they are ultimately resolved."),
        weight=20,
        evidence=_evi(df, ["alert_id", "asset_type", "category", "closure_minutes", "timestamp"]),
    )


def eg_repeat_no_remediation(alerts, cases, entity_id, min_repeats=3):
    """EG3: Same alert category recurring on the same asset repeatedly, with
    no case ever reaching root-cause identification -- a persistent issue
    that keeps getting reopened/rediscovered rather than fixed. Restricted
    to confirmed True Positive incidents -- false positives/benign alerts
    are not expected to have a "root cause remediated"."""
    df = alerts.merge(cases, on=["alert_id", "entity_id"])
    df = df[(df.entity_id == entity_id) & (df.disposition == "True Positive")]
    grp = df.groupby(["asset_id", "category"])
    flagged_evidence = []
    n_groups = 0
    for (asset_id, category), g in grp:
        if len(g) >= min_repeats and (g.root_cause_identified == "No").all():
            n_groups += 1
            flagged_evidence.extend(
                g[["alert_id", "asset_id", "category", "timestamp", "root_cause_identified"]]
                .head(3).to_dict(orient="records")
            )
    if n_groups == 0:
        return None
    return Flag(
        entity_id=entity_id, rule_id="EG3", category="Execution Gap",
        title="Repeated alerts on the same asset without root-cause remediation",
        rationale=(f"Found {n_groups} asset/alert-category combination(s) with {min_repeats}+ recurring "
                    f"alerts where root cause was never identified in any related case. This pattern "
                    f"indicates the underlying issue is being repeatedly detected but not actually fixed."),
        weight=15,
        evidence=flagged_evidence[:8],
    )


def eg_template_investigations(cases, alerts, entity_id, peer_median_notes, ratio_threshold=0.4, min_cases=15):
    """EG4: Investigation notes are suspiciously short/uniform compared to
    peers -- a proxy for template-driven, superficial review."""
    df = cases[cases.entity_id == entity_id]
    if len(df) < min_cases or peer_median_notes <= 0:
        return None
    entity_median = df.investigation_notes_length.median()
    if entity_median >= peer_median_notes * ratio_threshold:
        return None
    sample = df.sort_values("investigation_notes_length").head(6)
    return Flag(
        entity_id=entity_id, rule_id="EG4", category="Execution Gap",
        title="Investigation documentation suggests template-driven / superficial review",
        rationale=(f"Median investigation note length is {int(entity_median)} characters, versus a peer "
                    f"median of {int(peer_median_notes)} characters ({entity_median/peer_median_notes:.0%} of "
                    f"peer norm) across {len(df)} cases. Consistently short, generic case documentation is a "
                    f"known indicator of boilerplate/template-driven review rather than substantive investigation."),
        weight=10,
        evidence=_evi(sample, ["case_id", "alert_id", "investigation_notes_length"]),
    )


def eg_escalation_delay(cases, escalations, entity_id, peer_median_esc_minutes, ratio_threshold=2.0, min_n=5):
    """EG5: Escalation times for this entity are far slower than peers."""
    df = escalations[escalations.entity_id == entity_id]
    if len(df) < min_n or peer_median_esc_minutes <= 0:
        return None
    entity_median = df.escalation_minutes.median()
    if entity_median < peer_median_esc_minutes * ratio_threshold:
        return None
    sample = df.sort_values("escalation_minutes", ascending=False).head(6)
    return Flag(
        entity_id=entity_id, rule_id="EG5", category="Execution Gap",
        title="Escalation delays significantly exceed peer norms",
        rationale=(f"Median time-to-escalation is {int(entity_median)} minutes across {len(df)} escalations, "
                    f"vs a peer median of {int(peer_median_esc_minutes)} minutes -- "
                    f"{entity_median/peer_median_esc_minutes:.1f}x slower. Slow escalation reduces the "
                    f"window for effective incident response on issues that were judged serious enough to escalate."),
        weight=10,
        evidence=_evi(sample, ["escalation_id", "case_id", "escalation_minutes", "severity_at_escalation"]),
    )


# ======================================================  NEGATIVE SPACE ===

def ns_missing_telemetry(alerts, assets, entity_id):
    """NS1: A critical/high-criticality asset produced zero (or near-zero)
    alerts over the whole review period -- a monitoring blind spot."""
    ent_assets = assets[assets.entity_id == entity_id]
    counts = alerts[alerts.entity_id == entity_id].groupby("asset_id").size()
    silent = []
    for _, row in ent_assets.iterrows():
        if row.criticality in ("Critical", "High"):
            n = counts.get(row.asset_id, 0)
            if n <= 1:
                silent.append({"asset_id": row.asset_id, "asset_type": row.asset_type,
                                "criticality": row.criticality, "alert_count": int(n)})
    if not silent:
        return None
    return Flag(
        entity_id=entity_id, rule_id="NS1", category="Negative Space",
        title="Critical/high-criticality assets generating little or no security telemetry",
        rationale=(f"{len(silent)} asset(s) rated Critical/High criticality produced 0-1 alerts across the "
                    f"entire {90}-day review window. For assets of this criticality, near-total silence is "
                    f"itself a supervisory signal -- either the asset is genuinely quiet (unlikely at scale) "
                    f"or monitoring coverage/log ingestion for it is broken or absent."),
        weight=15,
        evidence=silent,
    )


def ns_no_escalation_records(alerts, cases, escalations, entity_id, min_critical_alerts=3):
    """NS2: Entity has meaningful critical/high true-positive alert volume
    but essentially zero escalation records -- escalation process may not
    exist in practice, even if documented."""
    df = alerts.merge(cases, on=["alert_id", "entity_id"])
    crit_tp = df[(df.entity_id == entity_id) &
                 (df.severity.isin(["Critical", "High"])) &
                 (df.disposition == "True Positive")]
    esc_count = len(escalations[escalations.entity_id == entity_id])
    if len(crit_tp) < min_critical_alerts or esc_count > max(1, len(crit_tp) * 0.05):
        return None
    return Flag(
        entity_id=entity_id, rule_id="NS2", category="Negative Space",
        title="Absence of escalation records despite critical alert activity",
        rationale=(f"{len(crit_tp)} Critical/High true-positive alerts were recorded, but only "
                    f"{esc_count} escalation record(s) exist for this entity overall. This gap between "
                    f"alert severity and escalation activity suggests the escalation process may not be "
                    f"functioning in practice, regardless of what policy documents describe."),
        weight=20,
        evidence=_evi(crit_tp.sort_values("timestamp", ascending=False),
                       ["alert_id", "asset_type", "category", "timestamp"]),
    )


def ns_low_activity_vs_peers(alert_rate_by_entity, entity_id, sector, z_threshold=-1.2):
    """NS3: Alerts-per-asset for this entity is a statistical outlier (far
    below) its sector peer group."""
    sector_rates = alert_rate_by_entity[alert_rate_by_entity.sector == sector]
    if len(sector_rates) < 3:
        return None
    mean = sector_rates.alerts_per_asset.mean()
    std = sector_rates.alerts_per_asset.std(ddof=0)
    row = sector_rates[sector_rates.entity_id == entity_id]
    if row.empty or std == 0:
        return None
    rate = row.alerts_per_asset.iloc[0]
    z = (rate - mean) / std
    if z > z_threshold:
        return None
    return Flag(
        entity_id=entity_id, rule_id="NS3", category="Negative Space",
        title="Alert volume significantly below sector peers",
        rationale=(f"This entity generates {rate:.1f} alerts per monitored asset over the review period, "
                    f"versus a {sector} sector peer average of {mean:.1f} (z-score {z:.2f}). Unusually low "
                    f"detection volume relative to comparable entities can indicate under-tuned detection "
                    f"content, restricted log/telemetry sources, or genuine under-monitoring."),
        weight=10,
        evidence=[{"entity_id": entity_id, "alerts_per_asset": round(rate, 2),
                    "sector_avg": round(mean, 2), "z_score": round(z, 2)}],
    )


def ns_missing_expected_category(alerts, assets, entity_id):
    """NS4: An asset type that should typically generate a certain alert
    category (e.g. Email Gateway -> Phishing) shows zero such alerts."""
    expectations = {"Email Gateway": "Phishing", "Web Application": "SQL Injection Attempt",
                     "VPN/Remote Access": "Credential Stuffing", "SCADA/OT Gateway": "Anomalous Command Sequence"}
    ent_assets = assets[assets.entity_id == entity_id]
    ent_alerts = alerts[alerts.entity_id == entity_id]
    missing = []
    for atype, expected_cat in expectations.items():
        if (ent_assets.asset_type == atype).any():
            n = len(ent_alerts[(ent_alerts.asset_type == atype) & (ent_alerts.category == expected_cat)])
            n_total_on_type = len(ent_alerts[ent_alerts.asset_type == atype])
            if n == 0 and n_total_on_type >= 5:
                missing.append({"asset_type": atype, "expected_category": expected_cat,
                                 "alerts_seen_on_asset_type": n_total_on_type})
    if not missing:
        return None
    return Flag(
        entity_id=entity_id, rule_id="NS4", category="Negative Space",
        title="Absence of an expected alert category for asset type present",
        rationale=(f"For {len(missing)} asset type(s), a commonly-expected alert category was never "
                    f"observed despite meaningful overall alert volume on that asset type. Total absence of "
                    f"an expected category (rather than a low rate) can indicate a detection content gap "
                    f"rather than genuine absence of that activity."),
        weight=10,
        evidence=missing,
    )
