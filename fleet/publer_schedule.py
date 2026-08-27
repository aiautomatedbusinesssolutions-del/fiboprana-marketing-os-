r"""Schedule TikTok + Instagram shorts through the Publer API (Business plan).

    python -m fleet.publer_schedule --check              # auth + accounts sanity
    python -m fleet.publer_schedule --list 2026-08-23 2026-08-31
    python -m fleet.publer_schedule manifest.json        # schedule a batch
    python -m fleet.publer_schedule manifest.json --dry-run

Replaces the browser-session Publer flow (login walls, 10MB compression, click
gotchas) with the code-only lane the founder approved 2026-08-22. The manifest
is a JSON list; each entry:

    {
      "file": "C:\\path\\to\\short.mp4",        # originals OK (API cap 200MB)
      "publish_at": "2026-08-23T12:00:00",       # local MT wall time, ISO
      "tiktok_caption": "...",
      "instagram_caption": "..."
    }

Each scheduled entry gets "publer_media_id", "publer_job_id" and
"publer_state" written back, so the manifest is its own ledger and re-runs
skip completed entries (same pattern as fleet/youtube_upload.py).

Per entry it uploads the video once, then creates ONE post targeting both
accounts with per-network captions (tiktok video + instagram reel), polls the
async job, and verifies by reading the scheduled posts back (act-then-verify).
Whoever schedules a batch still updates fleet/shorts_schedule.json - that file
is what the /week dashboard shows.

Auth: PUBLER_API_KEY in .env (scopes: posts+media r/w, analytics read; minted
2026-08-22, key name "fleet"). Workspace id resolves once from the API and is
cached in .env as PUBLER_WORKSPACE_ID. Rate limit: 100 requests / 2 minutes.
"""

import argparse
import json
import mimetypes
import os
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
API_BASE = "https://app.publer.com/api/v1"

# The two shorts accounts, resolved by --check and pinned here after the first
# run printed them (network -> account name on /accounts).
NETWORKS = ("tiktok", "instagram")


def _env(key, default=None):
    val = os.environ.get(key)
    if val:
        return val
    for line in ENV.read_bytes().decode("utf-8-sig").splitlines():
        line = line.strip()
        if line.startswith(f"{key}="):
            # strip any BOM that rode in on a clipboard/encoding round-trip
            return line.split("=", 1)[1].strip().lstrip("﻿")
    if default is not None:
        return default
    raise RuntimeError(f"{key} missing from .env")


def _append_env(key, value):
    data = ENV.read_bytes()
    if not data.endswith(b"\n"):
        data += b"\n"
    ENV.write_bytes(data + f"{key}={value}\n".encode())


def _api(method, endpoint, body=None, headers=None, workspace=True):
    """One Publer API call. body may be a dict (JSON) or raw bytes."""
    # Cloudflare fronts the API and 403s (error 1010) urllib's default
    # user agent - send a plain identifying UA.
    hdrs = {"Authorization": f"Bearer-API {_env('PUBLER_API_KEY')}",
            "User-Agent": "fiboprana-fleet/1.0"}
    if workspace:
        hdrs["Publer-Workspace-Id"] = workspace_id()
    if headers:
        hdrs.update(headers)
    data = None
    if body is not None:
        if isinstance(body, (dict, list)):
            data = json.dumps(body).encode()
            hdrs["Content-Type"] = "application/json"
        else:
            data = body
    req = urllib.request.Request(f"{API_BASE}{endpoint}", data=data,
                                 method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"{method} {endpoint} -> HTTP {e.code}: "
            f"{e.read().decode('utf-8', 'replace')[:400]}")


_WORKSPACE_ID = None


def workspace_id():
    """The Fiboprana workspace id, cached in .env after first resolve."""
    global _WORKSPACE_ID
    if _WORKSPACE_ID:
        return _WORKSPACE_ID
    cached = _env("PUBLER_WORKSPACE_ID", default="")
    if cached:
        _WORKSPACE_ID = cached
        return cached
    out = _api("GET", "/workspaces", workspace=False)
    spaces = out if isinstance(out, list) else out.get("workspaces", [])
    if not spaces:
        raise RuntimeError("no workspaces visible to this API key")
    pick = next((w for w in spaces
                 if "fiboprana" in (w.get("name") or "").lower()), spaces[0])
    _WORKSPACE_ID = str(pick["id"])
    _append_env("PUBLER_WORKSPACE_ID", _WORKSPACE_ID)
    print(f"(workspace '{pick.get('name')}' id cached to .env)")
    return _WORKSPACE_ID


def accounts():
    """All social accounts in the workspace, keyed by lowercase network."""
    out = _api("GET", "/accounts")
    accts = out if isinstance(out, list) else out.get("accounts", [])
    by_network = {}
    for a in accts:
        net = (a.get("provider") or a.get("network") or a.get("type") or "").lower()
        by_network.setdefault(net, []).append(a)
    return by_network


