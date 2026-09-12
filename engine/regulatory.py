"""
SOCAssure regulatory cross-reference (informational only)
===========================================================
Optional cross-reference between this tool's rule-based findings and the
real CERT-In (2022) Cyber Security Directions, issued by CERT-In under
Section 70B of the Information Technology Act, 2000 (effective 28 April
2022; source: the official Direction PDF at cert-in.org.in).

IMPORTANT -- what this is, and is not: SOCAssure does not verify or certify
legal/regulatory compliance. This module only notes where a rule-based
finding's underlying concern plausibly overlaps with a specific numbered
Direction, worded cautiously ("relevant to", never "violates" or "proves
non-compliance with"). Several Directions describe organizational or
infrastructure requirements (a designated Point of Contact, NTP time
sync, retaining specific subscriber records) that simply are not visible
in an alert-handling CSV at all -- those are listed for context only and
are deliberately left unmapped to any detector, rather than stretched to
fit one.
"""

# The six directions, in plain English, exactly as issued. Shown in full in
# the app's "Regulatory context" panel regardless of which (if any) apply to
# a given organization's findings -- so a supervisor sees the whole picture,
# not just the parts SOCAssure happens to be able to check.
CERT_IN_DIRECTIONS = {
    "i": {
        "label": "Direction (i) -- Time synchronisation",
        "text": "All ICT systems must synchronise their clocks with the NTP servers of NIC/NPL, or with "
                "an accredited equivalent, so that timestamps line up across systems during an investigation.",
    },
    "ii": {
        "label": "Direction (ii) -- 6-hour incident reporting",
        "text": "Specified categories of cyber incidents (per Annexure I) must be reported to CERT-In "
                "within 6 hours of being noticed or being brought to notice.",
    },
    "iii": {
        "label": "Direction (iii) -- Point of Contact",
        "text": "Every entity covered by the Directions must designate a Point of Contact to interface with CERT-In.",
    },
    "iv": {
        "label": "Direction (iv) -- 180-day log retention",
        "text": "ICT system logs must be securely retained for a rolling period of 180 days, and stored "
                "within Indian jurisdiction.",
    },
    "v": {
        "label": "Direction (v) -- Data centre / VPN / cloud subscriber records",
        "text": "Data centres, virtual-private-server/cloud providers, and VPN providers must retain "
                "specified customer/subscriber information for 5 years after any cancellation or withdrawal.",
    },
    "vi": {
        "label": "Direction (vi) -- Virtual asset provider KYC records",
        "text": "Virtual asset exchanges, custodian wallet providers, and similar entities must retain "
                "KYC and transaction records for 5 years, sufficient to reconstruct individual transactions.",
    },
}

# Only mapped where a rule-based finding's underlying concern genuinely,
# specifically overlaps with one of the Directions above. Every other
# detector (EG3 repeat-unresolved-issues, EG4 template investigation notes,
# NS1 missing telemetry, NS3 low activity vs peers, NS4 missing expected
# category) is left UNMAPPED on purpose -- those are real findings, just not
# ones a specific numbered Direction is about, and forcing a link there would
# overstate what this tool actually checks.
_RULE_TO_CERT_IN = {
    "EG1": ("ii", "A high/critical, confirmed-real alert closed within minutes with no escalation record is "
                  "exactly the kind of gap that risks a reportable incident never being surfaced in time for "
                  "the 6-hour CERT-In reporting window."),
    "EG2": ("ii", "A confirmed Critical alert that was never escalated at all means nobody was in a position "
                  "to judge whether it needed to be reported to CERT-In within 6 hours."),
    "EG5": ("ii", "A slow internal escalation directly eats into the 6-hour window Direction (ii) allows for "
                  "reporting a qualifying incident to CERT-In once it's noticed."),
    "NS2": ("ii", "Critical/high-severity, confirmed-real alerts with no escalation records at all leave no "
                  "internal trail showing the 6-hour CERT-In reporting obligation was ever even considered."),
}


def cert_in_reference(rule_id):
    """{clause, label, text, note} for a rule_id with a genuine CERT-In
    correspondence, or None. Most findings have no specific mapped clause --
    that's expected, not a gap in this function."""
    entry = _RULE_TO_CERT_IN.get(rule_id)
    if not entry:
        return None
    clause, note = entry
    d = CERT_IN_DIRECTIONS[clause]
    return {"clause": clause, "label": d["label"], "text": d["text"], "note": note}
