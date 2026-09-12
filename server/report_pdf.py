"""
SOCAssure supervisory report -- fixed professional PDF format
==============================================================
Builds the exact same downloadable report every time, from whatever
assessment snapshot (rules + ML + score, already computed by the untouched
engine) is passed in. This file only formats data that already exists --
it does not calculate anything itself.
"""
import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable, PageBreak,
)

SUPERVISORY_ATTENTION = {
    "Critical": "Immediate supervisory attention required. Recommend a full audit and direct "
                "engagement with this organization's SOC leadership before the next review cycle.",
    "High": "Elevated concern. Recommend a focused review of the specific findings below, and "
            "confirmation of remediation before the next assessment.",
    "Medium": "Some concerns noted. Recommend routine follow-up at the next assessment to confirm "
               "these patterns are not worsening.",
    "Low": "No significant concerns identified in this review period. Routine monitoring is sufficient.",
}


def _styles():
    ss = getSampleStyleSheet()
    ss.add(ParagraphStyle("SOCTitle", parent=ss["Title"], fontSize=20, textColor=colors.HexColor("#16233f"), spaceAfter=2))
    ss.add(ParagraphStyle("SATSubtitle", parent=ss["Normal"], fontSize=10.5, textColor=colors.HexColor("#5d719a"), spaceAfter=14))
    ss.add(ParagraphStyle("SectionHead", parent=ss["Heading2"], fontSize=12.5, textColor=colors.HexColor("#16233f"),
                           spaceBefore=16, spaceAfter=6, borderPadding=0))
    ss.add(ParagraphStyle("Body", parent=ss["Normal"], fontSize=10, leading=14.5, textColor=colors.HexColor("#222")))
    ss.add(ParagraphStyle("BodyDim", parent=ss["Normal"], fontSize=9, leading=13, textColor=colors.HexColor("#666")))
    ss.add(ParagraphStyle("ScoreBig", parent=ss["Normal"], fontSize=34, leading=36, textColor=colors.HexColor("#16233f")))
    ss.add(ParagraphStyle("SATBullet", parent=ss["Normal"], fontSize=10, leading=14.5, leftIndent=12, spaceAfter=5))
    return ss


_LABEL_COLOR = {
    "Critical": colors.HexColor("#c0392b"), "High": colors.HexColor("#d68a1c"),
    "Medium": colors.HexColor("#b8960c"), "Low": colors.HexColor("#1e8e5a"),
}


