"""Flask blueprint for the attribution module.

Owns the LOCAL side of attribution: the dashboard, create form, detail,
and archive endpoints under /attribution, plus a local /r/<code> redirect
for testing links end-to-end without leaving the machine. The PUBLIC
redirect — the one published posts actually use — is the Fiboprana app's
/r/<code> route on Vercel (fiboprana-site repo, src/app/r/[code]/route.ts);
both read the same marketing.short_links table, so a link created here is
live on fiboprana.com the moment the row lands.

Constants that are purely about attribution configuration (default
destination URL, source/medium autocomplete options) live here too instead
of in app.py — they have no callers outside this module.
"""

import os

from flask import Blueprint, abort, redirect, render_template, request, url_for

from fleet.supabase import SupabaseError

from . import analytics as attribution_analytics
from . import shortener as attribution_shortener
from . import store as attribution_store
from . import tracker as attribution_tracker


# Public base URL for short links — the Fiboprana app hosts the public
# redirect, so copied links point at the real domain by default. Override
# via .env for local end-to-end testing (http://localhost:5000).
ATTRIBUTION_BASE_URL = os.environ.get(
    "ATTRIBUTION_BASE_URL", "https://fiboprana.com"
).rstrip("/")
ATTRIBUTION_DEFAULT_DESTINATION = "https://fiboprana.com"

# Free-text in the DB; these are the autocomplete suggestions in the form.
ATTRIBUTION_SOURCE_OPTIONS = [
    "x", "tiktok", "linkedin",
    "reddit-dm", "reddit-post",
    "discord", "email-newsletter", "in-person",
]
ATTRIBUTION_MEDIUM_OPTIONS = [
    "post", "dm", "bio", "reply", "comment", "share", "signature",
]


bp = Blueprint("attribution", __name__)


@bp.route("/r/<code>")
def redirect_short(code):
    """Local twin of the public redirect — lets the whole flow be tested
    from this machine. Looks up the code, records a click, then 302s to
    the destination with UTM params appended. Archived links still
    redirect — archiving only hides them from the dashboard, since you
    can't recall a URL once you've pasted it into a published post."""
    link = attribution_store.get_link_by_code(code)
    if link is None:
        abort(404)

    attribution_tracker.record_click(link["id"], request)
    dest = attribution_shortener.build_destination_url(link)
    return redirect(dest, code=302)


@bp.route("/attribution")
def dashboard():
    return render_template(
        "attribution/dashboard.html",
        summary=attribution_analytics.summary(),
        by_source=attribution_analytics.clicks_by_source(),
        links=attribution_analytics.list_links(),
        default_destination=ATTRIBUTION_DEFAULT_DESTINATION,
    )


@bp.route("/attribution/new", methods=["GET", "POST"])
def new_form():
    """Render the create-link form. POST = prefilled from a calling page
    (observation detail, content-gen result); GET = empty form. Submission
    goes to /attribution/create — POST here doesn't create anything."""
    source = request.form if request.method == "POST" else request.args
    defaults = {
        "destination":    (source.get("destination") or "").strip() or ATTRIBUTION_DEFAULT_DESTINATION,
        "utm_source":     (source.get("utm_source") or "").strip(),
        "utm_medium":     (source.get("utm_medium") or "").strip(),
        "utm_campaign":   (source.get("utm_campaign") or "").strip(),
        "utm_content":    (source.get("utm_content") or "").strip(),
        "utm_term":       (source.get("utm_term") or "").strip(),
        "observation_id": (source.get("observation_id") or "").strip(),
        "post_text":      source.get("post_text") or "",
        "notes":          source.get("notes") or "",
    }
    return render_template(
        "attribution/new.html",
        defaults=defaults,
        errors={},
        source_options=ATTRIBUTION_SOURCE_OPTIONS,
        medium_options=ATTRIBUTION_MEDIUM_OPTIONS,
        default_destination=ATTRIBUTION_DEFAULT_DESTINATION,
    )


