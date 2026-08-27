"""The click-state mirror + the cloud drain job (off-PC drain v1).

    python -m fleet.fleet_state              # the dispatch "drain" job
    python -m fleet.fleet_state --push       # bootstrap: mirror local files up
    python -m fleet.fleet_state --show       # what the mirror holds

The problem this solves (founder-approved build 2026-08-22): the /week
click-state lives in local JSON files, so the publish auto-notice and the
xpost scheduler could only run while the founder's PC was on. Full migration
of state to Supabase was deliberately rejected for now (too much churn right
before the first real week). Instead, three rows in marketing.fleet_state:

  flow_state, video_ideas   pushed UP on every local write (the local files
                            remain the source of truth; the cloud NEVER
                            writes these rows, so it can never clobber them)
  cloud_results             the cloud drain's outbox: deltas only - a video
                            it logged, an xpost it scheduled. The local
                            drain runner (fleet/dashboard.py) applies each
                            delta to the local files and clears it.

The cloud drain job runs with the daily dispatch: with the PC off overnight,
a video scheduled in Studio still gets logged (marketing.videos + outcome
windows) and its approved long-form X post still reaches Typefully by the
next 9 AM run; the local files catch up the moment the dashboard runs again.
"""

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from fleet import asset_store, supabase  # noqa: E402

TABLE = "fleet_state"
VIDEO_LANES = ("news", "feature", "ascent")
_DURATION_RE = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _monday():
    return str(date.today() - timedelta(days=date.today().weekday()))


# ── the mirror (push is called from every local state writer) ────────────────
def push(name, data):
    """Best-effort upsert of one state row. Never raises: a mirror miss must
    never break a local save (the mirror heals on the next write)."""
    try:
        supabase.upsert(TABLE, {"name": name, "data": data,
                                "updated_at": _now()},
                        on_conflict="name", returning=False)
        return True
    except Exception:  # noqa: BLE001
        return False


def pull(name):
    try:
        rows = supabase.select(TABLE, params={"name": f"eq.{name}", "limit": 1})
        return rows[0]["data"] if rows else None
    except Exception:  # noqa: BLE001
        return None


# ── the cloud drain job ──────────────────────────────────────────────────────
def _youtube_candidates(monday_iso):
    """This week's unlogged long-form uploads on the channel (same filters as
    the /week scan: >=240s, go-live inside the week, not in marketing.videos)."""
    from zoneinfo import ZoneInfo

    from fleet import youtube_auth

    monday = datetime.strptime(monday_iso, "%Y-%m-%d").replace(
        tzinfo=ZoneInfo("America/Denver"))
    ch = youtube_auth.api_get("channels", part="contentDetails", mine="true")
    uploads = ch["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
    pl = youtube_auth.api_get("playlistItems", part="contentDetails",
                              playlistId=uploads, maxResults=15)
    ids = [i["contentDetails"]["videoId"] for i in pl.get("items", [])]
    if not ids:
        return []
    vids = youtube_auth.api_get("videos", part="snippet,status,contentDetails",
                                id=",".join(ids))
    fresh = []
    for v in vids.get("items", []):
        m = _DURATION_RE.fullmatch(v["contentDetails"].get("duration", ""))
        h, mn, s = (int(x or 0) for x in m.groups()) if m else (0, 0, 0)
        if h * 3600 + mn * 60 + s < 240:
            continue
        stamp = v["status"].get("publishAt") or v["snippet"].get("publishedAt")
        if not stamp:
            continue
        if datetime.fromisoformat(stamp.replace("Z", "+00:00")) < monday:
            continue
        fresh.append({"id": v["id"], "title": v["snippet"].get("title") or "",
                      "published_at": stamp})
    if not fresh:
        return []
    logged = supabase.select("videos", params={
        "select": "youtube_video_id",
        "youtube_video_id": f"in.({','.join(v['id'] for v in fresh)})"})
    seen = {r["youtube_video_id"] for r in logged}
    return [v for v in fresh if v["id"] not in seen]


def _slugify(text, fallback):
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:60]
    return slug or fallback


def _lane_title(slot):
    picked = slot.get("picked")
    idea = next((i for i in slot.get("ideas", []) if i.get("id") == picked), None)
    if idea and idea.get("title"):
        return idea["title"]
    script = (slot.get("script") or {}).get("script_md") or ""
    m = re.search(r"^#\s+(.+)$", script, re.MULTILINE)
    return m.group(1).strip() if m else None


def cloud_publish_scan(week, lane, steps, ideas):
    """One lane's publish leg, cloud-side. Returns a delta dict or None.
    Refuses ambiguity exactly like the /week scan: several candidates with no
    clear title match stays untouched for the founder's page."""
    candidates = _youtube_candidates(week)
    if not candidates:
        return None
    slot = (ideas.get(week) or {}).get(lane) or {}
    pick = candidates[0] if len(candidates) == 1 else None
    if pick is None:
        title = _lane_title(slot) or ""
        want = set(re.sub(r"[^a-z0-9]+", " ", title.lower()).split())

        def overlap(v):
            got = set(re.sub(r"[^a-z0-9]+", " ", v["title"].lower()).split())
            return len(want & got) / max(1, min(len(want), len(got)))

        scored = sorted(((overlap(v), v) for v in candidates),
                        key=lambda x: x[0], reverse=True)
        if want and scored[0][0] >= 0.5 and scored[0][0] > scored[1][0]:
            pick = scored[0][1]
    if pick is None:
        return None
    rows = supabase.select("videos", params={
        "pillar": f"eq.{lane}", "created_at": f"gte.{week}",
        "order": "created_at.desc", "limit": 1})
    if rows:
        row = rows[0]
    else:
        title = _lane_title(slot) or pick["title"]
        row = asset_store.create_video(
            slug=_slugify(title, f"{lane}-{week}"), pillar=lane,
            title_final=title, notes="logged by the cloud drain")
    published_at = datetime.fromisoformat(
        pick["published_at"].replace("Z", "+00:00"))
    asset_store.mark_published(row["id"], youtube_video_id=pick["id"],
                              published_at=published_at)
    return {"kind": "publish", "week": week, "video": lane,
            "youtube_video_id": pick["id"], "title": pick["title"],
            "published_at": pick["published_at"], "at": _now()}


