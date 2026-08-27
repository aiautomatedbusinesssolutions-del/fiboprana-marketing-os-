"""Schedule the week's approved X batch into Typefully.

    python -m content.x_schedule                # this week's batch
    python -m content.x_schedule --week 2026-08-10
    python -m content.x_schedule --dry-run      # print the plan, no API calls

The drain-pattern partner to content/x_run.py: the founder marking the
"dist/xbatch" card done on /week is the trigger (fleet/dashboard.py spawns this
module), meaning every post in fleet/x_batch.json is approved as it stands.

The batch week runs TUESDAY through the following MONDAY (the founder approves
on Monday evening). Each day gets two queue slots, 12:00 and 5:00 PM Mountain —
the same rules as the Typefully queue itself — and a slot already in the past
falls back to Typefully's "next-free-slot" so nothing silently posts late or
not at all. CTA posts go up as a two-part thread (body, then the link) so the
link ships as a self-reply.

Guards: a post already carrying a typefully_draft_id is skipped (safe to
re-run), and a post over 280 characters is refused and reported, never
truncated. After creating, every draft is read back from Typefully and its
status + publish time recorded onto the post — the card shows what the queue
actually holds, not what we hope it holds.
"""

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

import os  # noqa: E402  (after load_dotenv so the key is present)

API_BASE = "https://api.typefully.com/v2"
# Fiboprana's Typefully social set (X only) — set TYPEFULLY_SOCIAL_SET_ID in
# .env once the account is connected; 0 means not configured yet.
SOCIAL_SET = int(os.getenv("TYPEFULLY_SOCIAL_SET_ID", "0"))
X_BATCH_STATE = ROOT / "fleet" / "x_batch.json"
MT = ZoneInfo("America/Denver")
SLOT_HOURS = (12, 17)  # first and second post of each day, Mountain time
# The batch week: suggested_day -> days after the week's Monday. Tue..Sun are
# this week; "Mon" is the FOLLOWING Monday (the batch's closing day).
DAY_OFFSETS = {"Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6, "Mon": 7}


def _api(method, endpoint, body=None):
    """One Typefully API call. Returns the parsed JSON; raises RuntimeError
    with the response text on any non-2xx."""
    key = os.getenv("TYPEFULLY_API_KEY")
    if not key:
        raise RuntimeError("TYPEFULLY_API_KEY is not set (.env)")
    req = urllib.request.Request(
        f"{API_BASE}{endpoint}",
        method=method,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        data=json.dumps(body).encode("utf-8") if body is not None else None,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method} {endpoint} -> HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}")


def publish_at_for(post, slot_index, monday, now):
    """The post's queue slot as an aware datetime, or "next-free-slot" if that
    slot is already in the past. slot_index is 0 for a day's first post, 1 for
    its second (assigned by plan() in stored order)."""
    day = date.fromisoformat(monday) + timedelta(days=DAY_OFFSETS[post["suggested_day"]])
    hour = SLOT_HOURS[min(slot_index, len(SLOT_HOURS) - 1)]
    when = datetime(day.year, day.month, day.day, hour, 0, tzinfo=MT)
    return when if when > now else "next-free-slot"


def plan(posts):
    """Split the batch into ([(post, slot_index)], skipped_done, refused),
    where slot_index is 0 for a day's first post (12:00), 1 for its second
    (17:00), assigned in stored order."""
    seen_per_day = {}
    to_schedule, skipped_done, refused = [], [], []
    for p in posts:
        if p.get("typefully_draft_id"):
            skipped_done.append(p)
            continue
        if len(p["text"]) > 280:
            refused.append(p)
            continue
        slot_index = seen_per_day.get(p["suggested_day"], 0)
        seen_per_day[p["suggested_day"]] = slot_index + 1
        to_schedule.append((p, slot_index))
    return to_schedule, skipped_done, refused