def build_report_pdf(entity, assessment_date, generated_at, review_period_days):
    """entity: one entity dict exactly as produced by engine/pipeline.py
    (same shape whether it came live or from a saved Firestore snapshot).
    Returns a BytesIO ready to send as a file download."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=22 * mm, bottomMargin=18 * mm,
                             leftMargin=20 * mm, rightMargin=20 * mm)
    ss = _styles()
    story = []
    t = entity.get("technical", {})
    label = entity.get("risk_label", "Low")

    # ---- Header -----------------------------------------------------
    story.append(Paragraph("SOCAssure Supervisory Assessment Report", ss["SOCTitle"]))
    story.append(Paragraph("Supervisory Analytics Tool for SOC Assessment &middot; SIH 2026, Problem Statement 26157",
                            ss["SATSubtitle"]))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#dfe4ee")))

    # ---- 1. Organization details -------------------------------------
    story.append(Paragraph("Organization Details", ss["SectionHead"]))
    org_table = Table([
        ["Organization", entity.get("name", "")],
        ["Sector", entity.get("sector", "")],
        ["Tier", entity.get("tier", "")],
        ["Internal reference ID", entity.get("entity_id", "")],
        ["Assessment date", assessment_date],
        ["Report generated", generated_at],
        ["Review period covered", f"{review_period_days} days"],
    ], colWidths=[55 * mm, 110 * mm])
    org_table.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#5d719a")),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, colors.HexColor("#eef1f7")),
    ]))
    story.append(org_table)

    # ---- 2. Score / status -------------------------------------------
    story.append(Paragraph("Assessment Score", ss["SectionHead"]))
    score_row = Table([[
        Paragraph(f"{entity.get('risk_score', 0)}<font size=12>/100</font>", ss["ScoreBig"]),
        Paragraph(f"<b>{entity.get('simple_label', '')}</b><br/>{SUPERVISORY_ATTENTION.get(label, '')}", ss["Body"]),
    ]], colWidths=[38 * mm, 127 * mm])
    score_row.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    story.append(score_row)

    # ---- 3. Why this score was given ----------------------------------
    story.append(Paragraph("Why This Organization Received This Score", ss["SectionHead"]))
    reasons = entity.get("plain_reasons", [])
    if reasons:
        for r in reasons:
            story.append(Paragraph(f"&bull;&nbsp; {r}", ss["SATBullet"]))
    else:
        story.append(Paragraph("No concerning patterns were found in the data reviewed.", ss["Body"]))

    # ---- 4. Execution gap findings ------------------------------------
    eg_flags = [f for f in t.get("flags", []) if f.get("category") == "Execution Gap"]
    story.append(Paragraph("Execution Gap Findings", ss["SectionHead"]))
    story.append(Paragraph(
        "Execution gaps: cases where the organization's documented process looks fine, but what "
        "actually happened (timing, escalation, follow-through) tells a different story.", ss["BodyDim"]))
    if eg_flags:
        for f in eg_flags:
            story.append(Paragraph(f"<b>{f['title']}</b> &mdash; {f['rationale']}", ss["SATBullet"]))
    else:
        story.append(Paragraph("None found.", ss["Body"]))

    # ---- 5. Negative space findings ------------------------------------
    ns_flags = [f for f in t.get("flags", []) if f.get("category") == "Negative Space"]
    story.append(Paragraph("Negative-Space Findings", ss["SectionHead"]))
    story.append(Paragraph(
        "Negative space: evidence that should exist but is simply missing -- for example, a critical "
        "system that generates almost no alerts, which can mean monitoring itself is broken.", ss["BodyDim"]))
    if ns_flags:
        for f in ns_flags:
            story.append(Paragraph(f"<b>{f['title']}</b> &mdash; {f['rationale']}", ss["SATBullet"]))
    else:
        story.append(Paragraph("None found.", ss["Body"]))

    # ---- 6. ML analysis --------------------------------------------------
    story.append(Paragraph("AI / Machine-Learning Analysis", ss["SectionHead"]))
    story.append(Paragraph(
        f"An Isolation Forest model compared this organization's overall numbers against similar "
        f"organizations in the same sector and produced an anomaly score of "
        f"<b>{t.get('ml_anomaly_score', 0)}/100</b>. This is combined with the rule-based score above "
        f"(75% rules / 25% ML) into the final score.", ss["Body"]))
    ml_insights = t.get("ml_insights", [])
    if ml_insights:
        for ins in ml_insights:
            story.append(Paragraph(
                f"&bull;&nbsp; {ins.get('label', '')}: this organization = {ins.get('entity_value')}, "
                f"peer average = {ins.get('peer_mean')} (z-score {ins.get('z_score')})", ss["SATBullet"]))

    # ---- 7. Alert analysis ------------------------------------------------
    story.append(Paragraph("Alert Data Analysis", ss["SectionHead"]))
    story.append(Paragraph(
        f"{entity.get('n_alerts', 0)} alerts reviewed across {entity.get('n_assets', 0)} monitored "
        f"assets during the review period.", ss["Body"]))
    cats = entity.get("category_breakdown", [])
    if cats:
        cat_table = Table([["Alert category", "Count"]] + [[c["category"], str(c["count"])] for c in cats],
                           colWidths=[120 * mm, 45 * mm])
        cat_table.setStyle(TableStyle([
            ("FONTSIZE", (0, 0), (-1, -1), 9.5),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef1f7")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4), ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#eef1f7")),
        ]))
        story.append(cat_table)

    # ---- 8. Supervisory attention required --------------------------------
    story.append(Paragraph("Supervisory Attention Required", ss["SectionHead"]))
    story.append(Paragraph(SUPERVISORY_ATTENTION.get(label, ""), ss["Body"]))

    # ---- 9. Technical details -----------------------------------------------
    story.append(PageBreak())
    story.append(Paragraph("Technical Details (for auditors / judges)", ss["SectionHead"]))
    story.append(Paragraph(
        f"Rule-based component: {t.get('rule_score', 0)} &nbsp;&middot;&nbsp; "
        f"ML anomaly component: {t.get('ml_anomaly_score', 0)} &nbsp;&middot;&nbsp; "
        f"Combined 75% rules / 25% ML.", ss["Body"]))
    for f in t.get("flags", []):
        story.append(Paragraph(f"<b>[{f['rule_id']}] {f['category']}: {f['title']}</b>", ss["Body"]))
        story.append(Paragraph(f['rationale'], ss["BodyDim"]))
        for ev in f.get("evidence", [])[:6]:
            story.append(Paragraph("&nbsp;&nbsp;- " + ", ".join(f"{k}: {v}" for k, v in ev.items()), ss["BodyDim"]))
        story.append(Spacer(1, 4))

    # ---- Disclaimer ---------------------------------------------------------
    story.append(Spacer(1, 14))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#dfe4ee")))
    story.append(Paragraph(
        "This report is generated by SOCAssure to assist supervisory judgement and does not replace it. "
        "Findings are derived entirely from the alert-handling data supplied for this organization for "
        "the stated review period; they do not constitute a certification of security posture.",
        ss["BodyDim"]))

    doc.build(story)
    buf.seek(0)
    return buf
