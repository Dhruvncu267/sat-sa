"""
SAT-SA synthetic data generator
=================================
Generates a realistic (but fully synthetic) SOC alert / case-management
dataset spanning multiple Critical Sector Entities (CSEs), for prototyping
the Supervisory Analytics Tool for SOC Assessment (SAT-SA).

This stands in for the "periodic submissions" described in the problem
statement (alert metadata, case management records, investigation workflow
data, escalation records, disposition/closure info, asset inventory).

Several entities have INTENTIONALLY PLANTED weaknesses (execution gaps and
negative space) so the detection engine has known-good cases to catch --
useful for demoing / validating the tool. Everything else is randomised
"normal" background behaviour used for peer benchmarking.

Run:  python3 generate_data.py
Output: alerts.csv, cases.csv, escalations.csv, assets.csv, entities.csv
        written to ../output/
"""
import csv
import os
import random
from datetime import datetime, timedelta

random.seed(42)

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "output")
os.makedirs(OUT, exist_ok=True)

PERIOD_DAYS = 90
START = datetime(2026, 6, 1)

SEVERITIES = ["Critical", "High", "Medium", "Low"]
SEV_WEIGHTS = [0.08, 0.17, 0.35, 0.40]

CATEGORY_BY_ASSET = {
    "Core Banking Server": ["Unauthorized Access", "Data Exfiltration", "Malware", "Privilege Escalation"],
    "Payment Gateway": ["Fraudulent Transaction Pattern", "Unauthorized Access", "DDoS", "API Abuse"],
    "Database Server": ["Data Exfiltration", "SQL Injection Attempt", "Unauthorized Access", "Malware"],
    "Email Gateway": ["Phishing", "Malware", "Spam Campaign", "Business Email Compromise"],
    "Web Application": ["SQL Injection Attempt", "DDoS", "Web Defacement Attempt", "API Abuse"],
    "Domain Controller": ["Privilege Escalation", "Unauthorized Access", "Lateral Movement", "Malware"],
    "Endpoint Pool": ["Malware", "Phishing", "Ransomware Indicator", "Policy Violation"],
    "Network Perimeter (FW/IDS)": ["Port Scan", "DDoS", "Intrusion Attempt", "Lateral Movement"],
    "SCADA/OT Gateway": ["Unauthorized Access", "Anomalous Command Sequence", "Malware", "Protocol Anomaly"],
    "VPN/Remote Access": ["Unauthorized Access", "Credential Stuffing", "Anomalous Login Location"],
}

ASSET_CRITICALITY = {
    "Core Banking Server": "Critical", "Payment Gateway": "Critical", "Database Server": "Critical",
    "Email Gateway": "High", "Web Application": "High", "Domain Controller": "Critical",
    "Endpoint Pool": "Medium", "Network Perimeter (FW/IDS)": "High", "SCADA/OT Gateway": "Critical",
    "VPN/Remote Access": "High",
}

