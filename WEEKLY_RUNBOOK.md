# Fiboprana Weekly Marketing Runbook

> One machine, three loops, three cadences. This is the operating procedure you reference each week. Renders at /runbook.
>
> Hard rules that apply everywhere: copy-paste only (nothing auto-posts); canonical docs (the wiki + the sibling repo's positioning doc) are never auto-edited, only ever proposed-for-edit; stdlib over new deps; manual/pull-based (no scheduler yet, everything is a button); the wellness line (wiki `compliance/advice-line`) on every piece of generated copy.

## How the system is shaped

Three loops, each at its own cadence, all reading the same canonical docs (the `wiki/` vault) and feeding each other through documents and small SQLite tables, never through one shared database (insight flows through docs, not data; the Supabase `marketing` schema is the fleet's ledger, not a message bus).

- **Loop 1 — Weekly content loop** (weekly): what do we make and where do we engage this week? Lanes: trend radar, content watch, reply finds, signal intake, content engine, the faceless video chain.
- **Loop 2 — Strategy review** (monthly-ish): is what we believe about audience / message / platform still true, and is it drifting? Lanes: strategy-drift tracking, competitor watch (wearables + mind-tracking + AI coaches).
- **Loop 3 — Funnel / list-building**: how does ambient attention become an owned audience? Fiboprana starts AHEAD here — the Resend funnel, attribution capture, and the lead magnet already run in the sibling repo (`Fiboprana Marketing/`).

Dependency spine: signal intake + reply finds + trend radar all FEED the content engine. The content engine is a pure consumer; it produces drafts only. Video production is the sibling repo's scripted chain (wiki `channels/video-kit`) — this OS decides WHAT to make; that chain makes it.

---

# LOOP 1 — THE WEEKLY CONTENT LOOP

The ordered checklist for the weekly session. **[auto]** = the one-click run does it; **[you]** = your hand on it. Every [auto] step can later be peeled into the dispatcher schedule without changing what you do.

### 1. Kick off the weekly run — [you, 1 click]
On the dashboard, press **"Run weekly inputs."** This fans out the deterministic scanners and deposits everything into the week's input pile for the content engine. (Until the orchestrator button exists, run the lane buttons below in this same order; they are independent and idempotent.)

### 2. Trend radar digest — [auto] then [you, skim]
- **[auto]** The radar scan runs HN + RSS + Exa with the trend-tuned queries (`radar/config.yaml`), then the digest step distills this week's *new* pile into a short brand-voiced trend digest. Lives at **/radar/digest**.
- The in-lane buckets: wearable & mind-tracking product moves; mind-body research worth covering (with sources); score-anxiety / over-optimization backlash discourse; AI-in-wellness moves; the privacy & wellness-vs-medical regulatory line (FTC HBNR, WA MHMDA, FDA general-wellness). Diet/fitness-bro content, woo, and pure funding news are filtered out.
- **[you]** Read the digest (2 min). It is a journal entry, never an auto-edit to any doc. A strategic competitor move (e.g. Oura ships a mind-state feature) gets noted and escalated into the monthly competitor watch.

### 3. Content watch — what's working out there — [auto] then [you, skim]
- **[auto]** /watch/scan pulls YouTube + Reddit + open-web creators. Items are deduped and flagged **breakout** when they beat their own channel/sub trailing-median velocity. Sorted freshest-breakout-first at **/watch**.
- **[you]** Skim the breakouts. "Summarize craft" returns Hook / Format / Title-and-thumbnail / Cadence / one on-brand adaptation. Packaging intel (the HOW), not product intel.
- **[you]** For X/TikTok wellness creators with no clean API, "Log a find" when something stops your thumb: paste URL + one line at /watch/find/new.

### 4. Reply finds — posts worth replying to — [auto] then [you, work the queue]
- **[auto]** /replies/scan runs the Reddit scan producing one ranked queue at **/replies** (`reply_finder/config.yaml`: r/Meditation, r/Mindfulness, r/breathwork, r/qigong, r/ouraring, r/whoop, r/QuantifiedSelf).
- The ranking rewards the edge: flip-fit (a data-confusion / practice-doubt / score-anxiety thread where the turn-it-inward reply lands, +20/phrase), freshness, answer-room, audience-fit, minus a hard kill on medical/crisis/promo threads (never reply into those — compliance, not preference).
- **[you]** Work ~5 replies/day off the top, hand-written (the moat). Paste back what you replied at /replies/log. If a target is also a good observation, "Also observe" → /observations/new.

### 5. Swipe-file triage — the 10-minute distillation — [auto import] then [you, 10 min]
- **[auto]** New rows in the swipe inbox get a *draft* structured signal pre-filled (hook_type, why_it_landed, the founder's angle, recognizable_specific). Drafts only; never saved.
- **[you]** Work the queue at **/swipe/triage**: name the hook_type, one sentence on why it landed for this audience, the founder's angle in our lane (relief over optimization, noticing over grading), the recognizable specific generalized one notch, and a guardrails glance — if the shape only works with fear/scores/miracle-outcome framing, discard it.
- Capture all week by jotting links on your phone, batch-paste on session day.

### 6. Build the content pack — [auto] then [you, edit & copy]
- **[auto]** **"Build this week's content pack"**: bundles the weekly inputs (trend digest + signal cards + reply finds), allocates slots across the three pillars (`quit-tracker` / `is-it-working` / `mind-science`), drafts per-platform copy through the generators, runs every draft through the wellness-line guardrails check, and assembles one pack at **/content/pack/ID**.
- Each draft is a copy-paste textarea with a Copy button, char counter, a guardrail badge (advisory, not a gate, since nothing auto-posts), prefilled UTM campaign params (pillar slug = utm_campaign), and the on-demand image-prompt button (Quiet Earth kit).
- **[you]** Skim, pick the strongest per slot, edit in your own voice, copy out, post/schedule by hand.
- Quiet week? A smaller honest pack beats manufactured content. That's correct behavior.

### 7. The week's video — [you decide] then [the chain produces]
- Pick the video topic from the digest + reply pains (the strongest recurring pain or the best mind-science story). Scaffold and produce it in the sibling repo's chain: worksheet → narrate → stills → animate → transcribe → timeline → render → shorts (wiki `channels/video-kit`; binding pacing rules in `content/style-kit.md` there).
- Every scientific claim gets verified at scripting time; the publish package passes the compliance checklist before upload. Per-step outcomes land in the sibling repo's `content/production-log.jsonl`.

### 8. Log a platform note — [you, 1 min, optional but valuable]
- After you post, drop a row at **/capture/platform-note**. Cheap by design so it actually gets filled; feeds the monthly platform-performance lens.

## Weekly loop — at a glance
1. [you] press Run weekly inputs
2. [auto] radar digest → /radar/digest
3. [auto] content watch scan → /watch
4. [auto] reply finds scan → /replies
5. [auto] swipe import + pre-structure → /swipe/triage
6. [you] skim digest, log watch finds
7. [you] work ~5 replies, paste back
8. [you] 10-min swipe triage (promote/discard)
9. [auto] Build content pack → /content/pack/ID
10. [you] edit, copy, post by hand
11. [you] pick + produce the week's video in the sibling repo's chain
12. [you] log a platform note

---

# LOOP 2 — MONTHLY STRATEGY REVIEW

Run monthly-ish, when activity has accumulated. Two passes, in order.

## Pass A — Strategy-drift review — /strategy

One click: **"Run review."** Deterministic assembly gathers the inputs, one model call reasons over the pile, output writes to STRATEGY_REVIEW.md (generated, safe to overwrite) plus a queue of discrete positioning proposals you accept or reject by hand. The canonical positioning doc (`Fiboprana Marketing/brand/positioning-and-messaging.md`) is never touched by the machine — Accept surfaces text for you to paste there yourself, then re-distill the wiki page.

Run a fresh synthesis first (/synthesis/regenerate). Three lenses, each forced to a verdict:
- **(a) Audience drift.** Is the wedge shifting (tracker-anxious vs. practice-curious vs. practitioner)? A new recurring segment with no home?
- **(b) Positioning / message drift.** Does the observation corpus still support the positioning's claims? Is shipped content running a message the positioning doesn't name?
- **(c) Platform performance — honest, not fabricated.** Every claim labeled by basis: [founder read], [attribution: N clicks], or [no signal]. "This platform isn't working" ≠ "we can't see whether it's working yet."

## Pass B — Competitor watch — /competitors

Tracks HOW named competitors move on mind-state tracking and AI over time. Distinct from content-watch (packaging) and the radar (topic surfacing).

1. **[auto] Scan**: Exa per-competitor (first-party newsroom/blog domains) + space-level HN + trade press. No denylist here — you WANT to see competitors shipping scores, AI coaches, and emotion tracking, because that's the divergence-from-Fiboprana signal.
2. **[you] Triage fast**, paste app-store "what's new" notes, log wild sightings.
3. **[auto] Regenerate intel**: classifies each move (role: track / score / coach / correlate / community; fiboprana_line: body-only / adds-mind-proxy / grades-the-user / respects-no-score; maturity; threat) and writes a dated delta to COMPETITORS_AI.md plus positioning implications.

**The watchlist (tiered):** Tier 1 — closest mechanic: WHOOP (Journal), Welltory, Oura (Cumulative Stress). Tier 2 — platform coaches: Google/Fitbit, Samsung, Apple (Mindfulness/State of Mind). Tier 3 — mind-only incumbents: Calm, Headspace; manual-tracker peers: Bearable, Daylio; wearable-locked analogs (e.g. Mortis). Regulators/watchdogs: FTC (HBNR, endorsements), FDA (general-wellness line), Washington AG (MHMDA).

**Reconcile** Pass A and Pass B in the same sitting.

---

# LOOP 3 — FUNNEL & LIST-BUILDING (partially LIVE)

Unlike the engine's original design stage, Fiboprana's funnel already runs in the sibling repo: the landing page + Resend email capture with UTM/referrer attribution is deployed, and the lead magnet exists.

Framing constraints:
1. **The email list is the asset.** Short-form is discovery; trust and community compound in email. Beta invite waves draw from this list; north star is week-4 retention, not list size.
2. **Lead magnets cannot cross the wellness line:** no outcome promises, no scores, no "measure your stress." Mirrors, not guides.

## The lead magnets
- **Live — "The Missing Layer"** (`Fiboprana Marketing/brand/lead-magnet/`): the primary cold magnet.
- **Concept — "What's your relationship with your tracker?"** A mirror quiz (client-side scoring, pattern-naming never prescription) mapping the reader onto the relief patterns — score-anxious, data-numb, practice-doubter, quietly-cracking. A free preview of the product's core move; strongest quit-tracker-pillar magnet.
- **Concept — "The 30-second check-in, on paper."** A printable/manual version of the product's core loop; truest to the differentiator, deploy lower in the funnel.

## The newsletter — "Notice"
The owned-channel heartbeat. A weekly pattern note in the founder's voice: this week's pattern from the observation corpus, 1-2 on-thesis links from the radar (curation, not commentary), one rotating soft CTA (lead magnet / waitlist / reply with your own pattern). Byproduct of the weekly content pack, not a separate writing job. Drafted via the Notice generator, sent through Resend by hand until volume justifies automation.

## The offer ladder
- **[0] Content attention** — videos, posts, replies. Click /r/CODE short-link (attribution tracks this; public redirect ships in fiboprana-site).
- **[1] Lead-magnet landing** — email capture with source attribution (LIVE).
- **[2] Newsletter subscriber** — weekly "Notice."
- **[3] Beta invite** — waves from the list; the product is free during beta.
- **[4] Retained user** — week-4 check-in retention ≥30% is THE proof point.

---

## Quick reference — where things live
- Trend digest: /radar/digest — Weekly
- Content watch: /watch, /watch/find/new — Weekly
- Reply finds: /replies, /replies/log — Weekly
- Swipe triage: /swipe/triage, /swipe/new — Weekly
- Content pack: /content/pack/ID, /content/packs — Weekly
- Video chain: sibling repo `Fiboprana Marketing/` scripts + Remotion — Weekly
- Platform note: /capture/platform-note — Weekly → Monthly
- Strategy review: /strategy — Monthly
- Competitor watch: /competitors, /competitors/intel — Monthly
- Funnel: sibling repo (landing + Resend); attribution here at /attribution
- This runbook: /runbook
