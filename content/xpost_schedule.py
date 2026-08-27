"""Schedule a video's approved long-form X post into Typefully.

    python -m content.xpost_schedule                 # every approved, unscheduled xpost
    python -m content.xpost_schedule --week 2026-08-10 --video news
    python -m content.xpost_schedule --dry-run       # print the plan, no API calls

Drain-pattern wire 5 (founder call 2026-08-13, after the Rivo post sat approved
for a day with no session to act on it): the "ready to schedule" click on the
/week long-form card was a gate with nobody behind it — the contract said
"Claude-in-session schedules it", so an approval clicked between sessions just
waited. Now the click (and the video's publish stamp — whichever lands second)
spawns this module, and the card marks itself done.

Per approved xpost (video_ideas[week][video].xpost, status "approved", no
typefully_draft_id):

1. Find the video's row in marketing.videos (same pillar + created-this-week
   match the publish card uses). No row or no youtube_video_id yet means the
   video isn't scheduled on YouTube — not an error, the publish stamp will
   re-fire this module later.
2. Already-posted guard: scan the account's recent X posts via Typefully
   analytics for this text. A match (founder posted it by hand) records the
   live URL and marks the step done WITHOUT scheduling — the module notices a
   post rather than double-posting one.
3. Otherwise schedule ONE two-part Typefully draft — the post, then the
   YouTube link as a self-reply — timed to the video's go-live day at 23:00Z
   (5 PM MT slot), falling back to next-free-slot if that moment is past.
4. Read the draft back (never trust the create call alone), write
   typefully_draft_id / typefully_url / scheduled_for onto the xpost slot,
   and stamp the week's {video}/xpost flow step done.

Idempotent: a slot with typefully_draft_id (or status "posted") is DONE and
never rescheduled; re-runs are safe.
"""

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

import os  # noqa: E402  (after load_dotenv so the key is present)

from fleet import supabase  # noqa: E402

import urllib.error  # noqa: E402
import urllib.parse  # noqa: E402
import urllib.request  # noqa: E402

API_BASE = "https://api.typefully.com/v2"
# Fiboprana's Typefully social set (X only) — set TYPEFULLY_SOCIAL_SET_ID in
# .env once the account is connected; 0 means not configured yet.
SOCIAL_SET = int(os.getenv("TYPEFULLY_SOCIAL_SET_ID", "0"))
VIDEO_IDEAS = ROOT / "fleet" / "video_ideas.json"
FLOW_STATE = ROOT / "fleet" / "flow_state.json"
PUBLISH_HOUR_UTC = 23  # 5 PM MT during DST — the day's second queue slot


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


def _read(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write(path, data):
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)
    try:  # off-PC drain mirror - best-effort, never blocks a save
        from fleet import fleet_state
        name = {"flow_state.json": "flow_state",
                "video_ideas.json": "video_ideas"}.get(path.name)
        if name:
            fleet_state.push(name, data)
    except Exception:  # noqa: BLE001
        pass


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def mark_flow_done(week, video):
    """Stamp {video}/xpost done in flow_state.json — the card leaves the board
    on its own. An existing stamp is kept (same rule as the publish card)."""
    state = _read(FLOW_STATE)
    steps = state.setdefault(week, {})
    if f"{video}/xpost" not in steps:
        steps[f"{video}/xpost"] = _now()
        _write(FLOW_STATE, state)


def video_row(week, video):
    """The week's video row, youtube id first — identical find order to the
    /week publish card so both surfaces always agree on which video it is."""
    rows = supabase.select("videos", params={
        "pillar": f"eq.{video}", "created_at": f"gte.{week}",
        "order": "created_at.desc", "limit": 1,
        "select": "id,slug,youtube_video_id,published_at",
    })
    return rows[0] if rows else None


_WS = re.compile(r"\s+")


def _norm(text):
    return _WS.sub(" ", text or "").strip().lower()


def find_posted(text, since):
    """The already-posted guard: the account's recent X posts (via Typefully
    analytics, which sees native posts too), matched on the post's opening.
    Returns the live URL or None. Fail-open: an analytics error returns None
    and we fall through to scheduling — the queue read-back still verifies."""
    want = _norm(text)[:80]
    if not want:
        return None
    try:
        q = urllib.parse.urlencode({
            "start_date": since.isoformat(),
            "end_date": date.today().isoformat(),
            "limit": 50,
        })
        got = _api("GET", f"/social-sets/{SOCIAL_SET}/analytics/x/posts?{q}")
    except RuntimeError as e:
        print(f"  (already-posted scan skipped: {e})", file=sys.stderr)
        return None
    for p in got.get("results", []):
        if _norm(p.get("preview_text"))[:80] == want:
            return p.get("url") or f"posted (id {p.get('post_id')})"
    return None