def create_draft(post, when):
    """Create one scheduled draft. CTA posts become a two-part thread so the
    link ships as a self-reply (the X link-throttle dodge)."""
    parts = [{"text": post["text"]}]
    if post.get("is_cta") and post.get("link_reply"):
        parts.append({"text": post["link_reply"]})
    body = {
        "platforms": {"x": {"enabled": True, "posts": parts}},
        "publish_at": when if isinstance(when, str) else when.isoformat(),
    }
    return _api("POST", f"/social-sets/{SOCIAL_SET}/drafts", body)


def verify_draft(draft_id):
    """Read the draft back: (status, scheduled_date, part_count). The read-back
    is the record — never trust the create call alone. (The create body's field
    is publish_at, but the draft object reports it as scheduled_date.)"""
    d = _api("GET", f"/social-sets/{SOCIAL_SET}/drafts/{draft_id}")
    x = (d.get("platforms") or {}).get("x") or {}
    return d.get("status"), d.get("scheduled_date"), len(x.get("posts") or [])


def main():
    parser = argparse.ArgumentParser(description="Schedule the week's X batch into Typefully.")
    parser.add_argument("--week", default=None, help="the week's Monday (default: this week)")
    parser.add_argument("--dry-run", action="store_true", help="print the plan, no API calls")
    args = parser.parse_args()

    monday = args.week or str(date.today() - timedelta(days=date.today().weekday()))
    state = json.loads(X_BATCH_STATE.read_text(encoding="utf-8"))
    slot = state.get(monday)
    if not slot or not slot.get("posts"):
        print(f"No batch for week {monday} in {X_BATCH_STATE.name} — nothing to schedule.", file=sys.stderr)
        return 1

    now = datetime.now(MT)
    to_schedule, skipped_done, refused = plan(slot["posts"])
    print(f"Week {monday}: {len(to_schedule)} to schedule, "
          f"{len(skipped_done)} already scheduled, {len(refused)} refused (>280).")
    for p in refused:
        print(f"  REFUSED (trim first): {p['suggested_day']} · {p['text'][:60]}...")

    if args.dry_run:
        for p, s in to_schedule:
            print(f"  {p['suggested_day']} slot {s} -> {publish_at_for(p, s, monday, now)}: {p['text'][:60]}...")
        return 0

    failures = 0
    for p, s in to_schedule:
        when = publish_at_for(p, s, monday, now)
        try:
            created = create_draft(p, when)
            draft_id = created["id"]
            status, publish_at, parts = verify_draft(draft_id)
            p["typefully_draft_id"] = draft_id
            p["typefully_url"] = f"https://typefully.com/?d={draft_id}&a={SOCIAL_SET}"
            p["scheduled_for"] = publish_at
            p["typefully_status"] = status
            expected_parts = 2 if (p.get("is_cta") and p.get("link_reply")) else 1
            note = "" if (status == "scheduled" and parts == expected_parts) else \
                f"  <-- CHECK: status={status}, parts={parts} (wanted {expected_parts})"
            print(f"  ok {p['suggested_day']}: draft {draft_id} at {publish_at}{note}")
            if note:
                failures += 1
        except (RuntimeError, KeyError) as e:
            failures += 1
            print(f"  FAILED {p['suggested_day']} · {p['text'][:50]}...: {e}", file=sys.stderr)
        # persist after every post so a mid-run crash never re-creates drafts
        slot.pop("scheduling_since", None)
        slot["scheduled_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        slot["scheduled_by"] = "content.x_schedule"
        tmp = X_BATCH_STATE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        tmp.replace(X_BATCH_STATE)

    done = sum(1 for p in slot["posts"] if p.get("typefully_draft_id"))
    print(f"\n{done}/{len(slot['posts'])} posts scheduled; {failures} failures; "
          f"{len(refused)} awaiting a trim. Queue: https://typefully.com/?a={SOCIAL_SET}")
    return 1 if (failures or refused) else 0


if __name__ == "__main__":
    raise SystemExit(main())
