"""CTA comment watcher — posts the pinned-comment CTA on every new video.

Ported from the personal-brand workflow 2026-08-23 (its watcher, running at
5-minute cadence there). The publish-time CTA comment is exactly the kind of
step that gets forgotten after a scheduled publish: the video goes live at
22:00 UTC with nobody at the desk. This removes the human step.

Rules (all ported, adapted to house conventions):
  * comment text is fleet/cta_comment.txt VERBATIM - edit that file to change
    the CTA everywhere, no code change. Empty/missing file = watcher idles.
  * long-form only: anything under 300s is a Short and stays link-free (CTA
    wiring rule); live streams/VODs are skipped too.
  * only videos published in the last 14 days (no deep backfill).
  * dedupe twice: the local ledger (fleet/cta_comments.json) plus a live
    check for an existing owner comment containing fiboprana.com (covers
    videos the founder commented by hand). A failed live check counts as
    "has one" so we never retry into a wall every pass.
  * the YouTube API cannot PIN a comment - that stays a 5-second tap in
    Studio; the link is live either way.

Posting needs the youtube.force-ssl scope (upgrade 2026-08-23 - see
fleet/youtube_auth.py). Until the founder re-consents once, the watcher
reports the missing grant and backs off for 6 hours instead of hammering.

Runs from the dashboard drain runner every sweep (10 min), so the comment
lands within minutes of a scheduled publish while the PC is on. Also:

    python -m fleet.cta_comment            # one pass
    python -m fleet.cta_comment --dry-run  # report what it would post
"""

import argparse
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

from fleet import store  # noqa: E402

COMMENT_FILE = ROOT / "fleet" / "cta_comment.txt"
LEDGER_FILE = ROOT / "fleet" / "cta_comments.json"
MIN_DURATION_S = 300          # under this = a Short, stays link-free
MAX_AGE_DAYS = 14
LINK_MARKER = "fiboprana.com"
SCOPE_BACKOFF_H = 6
_DUR_RE = re.compile(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$")
API = "https://www.googleapis.com/youtube/v3"


def _duration_s(iso):
    m = _DUR_RE.match(iso or "")
    if not m:
        return 0
    h, mi, s = (int(g or 0) for g in m.groups())
    return h * 3600 + mi * 60 + s


def _read_ledger():
    try:
        return json.loads(LEDGER_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_ledger(ledger):
    tmp = LEDGER_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(ledger, indent=2), encoding="utf-8")
    tmp.replace(LEDGER_FILE)


def _api_post(path, token, body, **params):
    qs = urllib.parse.urlencode(params)
    req = urllib.request.Request(
        f"{API}/{path}?{qs}", data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _has_owner_comment(api_get, video_id, channel_id):
    """Existing top-level owner comment carrying a fiboprana.com link?
    Errors (comments disabled, quota) count as yes - never retry into a wall."""
    try:
        threads = api_get("commentThreads", part="snippet", videoId=video_id,
                          maxResults=50, textFormat="plainText")
    except Exception:  # noqa: BLE001
        return True
    for t in threads.get("items") or []:
        s = ((t.get("snippet") or {}).get("topLevelComment") or {}).get("snippet") or {}
        author = (s.get("authorChannelId") or {}).get("value")
        if author == channel_id and LINK_MARKER in (s.get("textOriginal") or ""):
            return True
    return False


def run_pass(dry_run=False):
    """One idempotent pass; returns a list of action strings (empty = quiet)."""
    try:
        text = COMMENT_FILE.read_text(encoding="utf-8-sig").strip()
    except OSError:
        text = ""
    if not text:
        return []

    ledger = _read_ledger()
    blocked = ledger.get("_scope_blocked")
    if blocked:
        since = datetime.fromisoformat(blocked)
        if datetime.now(timezone.utc) - since < timedelta(hours=SCOPE_BACKOFF_H):
            return []

    try:
        from fleet.youtube_auth import access_token, api_get
        token = access_token()
    except Exception:  # noqa: BLE001 - no credential here: quiet skip
        return []

    actions = []
    try:
        chans = api_get("channels", part="id,contentDetails", mine="true")
        chan = (chans.get("items") or [None])[0]
        if not chan:
            return ["cta: no channel visible to the credential"]
        uploads = chan["contentDetails"]["relatedPlaylists"]["uploads"]
        items = api_get("playlistItems", part="contentDetails",
                        playlistId=uploads, maxResults=10)
        candidates = [i["contentDetails"]["videoId"]
                      for i in items.get("items") or []
                      if i["contentDetails"]["videoId"] not in ledger]
        if not candidates:
            return []
        vids = api_get("videos", id=",".join(candidates),
                       part="snippet,status,contentDetails,liveStreamingDetails")
        cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)
        for v in vids.get("items") or []:
            vid = v["id"]
            snip = v["snippet"]
            if v["status"].get("privacyStatus") != "public":
                continue  # scheduled/unlisted: check again next pass
            published = datetime.fromisoformat(
                snip["publishedAt"].replace("Z", "+00:00"))
            if published < cutoff:
                ledger[vid] = {"skipped": "too old"}
                continue
            if v.get("liveStreamingDetails"):
                ledger[vid] = {"skipped": "stream/VOD"}
                continue
            seconds = _duration_s((v.get("contentDetails") or {}).get("duration"))
            if seconds < MIN_DURATION_S:
                ledger[vid] = {"skipped": f"short ({seconds}s), stays link-free"}
                continue
            if _has_owner_comment(api_get, vid, chan["id"]):
                ledger[vid] = {"already_had_comment": True}
                actions.append(f"cta: {snip['title']!r} already has one - recorded")
                continue
            if dry_run:
                actions.append(f"cta: WOULD comment on {snip['title']!r} ({vid})")
                continue
            try:
                _api_post("commentThreads", token, part="snippet", body={
                    "snippet": {"videoId": vid, "topLevelComment": {
                        "snippet": {"textOriginal": text}}}})
            except urllib.error.HTTPError as e:
                detail = e.read().decode(errors="replace")[:200]
                if e.code == 403 and "insufficient" in detail.lower():
                    ledger["_scope_blocked"] = datetime.now(
                        timezone.utc).isoformat(timespec="seconds")
                    actions.append(
                        "cta: BLOCKED - token lacks youtube.force-ssl; re-run "
                        "python -m fleet.youtube_auth (backing off 6h)")
                    store.record_heartbeat(
                        agent_name="cta_comment", status="failure",
                        message="comment insert blocked on missing "
                                "youtube.force-ssl scope")
                    break
                actions.append(f"cta: post FAILED for {vid}: HTTP {e.code} {detail}")
                continue
            ledger.pop("_scope_blocked", None)
            ledger[vid] = {"commented_at": datetime.now(timezone.utc).isoformat(
                timespec="seconds"), "title": snip["title"]}
            actions.append(f"cta: commented on {snip['title']!r} ({vid}) - "
                           "pin it in Studio when convenient")
            store.record_heartbeat(agent_name="cta_comment", status="success",
                                   message=f"CTA comment posted on {vid}")
    finally:
        if not dry_run:
            _write_ledger(ledger)
    return actions


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    actions = run_pass(dry_run=args.dry_run)
    for a in actions:
        print(a)
    if not actions:
        print("quiet pass - nothing owed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
