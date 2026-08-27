# attribution/

Short-link redirect + click logging so every published piece of content (X
post, TikTok caption, LinkedIn post, Reddit DM, newsletter, etc.) can be
tied back to the observation it came from and the channel it went out on.

**v2 (2026-08-08): publicly hosted.** v1 kept everything in a local SQLite
file, so the redirect only worked on this machine and no published link was
ever really tracked. v2 splits the roles:

- **Public redirect** — `https://fiboprana.com/r/<code>`, a route in the
  Fiboprana app (fiboprana-site repo, `src/app/r/[code]/route.ts`, Vercel).
  **TODO: not yet implemented in fiboprana-site** — port the route from the
  original engine's product repo before publishing any short links.
  The ONLY attribution surface on the public internet.
- **Private dashboard** — this module, still local-only Flask. Creates
  links, reads analytics. Never exposed, so it needs no auth: the README's
  old warning ("lock down everything except /r/*") is satisfied by
  construction instead of middleware.
- **Shared state** — `marketing.short_links` + `marketing.link_clicks` in
  Supabase (migration `20260808000001_attribution_links.sql`). A link
  created here is live on fiboprana.com the moment the row lands.

## What it does

1. You finish drafting a post / DM and click **Create tracking link** on the
   observation detail page or the content-gen result page.
2. The form is prefilled with channel + observation + post text. Confirm and
   submit; the dashboard generates a 6-character base62 code and shows you
   the full short URL (`https://fiboprana.com/r/<code>`) ready to copy.
3. You paste that short URL into your post / DM / newsletter and publish.
4. Every click hits the app's `/r/<code>`, which logs the visit to
   `marketing.link_clicks` and 302-redirects to the real destination with
   UTM params appended so downstream analytics see them.
5. The `/attribution` dashboard (local) shows totals, a channel breakdown,
   and a per-link click log so you can see which observations and channels
   actually pull.

## Files

```
attribution/
├── README.md
├── store.py        # Supabase data access (fleet/supabase.py underneath)
├── shortener.py    # base62 code generation + UTM-aware URL assembly
├── tracker.py      # record_click for the LOCAL /r route (daily-salted IP hashing)
├── analytics.py    # dashboard aggregates (fetch + plain-Python grouping)
├── autolink.py     # auto-attach: mint() find-or-create + rewrite_urls() for drafts + CLI
└── routes.py       # Flask blueprint: dashboard + local test redirect
```

## Auto-attach (v2.1) — drafts arrive tracked

Drafting lanes call `autolink.rewrite_urls()` at their finalize point, so
agent copy leaves the repo with `fiboprana.com/r/<code>` links already in
place — no manual create-and-paste. Wired lanes: weekly X batch link
replies (`content/x_run.py`, at generation), the long-form video X post
(`fleet/dashboard.py`, at the founder's approve click), the newsletter CTA
(`fleet/email_send.py::assemble_text`). YouTube descriptions use the CLI
(`python -m attribution.autolink <url> --source youtube --medium
description --campaign video-<slug>`). Minting is idempotent per
(destination x UTM identity) and fail-open — Supabase trouble ships the
bare URL rather than blocking a batch or a send. Lanes that are link-free
by channel strategy (replies, X post bodies, IG/TikTok captions) are
untouched.

Python stdlib only — no new dependencies. The old `links.db` / `schema.sql`
/ `db.py` SQLite layer was removed in v2 (it held one test link, no clicks).

## Routes (registered in `app.py`)

| Route | Method | Purpose |
|------|--------|---------|
| `/r/<code>` | GET | LOCAL twin of the public redirect, for end-to-end testing. Logs the click, 302s to destination + UTM. |
| `/attribution` | GET | Dashboard: totals, channel breakdown, list of links. |
| `/attribution/new` | GET, POST | Create-link form. POST prefills from a calling page. |
| `/attribution/create` | POST | Inserts a new `short_links` row, redirects to the detail page. |
| `/attribution/<id>` | GET | Per-link detail: short URL, full click log, archive button. |
| `/attribution/<id>/archive` | POST | Toggle the `archived` flag. The redirect keeps working forever; archive only hides the row from the default dashboard. |

`ATTRIBUTION_BASE_URL` defaults to `https://fiboprana.com` now; point it at
`http://localhost:5000` in `.env` if you want copied links to use the local
redirect during testing.

## Channel vocabulary

`utm_source` is free text in the DB. The form has a `<datalist>` of common
values for autocomplete; type anything else and it just gets stored.
Current seeded options:

```
x   tiktok   linkedin   reddit-dm   reddit-post
discord   email-newsletter   in-person
```

Add values to `ATTRIBUTION_SOURCE_OPTIONS` in `routes.py` once you're typing
the same thing repeatedly.

## Privacy

Click rows store `sha256(ip + today's UTC date)` instead of the raw IP, so
repeat visits within a day are roughly correlatable but the storage doesn't
hold actual PII. User-agent and referer are stored verbatim (truncated to
500 chars). No cookies, no fingerprinting, no third-party analytics. The
public route hashes identically (see the app-side route), so local and
public clicks are indistinguishable in storage.

## Not included (by design)

- **Conversion tracking** — no "did this click lead to a signup?" yet. Depends
  on Fiboprana app-side cooperation or a manual mark-converted workflow.
  Plan to add once there are real conversions to track.
- **Bot filtering** — Twitter / LinkedIn link-preview crawlers will inflate
  counts. Everything is logged; a UA blocklist can come later, and the raw
  user_agent column means it can be applied retroactively at query time.
- **A/B variants** — one link, one destination. No split-testing.

## Schema evolution

Tables live in Supabase now — evolve them the same way as every other
marketing table: a new file in `supabase/migrations/`, applied via Studio's
SQL editor. Ledger posture: no DELETE grants anywhere; `archived` is the
only mutable column on `short_links`; `link_clicks` is append-only.

### `observation_id` is a soft reference

The `observation_id` column points at rows in `capture/observations` — a
table that lives in a local SQLite file (`capture/conversations.db`) while
the links now live in Supabase. Nothing can enforce a foreign key across
that boundary, so it's a soft reference only: the dashboard reads / writes
`observation_id` as if it were an FK, but nothing prevents an observation
from being deleted out from under a `short_links` row. Same trade-off the
split always had; flagging it so future-you isn't surprised.

### UTM merge logic exists twice

`shortener.build_destination_url` (Python, local) and the app route's
`buildDestinationUrl` (TypeScript, public) implement the same merge rules.
If you change one, change the other.
