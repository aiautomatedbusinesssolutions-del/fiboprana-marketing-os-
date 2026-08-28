"""Fleet surfaces mounted inside the main Flask dashboard (app.py).

Same pages and same API as the standalone door (`python -m fleet.dashboard`,
port 8765), so the agent workflow lives at the one address the founder
already uses: http://127.0.0.1:5000. All endpoint logic is the shared-action
functions in fleet/dashboard.py; this file is Flask plumbing only.

One path difference: the fleet ledger page (fleet/dashboard.html) is served
at /fleet here, because / already belongs to the modules dashboard. Every
other path (/week, /review, /map, /kanban, /flow, /aids/*, /api/*) is
identical on both doors, which is why the HTML files need no edits.
"""

import json
import urllib.parse

from flask import Blueprint, Response, abort, request

from fleet import dashboard as fleet_dash
from fleet.supabase import SupabaseError

bp = Blueprint("fleet", __name__)

# The off-page drain runner must live wherever the server lives. The
# standalone door starts it in its own main(); the Flask door (app.py, the
# one address) starts it here at mount time. Daemon thread, self-guarded.
fleet_dash.start_drain_runner()

# Page name -> HTML file. Keys double as the URL path.
PAGES = {
    "fleet": fleet_dash.PAGE,
    "week": fleet_dash.WEEK,
    "kanban": fleet_dash.KANBAN,
    "flow": fleet_dash.FLOW,
    "review": fleet_dash.REVIEW,
    "map": fleet_dash.MAP,
    "finders": fleet_dash.FINDERS,
    "pulse": fleet_dash.PULSE,
    "convert": fleet_dash.CONVERT,
}


def _json(data, code=200):
    # json.dumps with default=str (not jsonify) to match the standalone door's
    # serialization of dates and UUIDs coming back from the stores.
    return Response(json.dumps(data, default=str), status=code,
                    mimetype="application/json")


@bp.after_request
def _no_store(resp):
    resp.headers["Cache-Control"] = "no-store"  # always live data
    return resp


@bp.get("/<any(fleet, week, kanban, flow, review, map, finders, pulse, convert):name>")
def fleet_page(name):
    return Response(PAGES[name].read_bytes(), mimetype="text/html")


@bp.get("/vendor/mermaid.min.js")
def vendor_mermaid():
    if not fleet_dash.MERMAID.is_file():
        abort(404)
    return Response(fleet_dash.MERMAID.read_bytes(),
                    mimetype="application/javascript")


@bp.get("/aids/<name>")
def aid_deck(name):
    # Same whitelist regex as the standalone door: no traversal, .html only.
    if not fleet_dash.AID_RE.match(f"/aids/{name}"):
        abort(404)
    deck = fleet_dash.AIDS_DIR / name
    if not deck.is_file():
        abort(404)
    return Response(deck.read_bytes(), mimetype="text/html")


@bp.get("/api/narration")
def api_narration():
    # Binary route, registered before the generic /api handler resolves it:
    # Flask prefers the static rule, so this wins over /api/<path:name>.
    try:
        _name, data = fleet_dash.narration_audio(
            request.args.get("week", ""), request.args.get("video", ""))
    except (ValueError, LookupError) as e:
        return _json({"error": str(e)}, 404)
    return Response(data, mimetype="audio/mpeg")


@bp.get("/api/stills")
def api_stills():
    try:
        return _json(fleet_dash.stills_listing(
            request.args.get("week", ""), request.args.get("video", "")))
    except (ValueError, LookupError) as e:
        return _json({"error": str(e)}, 404)


@bp.get("/api/still")
def api_still():
    try:
        data = fleet_dash.still_image(
            request.args.get("week", ""), request.args.get("video", ""),
            request.args.get("file", ""))
    except (ValueError, LookupError) as e:
        return _json({"error": str(e)}, 404)
    return Response(data, mimetype="image/png")


@bp.get("/api/<path:name>")
def api_get(name):
    if name in fleet_dash.STATE_FILES:
        return _json(fleet_dash.read_state(fleet_dash.STATE_FILES[name]))
    if name in fleet_dash.GETTERS:
        return _json(fleet_dash.GETTERS[name]())
    # parse_qsl on the raw query string, same as the standalone door, so
    # PostgREST params pass through byte-identical.
    params = dict(urllib.parse.parse_qsl(request.query_string.decode("utf-8")))
    try:
        return _json(fleet_dash.api_select(name, params))
    except LookupError:
        return _json({"error": "unknown table"}, 404)
    except SupabaseError as e:
        return _json({"error": str(e)}, 502)


@bp.post("/api/<path:name>")
def api_post(name):
    action = fleet_dash.POST_ACTIONS.get(name)
    if action is None:
        abort(404)
    try:
        body = request.get_json(force=True)
        return _json(action(body))
    except (ValueError, KeyError) as e:
        return _json({"error": str(e)}, 400)
    except SupabaseError as e:
        return _json({"error": str(e)}, 502)
