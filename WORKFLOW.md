# My Weekly Marketing Workflow — Fiboprana

> The lean, working-with-Claude-in-the-repo version of the weekly loop. This is the
> operating flow to run by hand first; the dashboard's `WEEKLY_RUNBOOK.md` is the fuller
> automated version of this same machine. Run it manually a few times, then hand as much
> as possible to the fleet. Nothing here is set in stone — running it is how we learn
> what to change.
>
> **Two doors, same machine.** Every step can be driven either by the command/dashboard
> *or* by working through it with Claude in the repo — same SQLite + Python modules
> underneath. The dashboard is a convenience view, not the source of truth.

Last updated: 2026-08-26 (day one of the Fiboprana clone — cadences are starting
guesses, not settled habits)

## North star

Grow the owned email list toward beta invite waves, and prove the content engine:
faceless videos + Reddit listening + the "Notice" email, all feeding each other.
Product north star it all serves: week-4 check-in retention ≥ 30%. Every step below is
written so it can eventually be **outsourced to an agent** — `[agent-ready]` steps are
shaped for that now; `[me]` steps stay human (the moat, or founder judgment).

## What the product is (so the content ladders to it)

The **mind-state layer on top of the wearables you already own**: a ~30-second daily
mind+body check-in, your body's signals brought in from devices you already have (or by
hand), one picture trending over weeks — quiet proof of whether your practice is doing
anything. **No score, no grade, no verdict.** Free during beta. The wedge is contested
(WHOOP/Welltory/Oura); the moat is trust + privacy + community + practice depth. Every
public word obeys the wellness line (`wiki/compliance/advice-line`).

## The shape of it

Research + Reddit listening feed a produce-and-publish loop:

- **Research + Reddit are the weekly heartbeat.** The radar surfaces where the niche is
  moving (wearables, mind-body research, score-anxiety backlash, AI-in-wellness,
  privacy/regulation). Reddit surfaces what real trackers and practitioners are stuck
  on — the most direct line to prospective users, so **it's the primary driver of what
  gets made** (and a feature-signal source for the product backlog).
- **A video a week off the strongest pain or story.** YouTube long-form (3–5 min) is the
  production engine; 3 Shorts cut from each master; X posts ride the same research run.
- **Everything funnels to email.** The list is the algorithm-proof asset; beta waves
  draw from it.

## "Run the research" — locked

When I say *run the research*, this is exactly what happens:

1. **`python -m radar.run`** `[agent-ready]` — scans HN + RSS + Exa
   (`radar/config.yaml`, wellness-tuned), then distills the brand-voiced trend digest
   (deduped against last week). Saved to `/radar/digest`.
2. **The content pull** `[agent-ready]` — read the digest and hand back five things,
   same shape every week: the **headline story**, the **"what this means for you" video
   angle**, any **competitor / divergence note**, **1–2 Reddit listening themes**, and a
   **feature seed** (logged to `../Fiboprana Marketing/product/feature-signals.md`).
3. **The post-research Q&A** `[me]` — the react step, five questions answered in chat:
   1. **Verdict:** strong / ok / off — one line on why (logged; the learning signal the
      next run reads).
   2. **Headline:** is this the story to open the week's mind-science video with?
   3. **Anything missed:** a story seen this week the digest didn't have?
   4. **Feature seed:** log-worthy for the product backlog, content-instead, or skip?
   5. **Reddit themes:** do these match what's actually out there? Swap any?

   The radar is tuned to never miss the **privacy / wellness-vs-medical regulatory
   line** (FTC HBNR, WA MHMDA, FDA general-wellness) — the Fiboprana analog of the
   line that must never be crossed unaware.

## The weekly steps (in running order)

1. **Run the research.** `[agent-ready]` + `[me]` for the call. The strongest signal
   becomes the week's video topic.

2. **Reddit — listen + reply (the heartbeat).** `python -m reply_finder.run`
   `[agent-ready]` ranks flip-shaped threads (data-confusion / practice-doubt /
   score-anxiety) across the wellness subs. Work the top of the queue — **replies stay
   hand-written and hand-posted, the moat** `[me]`. Never reply into medical or crisis
   threads (hard exclusion, compliance). The recurring pain here is the primary input
   to the week's video and the product's feature signals.

3. **Pick and produce the week's video.** `[me]` decides; the chain produces. Topic from
   the digest + reply pains, pillar-tagged (`quit-tracker` / `is-it-working` /
   `mind-science`). Production is the sibling repo's scripted faceless chain
   (worksheet → narrate → stills → animate → transcribe → timeline → render → shorts;
   `wiki/channels/video-kit`). Every scientific claim verified at scripting time; the
   binding pre-publish checklist runs before upload. Cost baseline: ~$7/video.

4. **Repurpose outward.** `[me]` (automatable later) Master → 3 Shorts → TikTok → X.
   Reddit is NOT a repurpose target — native-only there. X: the weekly batch generated
   from the same research run (quality-gated, start ~1/day), links in replies, bio +
   pinned post carry the standing CTA. Approval gate on all generated copy until the
   voice is proven.

5. **The "Notice" email.** `[agent-ready]` draft from the week's strongest pattern +
   1–2 curated radar links + one soft rotating CTA; `[me]` edit and send through
   Resend. This is the trust surface — warmest voice, privacy story is a content beat.

6. **Log outcomes.** `[me, 5 min]` Platform notes after posting; reply outcomes pasted
   back; video stats land via the sibling repo's `content-stats.mjs` at day-7/28.
   What lands tells us what has pull → bias next week toward it.

**Reserve pillar:** `mind-science` — "ancient practice, modern proof" is evergreen and
fills any week the timely material is thin. It ranks forever and seeds the future
library.

**Timely vs. evergreen:** backlash/news-reactive pieces (quit-tracker lane) are the
perishable hooks; is-it-working and mind-science pieces are compounding evergreen
assets. Make the timely one fast; never let it crowd out finishing the evergreen ones.

## How the research wires to content (the map)

- Digest headline + research bucket → the week's **mind-science / news-reactive video**.
- Reddit pain (primary) → the **quit-tracker / is-it-working video** + reply angles.
- Feature seeds → `product/feature-signals.md` in the sibling repo (the bridge to the
  product backlog — never a public capability claim until it ships).

One research run feeds: one video decision, the week's X batch, the Reddit themes, and
the Notice draft.

## Loop-closers (so it compounds)

- The recurring Reddit pain → this week's video → comments on that video → more
  listening material.
- Feature signals accumulate evidence per run → the product builds what's proven → the
  launch content pre-sold by the pillar that demanded it.
- Reply + post outcomes feed the levers ledger → agents learn which angles land.

## What to hand to agents first

Already one-command: the **radar scan + digest**, the **content pull**, the
**reply-finder scan**. Staying human: **writing replies** (the moat), **the video
topic call and title pick**, **the research verdict**. First automation target: the
weekly dispatcher running scan + digest + reply-finder on schedule
(`python -m fleet.dispatch --list` to see the jobs) so hands touch only judgment,
production, and publishing.

## Cadence note

Weekly research + one video/week is the starting rhythm — video 1 proved the chain;
consistency beats volume until the list and the channel give real signal. The digest
dedupes against prior weeks; when it starts repeating, drop to biweekly. Scale to
2–3 videos/week only when a pillar shows real pull.
