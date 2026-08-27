r"""Upload + schedule YouTube videos (shorts included) from a batch manifest.

    python -m fleet.youtube_upload manifest.json            # upload everything pending
    python -m fleet.youtube_upload manifest.json --limit 6  # quota-aware partial run

The manifest is a JSON list; each entry:

    {
      "file": "C:\\path\\to\\video.mp4",
      "title": "...",
      "description": "...",
      "tags": ["fiboprana", "..."],
      "publish_at": "2026-08-23T18:00:00Z"    # UTC; omit to upload private, unscheduled
    }

Each upload writes "video_id" (and "uploaded_at") back into the manifest entry,
so re-running the same manifest skips already-uploaded entries â€” the manifest is
its own ledger. Videos go up as PRIVATE with status.publishAt set, which is how
YouTube scheduling works; YouTube flips them public at that moment.

Quota: videos.insert costs 1600 units of the 10,000/day project quota, so at
most 6 uploads fit in one quota day (resets midnight US-Pacific). Use --limit.

Verification (act-then-verify): after each upload the script reads the video
back and prints the status YouTube actually stored. If the API project is
unverified with Google, YouTube may lock API uploads to private ("uploadStatus"
stays fine but the video shows as locked in Studio and publishAt is dropped) â€”
a missing publishAt in the read-back is the tell. Test with one video before
trusting a batch.

Uses the same channel credential as fleet.youtube_auth (scope includes
youtube.upload since 2026-08-07). Nothing here runs on a schedule; every
invocation is an explicit founder-directed action.
"""

import argparse
import json
import mimetypes
import urllib.error
import urllib.request
from pathlib import Path

from fleet.youtube_auth import access_token

UPLOAD_URL = ("https://www.googleapis.com/upload/youtube/v3/videos"
              "?uploadType=resumable&part=snippet,status")
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
CATEGORY_EDUCATION = "27"


def _start_session(token, meta):
    """Open a resumable-upload session; returns the session URL to PUT bytes to."""
    body = json.dumps(meta).encode()
    req = urllib.request.Request(UPLOAD_URL, data=body, method="POST", headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=UTF-8",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.headers["Location"]


def _put_bytes(session_url, token, path):
    """Send the whole file in one PUT; returns the created video resource."""
    data = path.read_bytes()
    mime = mimetypes.guess_type(str(path))[0] or "video/mp4"
    req = urllib.request.Request(session_url, data=data, method="PUT", headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": mime,
    })
    with urllib.request.urlopen(req, timeout=600) as resp:
        return json.loads(resp.read())


def _read_back(token, video_id):
    """Fetch what YouTube actually stored for the video (act-then-verify)."""
    req = urllib.request.Request(
        f"{VIDEOS_URL}?part=status,snippet&id={video_id}",
        headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        items = json.loads(resp.read()).get("items", [])
    return items[0] if items else None


def upload(entry):
    """Upload one manifest entry; returns (video_id, stored_status_dict)."""
    path = Path(entry["file"]).expanduser()
    if not path.is_file():
        raise SystemExit(f"File not found: {path}")
    status = {"privacyStatus": "private", "selfDeclaredMadeForKids": False}
    if entry.get("publish_at"):
        status["publishAt"] = entry["publish_at"]
    meta = {
        "snippet": {
            "title": entry["title"],
            "description": entry["description"],
            "tags": entry.get("tags", []),
            "categoryId": entry.get("category_id", CATEGORY_EDUCATION),
        },
        "status": status,
    }
    token = access_token()
    session_url = _start_session(token, meta)
    created = _put_bytes(session_url, token, path)
    video_id = created["id"]
    stored = _read_back(token, video_id)
    return video_id, (stored or {}).get("status", {})


def main():
    parser = argparse.ArgumentParser(description="Upload/schedule a manifest of videos.")
    parser.add_argument("manifest", help="path to the manifest JSON")
    parser.add_argument("--limit", type=int, default=0,
                        help="max uploads this run (quota: 6/day fit); 0 = no limit")
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    entries = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    done = 0
    for entry in entries:
        if entry.get("video_id"):
            continue
        if args.limit and done >= args.limit:
            print(f"--limit {args.limit} reached; remaining entries left pending")
            break
        print(f"Uploading: {entry['title']}")
        try:
            video_id, stored = upload(entry)
        except urllib.error.HTTPError as err:
            detail = err.read().decode(errors="replace")[:800]
            print(f"  FAILED {err.code}: {detail}")
            if err.code == 403 and "quota" in detail.lower():
                print("  daily quota exhausted; stopping (rerun after midnight Pacific)")
                break
            continue
        entry["video_id"] = video_id
        entry["stored_status"] = stored
        manifest_path.write_text(json.dumps(entries, indent=2), encoding="utf-8")
        done += 1
        scheduled = stored.get("publishAt", "MISSING - check for locked-private")
        print(f"  ok: https://youtube.com/shorts/{video_id}")
        print(f"  privacy={stored.get('privacyStatus')} publishAt={scheduled}")
    print(f"\n{done} uploaded this run; manifest updated in place.")


if __name__ == "__main__":
    main()
