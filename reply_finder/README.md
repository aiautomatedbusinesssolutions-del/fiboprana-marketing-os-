# reply_finder/

Ranks Reddit **reply targets** — threads where an early, on-brand reply has high
leverage. Rule-based scoring, no AI, no auto-posting. Replies stay hand-written. This
is the lean, run-it-from-the-terminal version; the dashboard wrapper comes later.

## Why RSS (and not the Reddit API or EXA)

Both other paths are blocked right now:
- **Reddit API** — couldn't get credentials (account too new / approval friction).
- **EXA** — Reddit is delisted from EXA's index; `include_domains: reddit.com` 403s and
  Reddit doesn't appear organically either. (EXA still reaches Bogleheads and other
  forums fine — see `sourcing_web/` if you want a forum lane.)

So this sources from Reddit's **public per-subreddit RSS** (`/r/<sub>/new/.rss`) via the
browser-UA reader in `radar/sources/rss.py`. No key needed.

## Run it

```
python -m reply_finder.run          # scan, print the ranked top 25
python -m reply_finder.run --top 10
python -m reply_finder.run --all
```

## How ranking works

A post must clear the `exclusion_phrases` hard-kill (day-trading / options / crypto /
leverage / engagement-trap) and match at least one flip phrase or beginner phrase, or
it's dropped as off-topic. Survivors score on:

- **flip match** (+20 each) — buy/sell/validation questions where the "turn it inward"
  reply lands. The edge. Grouped into `pre_decision`, `post_decision`,
  `journey_validation`, each carrying a suggested **angle**.
- **soft trigger** (+5 each) — beginner audience-fit; keeps the queue on our person.
- **freshness** (up to +20) — early replies win; full at age 0, gone at
  `feed.lookback_hours`. The `/new` feed is fresh-sorted, so this signal is reliable.

Asset-neutral by rule: nothing in the score depends on which ticker/asset the thread is
about, only on question-shape and freshness.

## Known limits

- **Rate limiting.** Reddit throttles unauthenticated RSS hard. The scan spaces feeds
  (`feed.delay_seconds`) and retries once on a 429, but a single run may not reach every
  sub. Re-running catches a different mix. Widen `delay_seconds` or trim `subreddits` if
  it's persistent.
- **No comment count.** RSS doesn't expose it, so there's no answer-room signal (the
  Reddit API had it). Freshness substitutes.
- **Title-weighted.** Reddit's `/new` RSS often carries an empty blurb, so flip-matching
  is mostly on the title — which is where the question usually lives anyway.

## Tuning

Edit `config.yaml`; reloaded each run (no restart). Main levers: `flip_groups`
(phrases + angles), `soft_trigger_phrases`, `exclusion_phrases`, the `scoring` weights,
`feed.lookback_hours` / `delay_seconds`, and `scan.min_score_to_display`.

## Logging replies (the agent training set)

Sent replies are persisted to `reply_log` (in `reply_finder/reply_log.db`) via
`store.py` — a self-contained training example per reply for the future reply agent
(the fleet). It records the post (denormalized), the scanner's signal at selection time
(`finder_score` / `flips` / `soft_triggers` / `angle`), the reply verbatim, the
**rationale** (why this post + why this angle), and outcome fields filled in later
(`outcome_score`, `op_replied`, `led_to_profile`). Two learnable loops: *what's
working* (reply + angle → outcome) and *find replies better* (finder signal → outcome).

```python
from reply_finder import store
rid = store.log_reply_from_target(target, reply_text="...", rationale="...", replied_at="2026-06-23")
store.update_outcome(rid, outcome_score=14, op_replied="yes")   # days later
```

Same interface for both drivers: me in chat now, a fleet agent later. The scanner
stays a pure function — nothing here touches scanning.

## Deferred

- **Scan-state persistence** (mark-replied, dedupe across weeks), title-dedup of reposts.
- **Negative examples** — logging *skipped* candidates (not just sent replies) for
  richer finder-learning. reply_log already gives positive signal (post features →
  outcome); this would add the other side.
- A forum lane via EXA (Bogleheads), and the `/replies` dashboard UI. The scan is a pure
  function (`scan_for_reply_targets`) so the dashboard can call it unchanged later.
