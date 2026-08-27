"""Send the weekly "Notice" issue via Resend.

The issue itself is drafted in session into fleet/email_broadcast.json and
approved on the /week card; this module only handles delivery. Three commands:

    python -m fleet.email_send --test              # send the week's issue to FOUNDER_EMAIL only
    python -m fleet.email_send --sync              # sync FH account holders into the Resend audience (no email sent)
    python -m fleet.email_send --schedule          # schedule the broadcast for next Thursday 9:00 AM Mountain

Design decisions (carried from the proven engine):
  * Recipients = the Fiboprana email list (the Resend audience the live
    landing page already writes into — the sibling repo's RESEND_AUDIENCE_ID).
    Resend owns unsubscribe links + suppression, so nobody we ever email is
    home-grown list plumbing.
    TODO: once the fiboprana-site product Supabase exists, --sync can also
    upsert account holders from its users table (see product_users below).
  * --schedule is only ever run after the founder approves the issue on the
    card (schedule-on-approval; never a blind cron).
  * Sends come from hello@fiboprana.com — verify the domain in Resend first.

Env (marketing .env):
    RESEND_API_KEY  (+ later: PRODUCT_SUPABASE_URL, PRODUCT_SUPABASE_SERVICE_KEY)
"""

import argparse
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

BROADCAST_FILE = ROOT / "fleet" / "email_broadcast.json"
RESEND_API = "https://api.resend.com"
AUDIENCE_NAME = "Fiboprana Notice"
FROM = "Fiboprana <hello@fiboprana.com>"
FOUNDER_EMAIL = "aiautomatedbusinesssolutions@gmail.com"
SEND_TZ = ZoneInfo("America/Denver")
SEND_HOUR = 9          # Thursday 9:00 AM Mountain, same slot every week
SEND_WEEKDAY = 3       # Monday=0 ... Thursday=3