def publish_at_for(published_at, now):
    """The video's go-live day at 23:00Z, or next-free-slot if that's past."""
    live = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    when = live.replace(hour=PUBLISH_HOUR_UTC, minute=0, second=0, microsecond=0)
    if when < live:  # a video going live after 23:00Z still gets same-day-ish
        when = live + timedelta(minutes=30)
    return when if when > now else "next-free-slot"


def create_draft(text, link, when):
    """One two-part thread: the post, then the link as a self-reply."""
    body = {
        "platforms": {"x": {"enabled": True,
                            "posts": [{"text": text}, {"text": link}]}},
        "publish_at": when if isinstance(when, str) else when.isoformat(),
    }
    return _api("POST", f"/social-sets/{SOCIAL_SET}/drafts", body)


def verify_draft(draft_id):
    """Read the draft back: (status, scheduled_date, part_count). The read-back
    is the record. (The create body's field is publish_at, but the draft
    object reports it as scheduled_date.)"""
    d = _api("GET", f"/social-sets/{SOCIAL_SET}/drafts/{draft_id}")
    x = (d.get("platforms") or {}).get("x") or {}
    return d.get("status"), d.get("scheduled_date"), len(x.get("posts") or [])


def pending(state, only_week=None, only_video=None):
    """Every (week, video, xpost) still owed a schedule."""
    out = []
    for week, videos in sorted(state.items()):
        if only_week and week != only_week:
            continue
        for video, slot in sorted(videos.items()):
            if only_video and video != only_video:
                continue
            xp = slot.get("xpost") or {}
            if (xp.get("status") == "approved"
                    and not xp.get("typefully_draft_id")):
                out.append((week, video, xp))
    return out


def main():
    parser = argparse.ArgumentParser(description="Schedule approved long-form X posts into Typefully.")
    parser.add_argument("--week", default=None, help="only this week's Monday")
    parser.add_argument("--video", default=None, help="only this video lane")
    parser.add_argument("--dry-run", action="store_true", help="print the plan, no API calls")
    args = parser.parse_args()

    state = _read(VIDEO_IDEAS)
    todo = pending(state, args.week, args.video)
    if not todo:
        print("No approved, unscheduled long-form X posts — nothing to do.")
        return 0

    now = datetime.now(timezone.utc)
    failures = 0
    for week, video, xp in todo:
        label = f"{week} {video}/xpost"
        row = video_row(week, video)
        # Waiting paths must release the in-flight marker, or the publish
        # stamp's re-fire gets skipped as "already running" and the post sits
        # approved forever (hit 2026-08-22: approve click 3 min before the
        # publish log deadlocked wire 5's whichever-lands-second design).
        if not row or not row.get("youtube_video_id"):
            print(f"  {label}: video not on YouTube yet — waiting for the publish stamp.")
            if xp.pop("scheduling_since", None):
                _write(VIDEO_IDEAS, state)
            continue
        if not row.get("published_at"):
            print(f"  {label}: video row has no go-live moment — waiting.")
            if xp.pop("scheduling_since", None):
                _write(VIDEO_IDEAS, state)
            continue

        link = xp.get("link_reply") or \
            f"https://www.youtube.com/watch?v={row['youtube_video_id']}"
        when = publish_at_for(row["published_at"], now)

        if args.dry_run:
            print(f"  {label}: would schedule for {when} · link {link}")
            continue

        try:
            # Founder-posted-by-hand beats the queue: notice it, don't repeat it.
            since = date.today() - timedelta(days=30)
            posted = find_posted(xp["text"], since)
            if posted:
                xp["status"] = "posted"
                xp["posted_url"] = posted
                xp["noticed_at"] = _now()
                xp["scheduled_by"] = "content.xpost_schedule"
                print(f"  {label}: already live on X ({posted}) — marked done, nothing scheduled.")
            else:
                created = create_draft(xp["text"], link, when)
                draft_id = created["id"]
                status, publish_at, parts = verify_draft(draft_id)
                xp["link_reply"] = link
                xp["typefully_draft_id"] = draft_id
                xp["typefully_url"] = f"https://typefully.com/?d={draft_id}&a={SOCIAL_SET}"
                xp["typefully_status"] = status
                xp["scheduled_for"] = publish_at
                xp["scheduled_at"] = _now()
                xp["scheduled_by"] = "content.xpost_schedule"
                note = "" if (status == "scheduled" and parts == 2) else \
                    f"  <-- CHECK: status={status}, parts={parts} (wanted 2)"
                print(f"  ok {label}: draft {draft_id} at {publish_at}{note}")
                if note:
                    failures += 1
                    continue  # don't stamp the card done on a bad read-back
            xp.pop("scheduling_since", None)
            # persist after every slot so a mid-run crash never re-creates drafts
            _write(VIDEO_IDEAS, state)
            mark_flow_done(week, video)
        except (RuntimeError, KeyError) as e:
            failures += 1
            print(f"  FAILED {label}: {e}", file=sys.stderr)
            xp.pop("scheduling_since", None)
            _write(VIDEO_IDEAS, state)

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