# entity_id, name, sector, tier, asset_types, behaviour_profile
# NOTE: each sector has >=3 entities so peer (z-score) benchmarking is
# statistically meaningful -- with only 2 peers a z-score is trivially +-1
# regardless of magnitude, which would hide real outliers.
ENTITIES = [
    ("CSE01", "SecureBank Ltd",        "Banking",         "Tier-1", ["Core Banking Server", "Payment Gateway", "Database Server", "Email Gateway", "Endpoint Pool", "Network Perimeter (FW/IDS)"], "normal"),
    ("CSE02", "TrustFin Bank",         "Banking",         "Tier-1", ["Core Banking Server", "Payment Gateway", "Database Server", "Email Gateway", "Endpoint Pool", "VPN/Remote Access"], "normal"),
    ("CSE03", "RapidPay Fintech",      "Payments",        "Tier-2", ["Payment Gateway", "Database Server", "Web Application", "Endpoint Pool"], "quick_closure_no_escalation"),
    ("CSE04", "MetroGrid Power",       "Power",           "Tier-1", ["SCADA/OT Gateway", "Domain Controller", "Endpoint Pool", "Network Perimeter (FW/IDS)"], "missing_ot_telemetry"),
    ("CSE05", "ConnectTel Telecom",    "Telecom",         "Tier-1", ["Network Perimeter (FW/IDS)", "Domain Controller", "Database Server", "Endpoint Pool", "VPN/Remote Access"], "repeat_no_remediation"),
    ("CSE06", "ApexInsure",            "Insurance",       "Tier-2", ["Database Server", "Web Application", "Email Gateway", "Endpoint Pool"], "no_escalation_records"),
    ("CSE07", "NorthState Power Corp", "Power",           "Tier-1", ["SCADA/OT Gateway", "Domain Controller", "Endpoint Pool", "Network Perimeter (FW/IDS)"], "normal"),
    ("CSE08", "UnityTelecom",          "Telecom",         "Tier-2", ["Network Perimeter (FW/IDS)", "Domain Controller", "Database Server", "Endpoint Pool"], "normal"),
    ("CSE09", "CentralBank Cooperative","Banking",        "Tier-2", ["Core Banking Server", "Database Server", "Email Gateway", "Endpoint Pool"], "template_investigations"),
    ("CSE10", "SwiftPay Wallets",      "Payments",        "Tier-2", ["Payment Gateway", "Web Application", "Database Server", "Endpoint Pool"], "low_activity_blindspot"),
    ("CSE11", "Bank of Union",         "Banking",         "Tier-2", ["Core Banking Server", "Database Server", "Email Gateway", "Endpoint Pool"], "normal"),
    ("CSE12", "PayEasy Systems",       "Payments",        "Tier-2", ["Payment Gateway", "Database Server", "Web Application", "Endpoint Pool"], "normal"),
    ("CSE13", "GridSecure Power",      "Power",           "Tier-2", ["SCADA/OT Gateway", "Domain Controller", "Endpoint Pool", "Network Perimeter (FW/IDS)"], "normal"),
    ("CSE14", "Bharat Telecom Networks","Telecom",        "Tier-2", ["Network Perimeter (FW/IDS)", "Domain Controller", "Database Server", "Endpoint Pool"], "normal"),
    ("CSE15", "SafeCover Insurance",   "Insurance",       "Tier-2", ["Database Server", "Web Application", "Email Gateway", "Endpoint Pool"], "normal"),
    ("CSE16", "National Assurance Co", "Insurance",       "Tier-2", ["Database Server", "Web Application", "Email Gateway", "Endpoint Pool"], "normal"),
    ("CSE17", "Metro Urban Bank",      "Banking",         "Tier-2", ["Core Banking Server", "Database Server", "Email Gateway", "Endpoint Pool"], "normal"),
    ("CSE18", "QuickCash Digital",     "Payments",        "Tier-2", ["Payment Gateway", "Database Server", "Web Application", "Endpoint Pool"], "normal"),
    ("CSE19", "InstaTransfer Ltd",     "Payments",        "Tier-2", ["Payment Gateway", "Web Application", "Database Server", "Endpoint Pool"], "normal"),
    ("CSE20", "Eastern Power Utility", "Power",           "Tier-1", ["SCADA/OT Gateway", "Domain Controller", "Endpoint Pool", "Network Perimeter (FW/IDS)"], "normal"),
    ("CSE21", "Sunrise Energy Grid",   "Power",           "Tier-2", ["SCADA/OT Gateway", "Domain Controller", "Endpoint Pool", "Network Perimeter (FW/IDS)"], "normal"),
    ("CSE22", "Vardhan Telecom",       "Telecom",         "Tier-2", ["Network Perimeter (FW/IDS)", "Domain Controller", "Database Server", "Endpoint Pool"], "normal"),
    ("CSE23", "Skyline Communications","Telecom",         "Tier-2", ["Network Perimeter (FW/IDS)", "Domain Controller", "Database Server", "Endpoint Pool"], "normal"),
    ("CSE24", "Reliant Life Insurance","Insurance",       "Tier-2", ["Database Server", "Web Application", "Email Gateway", "Endpoint Pool"], "normal"),
    ("CSE25", "Horizon General Insurance","Insurance",    "Tier-2", ["Database Server", "Web Application", "Email Gateway", "Endpoint Pool"], "normal"),
]


def write_csv(path, rows, header):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def rand_ts(day_offset_max=PERIOD_DAYS):
    return START + timedelta(days=random.uniform(0, day_offset_max), hours=random.uniform(0, 24))


