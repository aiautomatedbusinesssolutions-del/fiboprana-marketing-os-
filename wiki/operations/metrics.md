---
id: operations/metrics
title: Metrics — what we count
tags: [operations, metrics]
consumers: [metrics, research]
canonical: true
source_of_truth: null
last_verified: 2026-08-26
---

**North star (product):** week-4 check-in retention ≥ 30% once beta waves start.

**Marketing funnel (owned-list-first):** video views → tracked-link clicks (UTM per
pillar, short links via attribution) → email signups (Resend audience, source attributed)
→ beta invite acceptance → week-4 retention. The email list is the asset; platform metrics
are diagnostics, not goals.

**Per-video:** day-7 / day-28 views, watch %, and which pillar earns — logged against the
worksheet, reviewed before picking the next topic. **Per-channel learning loops:** reply
outcomes (Reddit), post outcomes (X), production cost per video (~$7 baseline from
video 1). **Guardrail metric:** zero compliance strikes — every public piece passes the
wellness line before it ships.

<!-- human -->

Metric snapshots land in Supabase (`marketing.metric_snapshots`); production costs in the
sibling repo's `content/production-log.jsonl`. North star from the locked PRD. Funnel
attribution: Resend contact properties (`signup_source/medium/campaign/content/referrer`)
set by the live landing page.
