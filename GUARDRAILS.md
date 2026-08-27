# Guardrails — Fiboprana (pointer)

The binding rules live in two places; edit THOSE, not this file:

- **The wellness line** (legal floor): `wiki/compliance/advice-line.md` — never
  cure/treat/diagnose, no health-outcome claims, no measurement/detection claims, no
  scores/grades/optimization framing, no uniqueness or absolute-market claims, no
  unshipped-capability claims, no dark patterns, privacy promises honored everywhere.
- **The AI-tell floor** (copy quality): `wiki/compliance/voice-guardrails.md` — no
  em-dashes, no stock AI constructions, no invented stats.

Full derivation and word lists: `../Fiboprana Marketing/brand/positioning-and-messaging.md`
§10 (✅/⛔, BINDING) and the standing guardrail in that repo's `AGENTS.md` (FTC
substantiation + endorsements, FDA general-wellness vs. medical-device line, FTC HBNR +
WA MHMDA privacy).

Automated enforcement: `content/guardrails_check.py` (idea/copy reviewer) and
`fleet/compliance_gate.py` (regex floor on reply drafts) implement these rules in code —
when the rules change, update both.
