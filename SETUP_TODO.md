# Setup punch list — from the first full weekly run (week of 2026-08-31)

Running notes from walking the board by hand with Claude. Two kinds of entries:
**[todo]** = something to wire before the agents can run this unattended;
**[fixed]** = a clone leftover or bug found on the walk and fixed in place.
Add to this file whenever the walk turns up another one.

## How the loop actually runs today
Every "agent" card on the board is currently **Claude in session** (door two): Claude reads
the pile, writes the artifact, and logs it to the Supabase ledger the same way the
autonomous command would. Nothing runs by itself yet. That is fine for building the
one-month content buffer; the todos below are what turns the cards autonomous.

## [todo] Before agents run unattended
- **LLM key** in `.env`: `OPENROUTER_API_KEY` (one key, any provider) or `ANTHROPIC_API_KEY`.
  Needed by: research_run, reply_finder, reply_drafter, content pack, Notice draft, X batch,
  distiller, outcome checker. (Video stays on the subscription by choice, for cost.)
- **Exa key** in `.env`: `EXA_API_KEY`. The radar's wellness-tuned queries in
  `radar/config.yaml` never fire without it; the pile is HN + RSS only until then.
- **Railway**: `railway link` + deploy so `railway.json`'s daily dispatch actually runs.
  The board's "Sunday cron on Railway" text describes the plan, not the present.
- **Heartbeat semantics**: `fleet/research_run.py --check-heartbeat` says OK if the last
  run is under 8 days old. The board should instead ask "is there a run since this
  week's Monday?" — that is what would have flagged the stale research on the board.
- **Content buffer mode**: founder policy is a one-month backlog before anything posts.
  The Distribute lane's done-clicks are wired as SHIP (X batch → Typefully, Notice →
  Thursday send). Until posting starts, do not click those; consider a board-level
  "buffer / hold" switch (`fleet/fleet_controls.json`) so a done-click records approval
  without scheduling.
- **Reply finder** has not run this week (needs the LLM key). It is the second heartbeat
  feeding the video pick.

## [fixed] on 2026-09-03
- `radar/sources/hn.py`: Algolia typo tolerance made "Oura" match "our" and "HRV" match
  "HRM", so the 20 newest front-page stories came in under every query. Now requires the
  query as a whole word in title or story text. 123 already-scanned off-topic items were
  marked `filtered` in `radar/seen.db`.
