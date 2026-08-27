"""The regex compliance FLOOR for reply drafts — Phase 1's only automated check.

It does NOT decide whether a draft is good; I read every draft. It catches the
handful of things that are never OK in a reply and would be embarrassing or legally
risky to send: links (X throttles them and they read as spam), and the
wellness-line tripwires from wiki/compliance/advice-line — disease/efficacy claims,
"measures your stress"-type overclaims, medical imperatives aimed at the reader,
score/grade framing, guaranteed-outcome language, and FOMO / urgency.

Severity routes the finding; it doesn't silently lose the draft:
  * 'block' — a link. Unambiguous. The draft is hidden from the review queue but
    still stored (the learning signal: what the drafter tried that it shouldn't).
  * 'flag'  — a use-vs-mention judgement call (a ticker could be an educational
    mention; "60/40" could be a date; a percentage could be a quote). Shown with a
    warning; I decide.
A clean draft is 'pass'. The LLM compliance judge and a versioned-in-DB ruleset are
Phase 2; this is the lean floor that runs before I ever see a draft.
"""

import re

GATE_RULESET_VERSION = "gate-v2-fiboprana"

# (rule, severity, compiled). 'block' hides the draft from review; 'flag' warns me.
# Ordered roughly hard -> soft. Soft rules lean toward catching things for a human
# eyeball, accepting some false positives, since I read every draft anyway.
_RULES = [
    # Hard: an explicit link. Never belongs in a reply.
    ("link", "block",
     re.compile(r"https?://\S+|\bwww\.\S+", re.IGNORECASE)),
    # Soft: a bare domain (could be an educational name-drop, could be a link).
    ("bare_domain", "flag",
     re.compile(r"\b[\w-]{2,}\.(com|net|org|io|co|app|gg|me|xyz)\b", re.IGNORECASE)),
    # Soft: a disease / efficacy claim (the FDA general-wellness line).
    ("disease_claim", "flag",
     re.compile(r"\b(cures?|treats?|heals?|diagnos\w*|prevents?)\b.{0,40}"
                r"\b(anxiety|depression|insomnia|ptsd|adhd|illness|disease|disorder|"
                r"condition)\b", re.IGNORECASE)),
    # Soft: a named health-outcome claim.
    ("outcome_claim", "flag",
     re.compile(r"\b(reduces?|lowers?|eliminates?|fixes?|boosts?)\s+(your\s+)?"
                r"(anxiety|depression|stress|cortisol|blood pressure|inflammation)\b|"
                r"\b(clinically proven|doctor[- ]recommended|medical[- ]grade)\b",
                re.IGNORECASE)),
    # Soft: a measurement / detection overclaim about mind-state.
    ("measurement_claim", "flag",
     re.compile(r"\b(measures?|detects?|reads?|knows?|quantif\w+)\s+(your\s+)?"
                r"(stress|emotion\w*|mood|mind|consciousness|mental state|anxiety)\b",
                re.IGNORECASE)),
    # Soft: a medical imperative aimed at the reader (never our lane).
    ("medical_imperative", "flag",
     re.compile(r"\byou\s+(should|need to|have to|ought to|must)\s+"
                r"(stop|start|quit|take|drop|skip)\b.{0,30}"
                r"\b(medication|meds|prescri\w+|therap\w+|doctor|dosage)\b",
                re.IGNORECASE)),
    # Soft: score / grade / optimization framing (the anti-backlash brand line).
    ("score_framing", "flag",
     re.compile(r"\b(your score|you'?re at \d{1,3}\s*%|optimi[sz]e your|"
                r"crush your|streak|leaderboard|rank(?:ing)? your)\b", re.IGNORECASE)),
    # Soft: a guaranteed-outcome / transformation claim (antifraud line).
    ("performance_claim", "flag",
     re.compile(r"\b(guaranteed?|life[- ]changing results|transform your life|"
                r"works for everyone|can'?t fail|100\s*%\s*(effective|works))\b",
                re.IGNORECASE)),
    # Soft: FOMO / urgency vocabulary (dark-pattern line).
    ("fomo", "flag",
     re.compile(r"\b(act now|don'?t miss|last chance|before it'?s too late|"
                r"limited time|get in (now|early))\b", re.IGNORECASE)),
]


def check(text):
    """Run the floor over one draft. Returns a dict ready to attach to a
    reply_drafts row: {gate_status, gate_violations, gate_ruleset_version}.

    gate_status: 'pass' (clean) | 'flag' (soft findings, shown with a warning) |
    'block' (a link; hidden from the review queue but still stored).
    gate_violations: a list of {rule, severity, match}, or None when clean.
    """
    text = text or ""
    violations = []
    for rule, severity, rx in _RULES:
        for m in rx.finditer(text):
            violations.append({"rule": rule, "severity": severity,
                               "match": m.group(0).strip()})
    if any(v["severity"] == "block" for v in violations):
        status = "block"
    elif violations:
        status = "flag"
    else:
        status = "pass"
    return {"gate_status": status,
            "gate_violations": violations or None,
            "gate_ruleset_version": GATE_RULESET_VERSION}
