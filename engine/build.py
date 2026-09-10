"""
SAT-SA offline build (static, zero-dependency-to-VIEW mode)
==============================================================
Runs the full pipeline (rules + ML) and writes a single self-contained
dashboard.html with the results baked in -- open it directly in a browser,
no server, no internet, nothing to install to VIEW it (you still need
pandas + scikit-learn installed to GENERATE it).

This is the "hand a judge a file that just works" mode. For the full
running application (live upload of new CSE data, REST API, etc.) see
server/app.py instead.

Run:  python3 build.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pipeline import run_pipeline

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "output")


def render_dashboard(analysis):
    template_path = os.path.join(HERE, "..", "dashboard_template.html")
    with open(template_path, "r") as f:
        template = f.read()
    injected = template.replace(
        "/*__SAT_SA_DATA__*/",
        "window.__SAT_SA_DATA__ = " + json.dumps(analysis) + ";"
    )
    out_path = os.path.join(OUT, "dashboard.html")
    with open(out_path, "w") as f:
        f.write(injected)


def build():
    analysis = run_pipeline(OUT)

    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "analysis.json"), "w") as f:
        json.dump(analysis, f, indent=2)

    render_dashboard(analysis)

    print(f"Entities analysed: {len(analysis['entities'])}")
    print(f"Total flags raised: {analysis['totals']['flags_raised']}")
    for e in analysis["entities"]:
        t = e["technical"]
        print(f"  {e['entity_id']:6s} {e['name']:26s} risk={e['risk_score']:3d} "
              f"(rule={t['rule_score']:3d} ml={t['ml_anomaly_score']:5.1f}) "
              f"{e['risk_label']:8s} flags={e['n_flags']}")
    print(f"\nWritten: {os.path.join(OUT, 'analysis.json')}")
    print(f"Written: {os.path.join(OUT, 'dashboard.html')}")


if __name__ == "__main__":
    build()