def cloud_xpost_schedule(week, lane, xp):
    """One lane's xpost leg, cloud-side, reusing the wire-5 module's own
    functions (already-posted guard, go-live slot, read-back verification).
    Returns a delta dict or None if the video isn't logged yet."""
    from content.xpost_schedule import (create_draft, find_posted,
                                        publish_at_for, verify_draft,
                                        video_row)

    row = video_row(week, lane)
    if not row or not row.get("youtube_video_id") or not row.get("published_at"):
        return None
    link = xp.get("link_reply") or \
        f"https://www.youtube.com/watch?v={row['youtube_video_id']}"
    posted = find_posted(xp.get("text") or "", date.today() - timedelta(days=30))
    if posted:
        return {"kind": "xpost", "week": week, "video": lane,
                "status": "posted", "posted_url": posted, "at": _now()}
    when = publish_at_for(row["published_at"], datetime.now(timezone.utc))
    created = create_draft(xp["text"], link, when)
    status, publish_at, parts = verify_draft(created["id"])
    if status != "scheduled" or parts != 2:
        raise RuntimeError(f"draft {created['id']} read-back: "
                           f"status={status}, parts={parts}")
    return {"kind": "xpost", "week": week, "video": lane,
            "link_reply": link, "typefully_draft_id": created["id"],
            "typefully_status": status, "scheduled_for": publish_at,
            "at": _now()}


def cloud_drain():
    """The dispatch job: act on the mirror, write deltas to the outbox."""
    flow = pull("flow_state")
    ideas = pull("video_ideas")
    if flow is None or ideas is None:
        print("no mirror yet (the local dashboard pushes it on first write) - nothing to do")
        return 0, "no mirror yet"
    week = _monday()
    steps = flow.get(week, {})
    results = pull("cloud_results") or {}
    actions = []

    for lane in VIDEO_LANES:
        if f"{lane}/publish" in steps or f"{week}:{lane}:publish" in results:
            continue
        if not any(f"{lane}/{s}" in steps for s in ("record", "edit")):
            continue
        try:
            delta = cloud_publish_scan(week, lane, steps, ideas)
        except Exception as e:  # noqa: BLE001 - one lane must not kill the job
            actions.append(f"publish {lane} FAILED: {e}")
            continue
        if delta:
            results[f"{week}:{lane}:publish"] = delta
            actions.append(f"logged {lane} video {delta['youtube_video_id']}")

    for lane in VIDEO_LANES:
        slot = (ideas.get(week) or {}).get(lane) or {}
        xp = slot.get("xpost") or {}
        key = f"{week}:{lane}:xpost"
        if (xp.get("status") != "approved" or xp.get("typefully_draft_id")
                or key in results):
            continue
        # a publish delta from THIS run makes the video row visible already
        try:
            delta = cloud_xpost_schedule(week, lane, xp)
        except Exception as e:  # noqa: BLE001
            actions.append(f"xpost {lane} FAILED: {e}")
            continue
        if delta:
            results[key] = delta
            actions.append(f"scheduled {lane} xpost "
                           f"({delta.get('typefully_draft_id') or 'noticed posted'})")

    if actions:
        push("cloud_results", results)
    summary = "; ".join(actions) if actions else "nothing owed"
    print(f"cloud drain: {summary}")
    failed = any("FAILED" in a for a in actions)
    return (1 if failed else 0), summary


def main():
    parser = argparse.ArgumentParser(description="Click-state mirror / cloud drain.")
    parser.add_argument("--push", action="store_true",
                        help="bootstrap: push the local state files up")
    parser.add_argument("--show", action="store_true")
    args = parser.parse_args()

    if args.push:
        for name, fname in (("flow_state", "flow_state.json"),
                            ("video_ideas", "video_ideas.json")):
            path = ROOT / "fleet" / fname
            data = json.loads(path.read_text(encoding="utf-8"))
            ok = push(name, data)
            print(f"{name}: {'pushed' if ok else 'PUSH FAILED'} ({len(json.dumps(data))} bytes)")
        return 0
    if args.show:
        for name in ("flow_state", "video_ideas", "cloud_results"):
            data = pull(name)
            print(f"{name}: {'missing' if data is None else str(len(json.dumps(data))) + ' bytes'}")
            if name == "cloud_results" and data:
                for k, v in data.items():
                    print(f"  {k}: {v.get('kind')} @ {v.get('at')}")
        return 0

    code, summary = cloud_drain()
    try:
        from fleet import store
        store.record_heartbeat(agent_name="drain",
                               status="success" if code == 0 else "failure",
                               message=summary[:300])
    except Exception:  # noqa: BLE001
        pass
    return code


if __name__ == "__main__":
    raise SystemExit(main())