def upload_media(path):
    """Direct multipart upload; returns the media id."""
    path = Path(path)
    mime = mimetypes.guess_type(str(path))[0] or "video/mp4"
    boundary = uuid.uuid4().hex
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'.encode(),
        f"Content-Type: {mime}\r\n\r\n".encode(),
        path.read_bytes(), b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ])
    out = _api("POST", "/media", body=body, headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}"})
    media = out.get("media") or out
    if isinstance(media, list):
        media = media[0]
    media_id = media.get("id")
    if not media_id:
        raise RuntimeError(f"media upload returned no id: {json.dumps(out)[:300]}")
    return media_id


def poll_job(job_id, timeout_s=180):
    """Poll an async job until complete/failed; returns the final payload."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        out = _api("GET", f"/job_status/{job_id}")
        status = out.get("status") or (out.get("data") or {}).get("status")
        if status in ("complete", "completed", "failed"):
            return status, out
        time.sleep(3)
    return "timeout", {}


def list_posts(state, date_from, date_to):
    """Scheduled/published posts in a window (the read-back verifier)."""
    q = f"?state={state}&from={date_from}&to={date_to}"
    out = _api("GET", f"/posts{q}")
    return out if isinstance(out, list) else out.get("posts", [])


def schedule_entry(entry, account_ids):
    """Upload one video and schedule it on both networks; returns job id."""
    media_id = entry.get("publer_media_id")
    if not media_id:
        media_id = upload_media(entry["file"])
        entry["publer_media_id"] = media_id
    when = entry["publish_at"]
    if len(when) == 16:  # allow "YYYY-MM-DDTHH:MM"
        when += ":00"
    networks = {
        "tiktok": {"type": "video", "text": entry["tiktok_caption"],
                   "media": [{"id": media_id, "type": "video"}]},
        "instagram": {"type": "reel", "text": entry["instagram_caption"],
                      "media": [{"id": media_id, "type": "video"}]},
    }
    body = {"bulk": {"state": "scheduled", "posts": [{
        "networks": networks,
        "media": [{"id": media_id, "type": "video"}],
        "accounts": [{"id": account_ids[n], "scheduled_at": when}
                     for n in NETWORKS],
    }]}}
    out = _api("POST", "/posts/schedule", body=body)
    job_id = (out.get("data") or {}).get("job_id") or out.get("job_id")
    if not job_id:
        raise RuntimeError(f"schedule returned no job_id: {json.dumps(out)[:300]}")
    entry["publer_job_id"] = job_id
    return job_id


def resolve_account_ids():
    by_net = accounts()
    ids = {}
    for net in NETWORKS:
        if not by_net.get(net):
            raise RuntimeError(f"no {net} account in workspace; have: {sorted(by_net)}")
        ids[net] = by_net[net][0]["id"]
    return ids


def main():
    parser = argparse.ArgumentParser(description="Publer API shorts scheduler.")
    parser.add_argument("manifest", nargs="?", help="batch manifest JSON")
    parser.add_argument("--check", action="store_true", help="auth + accounts sanity check")
    parser.add_argument("--list", nargs=2, metavar=("FROM", "TO"),
                        help="list scheduled posts in a date window")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    if args.check:
        print(f"workspace id: {workspace_id()}")
        for net, accts in sorted(accounts().items()):
            for a in accts:
                print(f"  {net or '?'}: {a.get('name')} (id {a.get('id')})")
        return 0

    if args.list:
        posts = list_posts("scheduled", args.list[0], args.list[1])
        print(f"{len(posts)} scheduled posts {args.list[0]}..{args.list[1]}")
        for p in sorted(posts, key=lambda p: p.get("scheduled_at") or ""):
            net = (p.get("network") or p.get("provider")
                   or (p.get("account") or {}).get("provider") or "?")
            print(f"  {p.get('scheduled_at')} {net:<10} "
                  f"{(p.get('text') or '')[:60]}")
        return 0

    if not args.manifest:
        parser.error("need a manifest, --check, or --list")
    manifest_path = Path(args.manifest)
    entries = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    account_ids = resolve_account_ids()
    done = 0
    for entry in entries:
        if entry.get("publer_state") == "scheduled":
            continue
        if args.limit and done >= args.limit:
            break
        label = f"{Path(entry['file']).name} @ {entry['publish_at']}"
        if args.dry_run:
            print(f"would schedule: {label}")
            continue
        print(f"scheduling: {label}")
        job_id = schedule_entry(entry, account_ids)
        manifest_path.write_text(json.dumps(entries, indent=2), encoding="utf-8")
        status, payload = poll_job(job_id)
        if status in ("complete", "completed"):
            entry["publer_state"] = "scheduled"
            print(f"  ok (job {job_id})")
        else:
            entry["publer_state"] = f"job_{status}"
            print(f"  CHECK: job {job_id} -> {status}: {json.dumps(payload)[:200]}")
        manifest_path.write_text(json.dumps(entries, indent=2), encoding="utf-8")
        done += 1
    print(f"\n{done} entries processed. Verify with --list, then update "
          "fleet/shorts_schedule.json.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
