# Fiboprana Marketing OS — working brief

This repo is the **marketing engine** for Fiboprana (the mind-state layer on top of
wearables — general wellness, never medical). Cloned 2026-08-26 from the founder's
proven engine for his other business; architecture doctrine in `MARKETING_OS.md`.

Rules that matter in every session:
- **Business facts live in `wiki/`** — agents/prompts load pages at runtime via
  `fleet/wiki.py`. To change persona, positioning, compliance, or channel doctrine,
  edit the wiki page (and its root mirror if one exists: BRAND_VOICE.md,
  POSITIONING.md, PRODUCT_PRINCIPLES.md), never the prompt files.
- **The wellness line is binding** (`wiki/compliance/advice-line.md`): never
  cure/treat/diagnose, no health-outcome or "measures your stress" claims, no
  scores/grades/optimization framing, no uniqueness claims, no unshipped-capability
  claims. Enforced in code by `content/guardrails_check.py` + `fleet/compliance_gate.py`.
- **Nothing ever auto-posts.** `fleet/publish.py` raises by design; replies and posts
  are hand-approved.
- **Video production is NOT here** — it's the faceless chain in the sibling repo
  `../Fiboprana Marketing/` (see `wiki/channels/video-kit.md`). This OS decides what
  to make; that chain makes it.
- Weekly operating loop: `WORKFLOW.md` (lean) / `WEEKLY_RUNBOOK.md` (dashboard
  version). Database: `SUPABASE_SETUP.md` (one shared Fiboprana project, `marketing`
  schema, scoped role — not yet provisioned).
- Stack: Python 3.12+ stdlib-first Flask (port 5000; fleet dashboard 8765), SQLite per
  module, Supabase for the fleet ledger, LLM via `fleet/llm.py` (OpenRouter primary,
  Anthropic fallback — one key enables everything).