- `fleet/research_agent.py`: the content pull's SHIPPED block read
  `content/SHIPPED_FEATURES.md` (the engine's old path, never present in this clone) and
  silently fell back to "(unavailable)". Re-pointed at the wiki page
  `product/feature-inventory`, which already declared `content_pull` as a consumer.
- `fleet/week.html`: "Build the tool" card and gate text still described the Stackivate
  site (quiz/Pro hook, proxy.ts allowlist, 307s). Rewritten for a fiboprana-site page.
- Neuroscience News RSS returned 403 during two scans, then 200 on retest with the same
  settings. Transient block on their side; feed kept. If it recurs, add a retry with
  backoff in `radar/sources/rss.py`.

## Still-open leftovers to check when you pass them
- Q&A `real_moment` question wording was already reworded (verified 2026-09-03).
- Anything else that reads "portfolio", "Pro", "Stack of Eight", or names Stackivate
  channels: grep `fleet/*.html` and `templates/` when in doubt.

## [todo] Founder asks captured on the 2026-09-03 walk
- **Competitor tracker.** Start a living list of direct competitors and what they ship,
  week over week. First entry: **ATLAS (atlasmankind.com)** — behind-the-ear brain
  wearable, 24/7 neural recording, "map your brain and master your mind", pre-orders
  opened 2026-09-01. Closest thing seen so far to the mind-state layer; differences to
  document: hardware vs none, performance/optimization framing vs no-score, neural data
  vs self-report + BYO wearable. Home for it: `wiki/market/competitors.md` (Loop 2's
  /competitors watch reads the wiki) — add a "Direct" tier above the adjacent incumbents.
- **Terminology rule** written into `wiki/compliance/voice-guardrails.md`: name the method
  (meditation for now), never bare "practice", in public copy. Older copy on the board
  (research video angle, card texts) still says "practice"; rewrite as it gets used.
- **Stuck "generating" markers.** When the Q&A done-click spawns `content.x_run` and
  `content.email_run` and they fail (no LLM key), the `generating_since` marker stays in
  `fleet/x_batch.json` / `fleet/email_broadcast.json`, so the overlay says "generating"
  until someone writes the slot by hand. `news_ideas_run` already pops its marker on
  failure; give the other two the same behavior and show the log's last line on the card.
- **Guardrails check is LLM-only.** `content/guardrails_check.py` only exposes
  `check_idea_against_guardrails(...)` which calls the model, so this week's X batch,
  Notice, and story options were NOT machine-checked (they were written against the
  wellness line by hand). Once the key is in, re-run the check over the buffer before
  anything posts; consider adding a cheap regex pre-check (banned words from
  advice-line) that runs without a key.

## Corrections and fixes, later on 2026-09-03
- **Typefully is NOT connected here.** `TYPEFULLY_API_KEY` and `TYPEFULLY_SOCIAL_SET_ID`
  are blank in `.env` (earlier note listing them as set was wrong: every line was blank
  except Supabase, Reddit, Apify, X username, xAI, and the attribution base URL). So the
  X batch "mark done = schedule" click cannot post anything today. When the founder is
  ready to post: create the Typefully API key, read the social-set id from the Typefully
  URL, put both in `.env`. Buffer mode note: `content/x_schedule.py` always sets
  `publish_at`; add a `--drafts-only` flag (omit `publish_at`) so a batch can be parked in
  Typefully as unscheduled drafts while the one-month buffer builds.
- **Resend is NOT connected here.** `RESEND_API_KEY` / `RESEND_AUDIENCE_ID` are blank in
  this repo's `.env`; the live keys live in `../Fiboprana Marketing/.env.local`. Copy the
  two values over (same business, same audience) before the Notice card's done-click can
  schedule a send. Until then the email card is read-and-feedback only.
- [fixed] `content/x_run.py` and `content/email_run.py` now clear the card's
  `generating_since` marker on every failure exit and write `last_error` /
  `last_error_at` on the slot; `fleet/week.html` shows that error on the two overlays.
- [fixed] `content/x_schedule.py` crashed at import when the social-set id was blank.
- [fixed] `content/guardrails_check.py` gained `check_text_offline(text)`: a keyless
  regex floor for the advice-line bans, score/streak/FOMO framing, em dashes, AI-tell
  phrases, and bare "practice". Run over this week's 14 posts + Notice + 3 story options:
  0 flags. The model check still runs once a key is in.
- **Research verification**: `fleet/research_verify.py` exists (fetches the named
  company's own site and tries to refute the headline + competitor note) but every
  judgment goes through the LLM, so it is skipped without a key. This week's three
  promoted claims (ATLAS press release, Oura S-1, Oura MIS post) were fetched and read
  by hand and the verification notes are stored on the run's `inputs.verification`.
- [fixed] `zoneinfo` had no time zone database on this Windows Python (3.14), so both
  `content/x_schedule.py` and `fleet/email_send.py` crashed on `America/Denver`.
  Installed `tzdata` into `.venv` and added it to `requirements.txt`.
- [fixed] The story-options overlay had no feedback box (founder ask 2026-09-03). Added:
  notes save to `<video>.ideas_feedback` on the week's slot via the existing
  `/api/video_ideas` feedback route (`target: "ideas"`); the slate rewrites from them and
  carry-over ideas keep the notes.
- **Founder rule (video content):** never cover or name a competitor as the subject of a
  video. Category-level framing only ("a brain wearable now exists"). Competitors are
  tracked in the competitor list, not on the channel.
- [fixed] `content/facts_run.py` left `facts_generating_since` on the card when the plan
  or search step failed (same stuck-marker shape as x_run/email_run). Early failures now
  clear it and record `facts_last_error`.
- **Facts step without keys**: the runner's search step needs Exa and its plan/verify
  steps need the LLM, so this week's report was researched in session (5 primary sources
  fetched; PubMed/PMC block scripted fetches with a captcha, Europe PMC's REST API does
  not, so use that for paper abstracts).
- [fixed] **The story rule** (founder correction 2026-09-03: one story, one question, ancient
  half + modern half of the SAME question; no stapled studies; no competitor as subject;
  name the method; grounded register) now lives in `wiki/channels/youtube.md` and is
  loaded at import into the ideas, facts (plan + verify), and script prompts, fail-closed.
  The script prompt's standing close now says "your meditation (or the named method)".
- **Post-workflow revisit (founder ask):** the "news video" lane's prompt is written as
  "this week's story translated," which pulls drafts toward news roundups. Redefine the
  lanes (timely story vs evergreen story, both under the story rule) when the video
  structure is revisited.