def gen():
    entities_rows = []
    assets_rows = []
    alerts_rows = []
    cases_rows = []
    escalations_rows = []

    alert_id_ctr = 1
    case_id_ctr = 1
    esc_id_ctr = 1

    for entity_id, name, sector, tier, asset_types, profile in ENTITIES:
        entities_rows.append([entity_id, name, sector, tier, profile])

        # ---- assets ----
        entity_assets = []
        for i, atype in enumerate(asset_types):
            n_instances = random.randint(1, 3)
            for k in range(n_instances):
                asset_id = f"{entity_id}-A{i}{k}"
                crit = ASSET_CRITICALITY[atype]
                assets_rows.append([asset_id, entity_id, atype, crit])
                entity_assets.append((asset_id, atype, crit))

        # base alert volume per asset per profile
        base_alert_count = random.randint(35, 70)
        if profile == "low_activity_blindspot":
            base_alert_count = random.randint(6, 12)

        for (asset_id, atype, crit) in entity_assets:
            # negative space: MetroGrid's SCADA/OT gateway gets almost no alerts
            if profile == "missing_ot_telemetry" and atype == "SCADA/OT Gateway":
                n_alerts = random.randint(0, 1)
            elif profile == "low_activity_blindspot":
                n_alerts = random.randint(1, 4)
            else:
                n_alerts = random.randint(max(3, base_alert_count // len(entity_assets) - 3),
                                           base_alert_count // len(entity_assets) + 3)

            categories = CATEGORY_BY_ASSET[atype]

            # negative space: ApexInsure's Email Gateway never sees Phishing alerts
            avail_categories = categories
            if profile == "no_escalation_records" and atype == "Email Gateway":
                avail_categories = [c for c in categories if c != "Phishing"]

            recurring_category = random.choice(categories) if profile == "repeat_no_remediation" else None

            # entities with a "no escalation" weakness need a reliable supply
            # of critical/high true-positive alerts for the gap to be visible
            sev_weights = [0.30, 0.30, 0.25, 0.15] if profile == "no_escalation_records" else SEV_WEIGHTS
            disp_weights = [0.55, 0.25, 0.20] if profile == "no_escalation_records" else [0.35, 0.35, 0.30]

            for _ in range(n_alerts):
                ts = rand_ts()
                sev = random.choices(SEVERITIES, weights=sev_weights)[0]
                cat = recurring_category if (recurring_category and random.random() < 0.5) else random.choice(avail_categories)
                disposition = random.choices(
                    ["True Positive", "False Positive", "Benign"], weights=disp_weights
                )[0]

                alert_id = f"AL{alert_id_ctr:06d}"
                alert_id_ctr += 1

                # -------- case / investigation behaviour per profile --------
                investigated = True
                escalated = False
                root_cause = random.random() < 0.55
                notes_len = random.randint(120, 480)  # characters, proxy for depth of investigation
                closure_minutes = random.randint(30, 2880)  # 30 min - 2 days baseline
                escalation_minutes = None

                is_critical_tp = sev in ("Critical", "High") and disposition == "True Positive"

                if profile == "quick_closure_no_escalation" and is_critical_tp:
                    closure_minutes = random.randint(1, 10)     # closed almost instantly
                    escalated = False
                    root_cause = False
                    notes_len = random.randint(20, 60)
                elif profile == "repeat_no_remediation" and cat == recurring_category:
                    root_cause = False                           # never actually fixed
                    escalated = random.random() < 0.15
                    closure_minutes = random.randint(60, 400)
                elif profile == "no_escalation_records":
                    escalated = False                             # this entity essentially never escalates
                elif profile == "template_investigations":
                    notes_len = random.randint(30, 70)            # short boilerplate notes
                    escalated = is_critical_tp and random.random() < 0.5
                else:
                    if is_critical_tp:
                        escalated = random.random() < 0.8
                    else:
                        escalated = random.random() < 0.05

                if escalated:
                    escalation_minutes = random.randint(5, 240)

                alerts_rows.append([
                    alert_id, entity_id, asset_id, atype, ts.isoformat(timespec="minutes"),
                    sev, cat, disposition, "Closed" if investigated else "Open",
                ])

                case_id = f"CS{case_id_ctr:06d}"
                case_id_ctr += 1
                opened_at = ts + timedelta(minutes=random.randint(1, 30))
                closed_at = opened_at + timedelta(minutes=closure_minutes)

                cases_rows.append([
                    case_id, alert_id, entity_id, opened_at.isoformat(timespec="minutes"),
                    closed_at.isoformat(timespec="minutes"), closure_minutes,
                    "Yes" if escalated else "No", "Yes" if root_cause else "No", notes_len,
                ])

                if escalated:
                    esc_id = f"ES{esc_id_ctr:06d}"
                    esc_id_ctr += 1
                    esc_at = opened_at + timedelta(minutes=escalation_minutes)
                    escalations_rows.append([
                        esc_id, case_id, entity_id, esc_at.isoformat(timespec="minutes"),
                        escalation_minutes, sev, "L2 Supervisor" if sev != "Critical" else "CISO Office",
                    ])

    write_csv(os.path.join(OUT, "entities.csv"), entities_rows,
              ["entity_id", "name", "sector", "tier", "_sim_profile"])
    write_csv(os.path.join(OUT, "assets.csv"), assets_rows,
              ["asset_id", "entity_id", "asset_type", "criticality"])
    write_csv(os.path.join(OUT, "alerts.csv"), alerts_rows,
              ["alert_id", "entity_id", "asset_id", "asset_type", "timestamp", "severity",
               "category", "disposition", "status"])
    write_csv(os.path.join(OUT, "cases.csv"), cases_rows,
              ["case_id", "alert_id", "entity_id", "opened_at", "closed_at", "closure_minutes",
               "escalated", "root_cause_identified", "investigation_notes_length"])
    write_csv(os.path.join(OUT, "escalations.csv"), escalations_rows,
              ["escalation_id", "case_id", "entity_id", "escalated_at", "escalation_minutes",
               "severity_at_escalation", "escalated_to"])

    print(f"Generated: {len(entities_rows)} entities, {len(assets_rows)} assets, "
          f"{len(alerts_rows)} alerts, {len(cases_rows)} cases, {len(escalations_rows)} escalations")
    print(f"Written to: {OUT}")


if __name__ == "__main__":
    gen()