@bp.route("/attribution/create", methods=["POST"])
def create():
    errors = {}
    destination = (request.form.get("destination") or "").strip()
    if not destination:
        errors["destination"] = "Destination URL is required."
    elif not (destination.startswith("http://") or destination.startswith("https://")):
        errors["destination"] = "Destination must start with http:// or https://."

    obs_raw = (request.form.get("observation_id") or "").strip()
    observation_id = None
    if obs_raw:
        try:
            observation_id = int(obs_raw)
        except ValueError:
            errors["observation_id"] = "Observation id must be a number (or blank)."

    if errors:
        return render_template(
            "attribution/new.html",
            defaults=request.form,
            errors=errors,
            source_options=ATTRIBUTION_SOURCE_OPTIONS,
            medium_options=ATTRIBUTION_MEDIUM_OPTIONS,
            default_destination=ATTRIBUTION_DEFAULT_DESTINATION,
        ), 400

    # Generate-then-insert with a defensive retry. Two layers exist for
    # different races: shortener.generate_code SELECT-checks for uniqueness
    # (same-session collisions), and the INSERT can still hit the UNIQUE
    # constraint against a concurrent writer (a 23505 SupabaseError below).
    # Three attempts is plenty given collision odds are ~1 in 57 billion
    # per code. created_at is DB-defaulted now — not sent.
    row_values = {
        "destination":    destination,
        "utm_source":     (request.form.get("utm_source") or "").strip() or None,
        "utm_medium":     (request.form.get("utm_medium") or "").strip() or None,
        "utm_campaign":   (request.form.get("utm_campaign") or "").strip() or None,
        "utm_content":    (request.form.get("utm_content") or "").strip() or None,
        "utm_term":       (request.form.get("utm_term") or "").strip() or None,
        "observation_id": observation_id,
        "post_text":      (request.form.get("post_text") or "").strip() or None,
        "notes":          (request.form.get("notes") or "").strip() or None,
    }

    inserted = None
    for _ in range(3):
        try:
            code = attribution_shortener.generate_code()
        except attribution_shortener.ShortCodeCollisionError:
            break
        try:
            inserted = attribution_store.insert_link({"code": code, **row_values})
            break
        except SupabaseError as e:
            if "23505" not in str(e):  # unique-code race is the only retryable
                raise
            continue

    if inserted is None:
        return render_template(
            "attribution/new.html",
            defaults=request.form,
            errors={"destination": "Couldn't generate a unique short code. Please try submitting again."},
            source_options=ATTRIBUTION_SOURCE_OPTIONS,
            medium_options=ATTRIBUTION_MEDIUM_OPTIONS,
            default_destination=ATTRIBUTION_DEFAULT_DESTINATION,
        ), 500

    return redirect(url_for("attribution.detail", link_id=inserted["id"], just_created=1))


@bp.route("/attribution/<int:link_id>")
def detail(link_id):
    link = attribution_store.get_link(link_id)
    if link is None:
        abort(404)

    return render_template(
        "attribution/detail.html",
        link=link,
        clicks=attribution_analytics.link_clicks(link_id),
        short_url=attribution_shortener.build_short_url(ATTRIBUTION_BASE_URL, link["code"]),
        resolved_destination=attribution_shortener.build_destination_url(link),
        just_created=bool(request.args.get("just_created")),
    )


@bp.route("/attribution/<int:link_id>/archive", methods=["POST"])
def archive(link_id):
    """Toggle the archived flag. Unarchive is signaled with a hidden form
    field rather than a separate route — the same button slot in the
    detail template flips behavior based on the link's current state."""
    new_state = not request.form.get("unarchive")
    patched = attribution_store.set_archived(link_id, new_state)
    if not patched:
        abort(404)
    return redirect(url_for("attribution.detail", link_id=link_id))