def _resend(method, path, body=None):
    req = urllib.request.Request(
        RESEND_API + path,
        method=method,
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        headers={
            "Authorization": f"Bearer {os.environ['RESEND_API_KEY']}",
            "Content-Type": "application/json",
            # Cloudflare in front of api.resend.com blocks the default
            # Python-urllib signature with a 403/1010; any honest UA passes.
            "User-Agent": "fiboprana-notice-sender/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise SystemExit(f"Resend {method} {path} -> HTTP {e.code}: {e.read().decode()[:300]}")


def latest_issue():
    """The newest issue in the broadcast file, with its week key."""
    data = json.loads(BROADCAST_FILE.read_text(encoding="utf-8"))
    week = max(data.keys())
    return week, data[week]


# CAN-SPAM: every commercial email needs a physical postal address.
# TODO: fill in Fiboprana's registered postal address before the first send.
POSTAL = "Fiboprana · TODO: postal address (CAN-SPAM required)"


def assemble_text(issue, broadcast=False, week=None):
    """The same paste-ready plain text the /week card shows. Broadcasts append
    the why-you-got-this + unsubscribe + postal footer ({{{RESEND_UNSUBSCRIBE_URL}}}
    is Resend's merge tag, resolved per recipient at send time).

    Auto-attaches tracking to the CTA at assembly time: any fiboprana.com
    URL in it becomes a fiboprana.com/r/<code> short link stamped
    email-newsletter/<week>. Idempotent (test + schedule reuse one link) and
    fail-open (a shortener hiccup sends the bare URL). Around-the-web links
    are external curation — deliberately untouched."""
    from attribution import autolink
    cta = autolink.rewrite_urls(
        issue["cta"], source="email-newsletter", medium="email",
        campaign=f"newsletter-{week}" if week else "newsletter", content="cta")
    parts = [issue["pattern_md"], ""]
    # Legacy section: issues drafted before 2026-08-10 carried external links.
    # Founder rule since: the CTA's Fiboprana link is the email's only link,
    # so new issues have no around_the_web and this block never fires.
    if issue.get("around_the_web"):
        links = "\n".join(
            f"- {l['title']}{' - ' + l['url'] if l.get('url') else ''}\n  {l.get('note') or ''}"
            for l in issue["around_the_web"]
        )
        parts += ["From around the web:", links, ""]
    parts.append(cta)
    if broadcast:
        parts += ["", "You're getting this because you joined the Fiboprana list. "
                      "Unsubscribe: {{{RESEND_UNSUBSCRIBE_URL}}}", POSTAL]
    return "\n".join(parts)


def assemble_html(issue, unsubscribe=False, week=None):
    """Minimal readable HTML: the plain text with paragraphs preserved. The
    newsletter is deliberately a plain note, not a designed template."""
    import html as h
    paras = assemble_text(issue, week=week).split("\n\n")
    body = "\n".join(
        f'<p style="margin:0 0 16px; line-height:1.6;">{h.escape(p).replace(chr(10), "<br>")}</p>'
        for p in paras
    )
    foot = '<p style="margin:24px 0 0; font-size:13px; color:#8a95a6;">'
    if unsubscribe:
        foot += ("You're getting this because you joined the Fiboprana list. "
                 '<a href="{{{RESEND_UNSUBSCRIBE_URL}}}" style="color:#8a95a6;">Unsubscribe</a><br>')
    foot += h.escape(POSTAL) + "</p>"
    return (
        '<div style="max-width:600px; margin:0 auto; font-family:Georgia, serif; '
        'font-size:16px; color:#1a1a1a; padding:24px;">' + body + foot + "</div>"
    )


def product_users():
    """Account-holder emails from the Fiboprana product's Supabase users table.

    TODO: the fiboprana-site product database does not exist yet, so this
    returns [] and --sync is a no-op beyond ensuring the audience. When the
    product ships, set PRODUCT_SUPABASE_URL / PRODUCT_SUPABASE_SERVICE_KEY
    and restore the fetch below (filter out any *-tester@ accounts — they
    hard-bounce and damage sender reputation). Until then the audience is
    fed by the live landing page's subscribe form, not by this sync."""
    url = os.environ.get("PRODUCT_SUPABASE_URL")
    key = os.environ.get("PRODUCT_SUPABASE_SERVICE_KEY")
    if not url or not key:
        return []
    req = urllib.request.Request(
        url + "/rest/v1/users?select=email,created_at",
        headers={"apikey": key, "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        rows = json.load(resp)
    emails = {r["email"].lower() for r in rows if r.get("email")}
    return sorted(e for e in emails if "-tester@" not in e)


def ensure_audience():
    """Find or create the Notice audience; returns its id."""
    existing = _resend("GET", "/audiences").get("data", [])
    for a in existing:
        if a.get("name") == AUDIENCE_NAME:
            return a["id"]
    created = _resend("POST", "/audiences", {"name": AUDIENCE_NAME})
    return created["id"]


def sync_audience():
    """Upsert every account holder as a contact. Resend keeps unsubscribes
    sticky per contact, so re-adding an existing email never re-subscribes it."""
    audience_id = ensure_audience()
    contacts = _resend("GET", f"/audiences/{audience_id}/contacts").get("data", [])
    have = {c["email"].lower() for c in contacts}
    users = product_users()
    added = 0
    for email in users:
        if email in have:
            continue
        _resend("POST", f"/audiences/{audience_id}/contacts", {"email": email})
        added += 1
    print(f"audience '{AUDIENCE_NAME}': {len(users)} synced from the product DB, {added} newly added, "
          f"{len(have)} already present (landing-page signups land here directly)")
    return audience_id


def next_send_slot(now=None):
    """Next Thursday 9:00 AM Mountain as an ISO string Resend accepts."""
    now = now or datetime.now(SEND_TZ)
    days = (SEND_WEEKDAY - now.weekday()) % 7
    slot = (now + timedelta(days=days)).replace(hour=SEND_HOUR, minute=0, second=0, microsecond=0)
    if slot <= now:
        slot += timedelta(days=7)
    return slot


def cmd_test():
    week, issue = latest_issue()
    missing = [l["title"] for l in issue.get("around_the_web", []) if not l.get("url")]
    if missing:
        print(f"note: {len(missing)} around-the-web link(s) missing a URL: {missing}")
    result = _resend("POST", "/emails", {
        "from": FROM,
        "to": [FOUNDER_EMAIL],
        "subject": f"[TEST] {issue['subject']}",
        "text": assemble_text(issue, week=week),
        "html": assemble_html(issue, unsubscribe=False, week=week),
    })
    print(f"test send of week {week} ('{issue['subject']}') -> {FOUNDER_EMAIL} | id {result.get('id')}")


def cmd_schedule():
    week, issue = latest_issue()
    if issue.get("status") != "approved":
        raise SystemExit(f"week {week} issue status is '{issue.get('status')}' — "
                         "schedule only runs on an approved issue (mark it on the /week card).")
    missing = [l["title"] for l in issue.get("around_the_web", []) if not l.get("url")]
    if missing:
        raise SystemExit(f"refusing to schedule with URL-less links: {missing}")
    audience_id = sync_audience()
    slot = next_send_slot()
    broadcast = _resend("POST", "/broadcasts", {
        "audience_id": audience_id,
        "from": FROM,
        "subject": issue["subject"],
        "preview_text": issue.get("preview_text") or "",
        "text": assemble_text(issue, broadcast=True, week=week),
        "html": assemble_html(issue, unsubscribe=True, week=week),
        "name": f"Notice — week of {week}",
    })
    _resend("POST", f"/broadcasts/{broadcast['id']}/send", {"scheduled_at": slot.isoformat()})
    # Act-then-verify (founder directive 2026-08-07): persist the broadcast id
    # + slot onto the week's issue so the record can be checked against Resend
    # later — before this, the id only ever existed in a terminal scrollback.
    data = json.loads(BROADCAST_FILE.read_text(encoding="utf-8"))
    data[week].pop("send_scheduling_since", None)  # wire 6's in-flight marker
    data[week].update({"status": "scheduled", "broadcast_id": broadcast["id"],
                       "scheduled_at": slot.isoformat()})
    BROADCAST_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"broadcast for week {week} scheduled: {slot.strftime('%A %Y-%m-%d %H:%M %Z')} | id {broadcast['id']}")


def main():
    parser = argparse.ArgumentParser(description="Send the weekly Notice issue via Resend.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--test", action="store_true", help="send the issue to the founder only")
    mode.add_argument("--sync", action="store_true", help="sync account holders into the audience")
    mode.add_argument("--schedule", action="store_true", help="schedule the approved issue for Thu 9am MT")
    args = parser.parse_args()
    if args.test:
        cmd_test()
    elif args.sync:
        sync_audience()
    elif args.schedule:
        cmd_schedule()


if __name__ == "__main__":
    main()
