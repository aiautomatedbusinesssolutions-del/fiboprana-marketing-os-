"""Generate the video's post-script artifacts: deck, thumbnail prompts, package.

    python -m content.video_artifacts_run                  # news video, all parts
    python -m content.video_artifacts_run --video feature
    python -m content.video_artifacts_run --part thumbs    # one part only

The script-done fan-out (founder call 2026-08-11): the moment a video's script
card is marked done on /week, fleet/dashboard.py spawns this module and every
artifact that depends only on the approved script + verified facts generates in
one batch — deck, thumbnail prompts, package (titles / description / tags /
community post + image prompt), and the long-form X post draft. Parts run
sequentially IN ONE PROCESS on purpose: they all write the same week slot in
fleet/video_ideas.json, and one writer can't race itself. Each part persists as
it finishes, so the cards fill progressively.

Idempotent: a part whose slot key already has content is skipped (regenerating
after feedback stays a deliberate, in-session act). Founder feedback lists are
preserved on regeneration. Every part validates before saving — wrong shape,
em dashes, or missing required blocks refuse to save rather than land broken —
and a refused attempt retries with the reason fed back (RETRY_ATTEMPTS total)
before reporting FAIL, so one bad generation can't strand a card
on a card.
"""

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

from fleet import llm  # noqa: E402
from content.video_artifact_prompts import (  # noqa: E402
    DECK_SYSTEM_PROMPT, THUMBS_SYSTEM_PROMPT, PKG_SYSTEM_PROMPT)

MODEL = llm.model_for("video_artifacts", "claude-sonnet-4-6")
VIDEO_IDEAS = ROOT / "fleet" / "video_ideas.json"
AIDS_DIR = ROOT / "videos" / "aids"
EM_DASH = "—"
# The style ledger, inlined (see memory/thumbnail notes). Founder rule
# 2026-08-22: style a = the researched house look; styles b and c = two NEW
# visual directions per video, for CTR testing on the young channel. Append
# each week's tried directions below so the generator rotates instead of
# repeating (content lessons like money cue / problem state stay binding for
# every style; only the visual language varies).
STYLE_LEDGER_NOTE = ("Style ledger: house style (style a) = dark 0b0f14, emerald/amber, "
                     "white caps headline, cinematic-photoreal or editorial-3D scene; "
                     "CTR data still pending. Experiment directions already tried in "
                     "recent videos: none logged yet, all directions open.")


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _monday():
    return str(date.today() - timedelta(days=date.today().weekday()))


def read_state():
    return json.loads(VIDEO_IDEAS.read_text(encoding="utf-8"))


def write_state(state):
    tmp = VIDEO_IDEAS.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(VIDEO_IDEAS)
    try:  # off-PC drain mirror - best-effort, never blocks a save
        from fleet import fleet_state
        fleet_state.push("video_ideas", state)
    except Exception:  # noqa: BLE001
        pass


def load_inputs(week, video):
    """(slot, idea, script_md, facts_md, slug) for the picked idea, or an error
    string. The slug (used for filenames) comes from the script's own file name
    so deck and script stay siblings."""
    state = read_state()
    slot = state.get(week, {}).get(video)
    if not slot:
        return None, f"no {video} slot for week {week}"
    script = (slot.get("script") or {}).get("script_md")
    if not script:
        return None, "needs an approved script first"
    picked = slot.get("picked")
    idea = next((i for i in slot.get("ideas", []) if i["id"] == picked), None)
    if idea is None:
        # The feature and ascent lanes carry no idea list (their pick happens
        # at the build gate / in the journal), so the script IS the identity:
        # its H1 is the title (found 2026-08-13, when the feature fan-out
        # refused to run for want of a picked idea).
        m = re.search(r"^#\s+(.+)$", script, re.MULTILINE)
        if not m:
            return None, "needs a picked idea, or a script whose H1 is the title"
        idea = {"id": video, "title": m.group(1).strip(),
                "angle": f"the week's {video} video, generated from the approved script below"}
    facts = (slot.get("facts") or {}).get("report_md") or "(no facts report on the card)"
    slug = Path((slot.get("script") or {}).get("file") or f"{picked or video}-{week}").stem
    return (state, slot, idea, script, facts, slug), None


def _user_message(idea, script_md, facts_md, extra=""):
    parts = [f"THE PICKED IDEA:\ntitle: {idea['title']}\nangle: {idea['angle']}"]
    if extra:
        parts.append(extra)
    parts.append(f"VERIFIED FACTS REPORT:\n<<<FACTS\n{facts_md}\nFACTS>>>")
    parts.append(f"APPROVED SCRIPT:\n<<<SCRIPT\n{script_md}\nSCRIPT>>>")
    return "\n\n".join(parts)


def _no_em_dash(*texts):
    return not any(EM_DASH in (t or "") for t in texts)


def _strip_em_dashes(text):
    """Mechanical fix for the one refusal that never needs the model's
    judgment (founder call 2026-08-17, after an em dash cost a retry): in a
    thumbnail prompt the em dash only matters because the HEADLINE inside it
    gets rendered into the published image, and a comma or hyphen serves
    identically, so code fixes it for free. Deck and pkg copy keep
    refuse-and-retry - there the em dash sits in voice-bearing published
    prose the model should rewrite itself."""
    return (text or "").replace(f" {EM_DASH} ", ", ").replace(EM_DASH, "-")


def _parse_json(text):
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except ValueError:
        return None


RETRY_ATTEMPTS = 3


def _generate_validated(system, user, *, max_tokens, temperature, parse,
                        model=None):
    """The generate -> parse -> validate loop, retrying with the validator's
    refusal fed back (up to RETRY_ATTEMPTS total). A validator exists to
    refuse bad saves; without the retry, one refusal silently stranded the
    card until a human noticed (thumbs: face-space wk 2026-08-10, em dash wk
    2026-08-17 — founder directive: never again). parse takes the raw reply
    and returns (data, error); error None means save it. Returns (data, None)
    on success or (None, final_error) after the attempts run out. Transport /
    budget-breaker errors return immediately — those aren't fixable by asking
    the model to try harder."""
    verr = None
    for _ in range(RETRY_ATTEMPTS):
        msg = user if verr is None else (
            user + "\n\nYOUR PREVIOUS ATTEMPT WAS REFUSED by the output "
            f"validator for this reason: {verr}\nProduce the complete output "
            "again with that corrected. Reminder: never use an em dash "
            "anywhere in the output.")
        result, lerr = llm.complete(model=model or MODEL, system=system, user=msg,
                                    max_tokens=max_tokens, temperature=temperature)
        if lerr:
            return None, lerr
        data, verr = parse(result.text)
        if verr is None:
            return data, None
    return None, f"validation, {RETRY_ATTEMPTS} attempts: {verr}"


# ── part: deck ───────────────────────────────────────────────────────────────
# The real brand lockup (founder rule 2026-08-24: the actual logo, never a
# text-glyph placeholder). The PNGs are the site header's own assets (export
# them from the sibling repo's wordmark). They ride the saved deck as data
# URIs so the storyboard file stays self-contained - the model never sees the base64:
# strip_brand() collapses the template's brand div to a short placeholder
# before prompting, inject_brand() puts the real markup back at save time.
BRAND_DIR = Path(__file__).resolve().parent.parent / "videos" / "templates" / "brand"
BRAND_RE = re.compile(r'<div class="brand">.*?</div>', re.S)
BRAND_PLACEHOLDER = '<div class="brand"><!-- real logo injected at save --></div>'


def _brand_markup():
    import base64
    try:
        # TODO: drop Fiboprana's mark/wordmark PNGs into videos/templates/brand/
        # (the wordmark component lives in the sibling repo's src/components).
        mark = base64.b64encode((BRAND_DIR / "fiboprana-mark.png").read_bytes()).decode()
        word = base64.b64encode((BRAND_DIR / "fiboprana-wordmark.png").read_bytes()).decode()
    except OSError:
        return '<div class="brand">Fiboprana</div>'  # assets missing - plain text fallback
    # Sizes are INLINE on the imgs, not in the deck's stylesheet: a deck
    # templates from its own lane's previous week, so injected markup can never
    # assume the template carries matching CSS rules (2026-08-24: the feature
    # deck templated from a pre-logo week and rendered the mark at 256px).
    return ('<div class="brand" style="display:flex;align-items:center;gap:.55em">'
            '<img class="brandmark" alt="" '
            'style="display:block;width:auto;height:clamp(1.5rem,2.6vw,2.1rem)" '
            f'src="data:image/png;base64,{mark}">'
            '<img class="brandword" alt="Fiboprana" '
            'style="display:block;width:auto;height:clamp(1rem,1.75vw,1.4rem)" '
            f'src="data:image/png;base64,{word}">'
            '</div>')


def strip_brand(html):
    return BRAND_RE.sub(BRAND_PLACEHOLDER, html, count=1)


def inject_brand(html):
    return BRAND_RE.sub(lambda _: _brand_markup(), html, count=1)


# The never-clip guarantee, injected at save time like the brand mark - decks
# template from their own lane's previous week, so a fix that lives only in
# one deck's markup doesn't reach the other lanes (2026-08-24: the feature
# deck missed the auto-fit for exactly this reason). Self-contained and
# idempotent: it skips wrapping if the template's own script already wrapped
# (the news lane carries an inline copy), and re-fitting is a no-op.
FIT_MARK = "<!-- autofit (injected at save) -->"
FIT_RE = re.compile(re.escape(FIT_MARK) + r".*?" + re.escape(FIT_MARK), re.S)
FIT_SNIPPET = FIT_MARK + """
<style>.slide .fit{display:flex;flex-direction:column;justify-content:center;transform-origin:left center;flex:none}</style>
<script>
(() => {
  const slides = [...document.querySelectorAll('.slide')];
  slides.forEach((s) => {
    if (s.querySelector(':scope > .fit')) return;
    const w = document.createElement('div');
    w.className = 'fit';
    while (s.firstChild) w.appendChild(s.firstChild);
    s.appendChild(w);
  });
  function fit(s){
    if (!s) return;
    const w = s.querySelector(':scope > .fit');
    if (!w) return;
    w.style.transform = '';
    const cs = getComputedStyle(s);
    const availH = s.clientHeight - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom);
    const availW = s.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
    const f = Math.min(1, availH / w.offsetHeight, availW / w.offsetWidth);
    if (f < 1) w.style.transform = 'scale(' + f + ')';
  }
  const fitActive = () => fit(document.querySelector('.slide.active'));
  // Navigation happens via the template's own keydown/click handlers; re-fit
  // right after they run. Cheap, idempotent, no coupling to the nav script.
  document.addEventListener('keydown', () => setTimeout(fitActive, 0));
  document.addEventListener('click', () => setTimeout(fitActive, 0));
  window.addEventListener('resize', fitActive);
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(fitActive);
  fitActive();
})();
</script>
""" + FIT_MARK


def strip_fit(html):
    return FIT_RE.sub("", html)


def inject_fit(html):
    html = strip_fit(html)  # never stack two copies via template carryover
    if "</body>" in html:
        return html.replace("</body>", FIT_SNIPPET + "\n</body>", 1)
    return html + FIT_SNIPPET


def template_deck_html(week, video, slug):
    """Last week's registered deck for this video (structure donor), falling
    back to the newest deck in videos/aids that isn't this week's own."""
    state = read_state()
    for wk in sorted(state.keys(), reverse=True):
        if wk >= week:
            continue
        f = ((state[wk].get(video) or {}).get("deck") or {}).get("file")
        if f and (ROOT / f).exists():
            return (ROOT / f).read_text(encoding="utf-8")
    decks = sorted(AIDS_DIR.glob("*.html"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in decks:
        if p.stem != slug:
            return p.read_text(encoding="utf-8")
    return None


def validate_deck(html):
    if not html.lstrip().lower().startswith("<!doctype"):
        return "not a full HTML document"
    if html.count('<section class="slide"') < 6:
        return "fewer than 6 slides"
    for needed in ('id="dots"', "ArrowRight", 'class="brand"'):
        if needed not in html:
            return f"template structure missing ({needed})"
    body = html.split("<body>", 1)[-1]
    if EM_DASH in body:
        return "em dash in visible copy"
    return None


def run_deck(week, video):
    loaded, err = load_inputs(week, video)
    if err:
        return f"deck: SKIP ({err})"
    state, slot, idea, script, facts, slug = loaded
    if (slot.get("deck") or {}).get("file"):
        return "deck: SKIP (already built; regenerate in session after feedback)"
    template = template_deck_html(week, video, slug)
    if not template:
        return "deck: FAIL (no template deck found in videos/aids)"
    template = strip_fit(strip_brand(template))
    def parse(text):
        html = text.strip()
        if html.startswith("```"):
            html = re.sub(r"^```[a-z]*\n|\n```$", "", html)
        verr = validate_deck(html)
        return (html, None) if verr is None else (None, verr)

    html, gerr = _generate_validated(
        DECK_SYSTEM_PROMPT,
        _user_message(idea, script, facts,
                      f"LAST WEEK'S DECK (the exact structural template):\n"
                      f"<<<TEMPLATE\n{template}\nTEMPLATE>>>"),
        max_tokens=9000, temperature=0.4, parse=parse)
    if gerr:
        return f"deck: FAIL ({gerr})"
    out = AIDS_DIR / f"{slug}.html"
    out.write_text(inject_fit(inject_brand(html)), encoding="utf-8")
    slot["deck"] = {
        "status": "draft", "built_at": _now(), "built_by": "content.video_artifacts_run",
        "file": f"videos/aids/{slug}.html", "url": f"/aids/{slug}.html",
        "feedback": (slot.get("deck") or {}).get("feedback", []),
    }
    write_state(state)
    return f"deck: ok ({out.name})"


# ── part: thumbnail prompts ──────────────────────────────────────────────────
def validate_thumbs(data):
    prompts = data.get("prompts")
    if not (isinstance(prompts, list) and len(prompts) == 6):
        return "need exactly 6 prompts"
    if [p.get("id") for p in prompts] != ["a1", "a2", "b1", "b2", "c1", "c2"]:
        return "ids must be a1,a2,b1,b2,c1,c2 in order"
    for p in prompts:
        t = p.get("text") or ""
        if "1280x720" not in t:
            return f"{p['id']}: missing 1280x720"
        if "FULL WIDTH" not in t.upper():
            return f"{p['id']}: headline full-width rule missing"
        if p["id"].endswith("1") and "lower left" not in t.lower().replace("-", " "):
            return f"{p['id']}: face-space variant must reserve the lower left"
    if not _no_em_dash(data.get("note"), *[p.get("text") for p in prompts],
                       *[p.get("style") for p in prompts]):
        return "em dash in output"
    return None


def run_thumbs(week, video):
    loaded, err = load_inputs(week, video)
    if err:
        return f"thumbs: SKIP ({err})"
    state, slot, idea, script, facts, slug = loaded
    if (slot.get("thumbs") or {}).get("prompts"):
        return "thumbs: SKIP (already written; regenerate in session after feedback)"
    def parse(text):
        data = _parse_json(text)
        if data is None:
            return None, "no JSON in reply"
        # Sanitize before validating: the em-dash check stays in the
        # validator as a backstop, but code fixes it so it never costs a retry.
        data["note"] = _strip_em_dashes(data.get("note"))
        for p in data.get("prompts") or []:
            if isinstance(p, dict):
                p["text"] = _strip_em_dashes(p.get("text"))
                p["style"] = _strip_em_dashes(p.get("style"))
        verr = validate_thumbs(data)
        return (data, None) if verr is None else (None, verr)

    data, gerr = _generate_validated(
        THUMBS_SYSTEM_PROMPT,
        _user_message(idea, script, facts, STYLE_LEDGER_NOTE),
        max_tokens=5000, temperature=0.7, parse=parse)
    if gerr:
        return f"thumbs: FAIL ({gerr})"
    slot["thumbs"] = {
        "status": "prompts_ready", "written_at": _now(),
        "written_by": "content.video_artifacts_run",
        "note": data.get("note") or "",
        "prompts": data["prompts"],
        "feedback": (slot.get("thumbs") or {}).get("feedback", []),
    }
    write_state(state)
    return "thumbs: ok (6 prompts)"


# ── part: package (+ the long-form X post draft) ─────────────────────────────
def validate_pkg(data):
    titles = data.get("title_options")
    if not (isinstance(titles, list) and len(titles) == 3):
        return "need exactly 3 title options"
    desc = data.get("description") or ""
    for needed in ("About Fiboprana", "not medical advice", "#Fiboprana",
                   "https://fiboprana.com"):
        if needed not in desc:
            return f"description missing required block ({needed})"
    for field in ("tags", "community_post", "community_image_prompt", "xpost_text"):
        if not (data.get(field) or "").strip():
            return f"missing field: {field}"
    if "http" in data["community_post"] or "http" in data["xpost_text"]:
        return "community post / X post must carry no links (link rides separately)"
    all_text = [*titles, desc, data["tags"], data["community_post"],
                data["community_image_prompt"], data["xpost_text"]]
    if not _no_em_dash(*all_text):
        return "em dash in output"
    return None


def run_pkg(week, video):
    loaded, err = load_inputs(week, video)
    if err:
        return f"pkg: SKIP ({err})"
    state, slot, idea, script, facts, slug = loaded
    if (slot.get("pkg") or {}).get("description"):
        return "pkg: SKIP (already written; regenerate in session after feedback)"
    def parse(text):
        data = _parse_json(text)
        if data is None:
            return None, "no JSON in reply"
        verr = validate_pkg(data)
        return (data, None) if verr is None else (None, verr)

    data, gerr = _generate_validated(
        PKG_SYSTEM_PROMPT,
        _user_message(idea, script, facts),
        max_tokens=5000, temperature=0.5, parse=parse)
    if gerr:
        return f"pkg: FAIL ({gerr})"
    # Tracked link: the description never ships a bare fiboprana.com URL
    # (youtube_description.md rule). Fail-open: a shortener hiccup keeps the
    # bare URL rather than losing the draft.
    desc = data["description"]
    try:
        from attribution import autolink
        desc = autolink.rewrite_urls(desc, source="youtube", medium="description",
                                     campaign=f"video-{slug}", content="about")
    except Exception as e:  # noqa: BLE001
        print(f"pkg: note — link tracking skipped ({e})", file=sys.stderr)
    slot["pkg"] = {
        "status": "draft", "written_at": _now(),
        "written_by": "content.video_artifacts_run",
        "title_options": data["title_options"], "description": desc,
        "tags": data["tags"], "community_post": data["community_post"],
        "community_image_prompt": data["community_image_prompt"],
        "thumb_note": "Video thumbnail (16:9): pick from the 6 prompts on the thumbnail card; "
                      "your photo lower-left in the face-space variants. The community post "
                      "uses its OWN square image (prompt in this package), never the video thumbnail.",
        "feedback": (slot.get("pkg") or {}).get("feedback", []),
    }
    if not (slot.get("xpost") or {}).get("text"):
        slot["xpost"] = {
            "text": data["xpost_text"],
            "link_reply": None,  # the video URL, added at schedule time
            "schedule_note": "Schedules for the video's go-live day once the YouTube link exists; "
                             "the link posts as a self-reply.",
            "status": "draft", "written_at": _now(),
            "written_by": "content.video_artifacts_run", "feedback": [],
        }
    write_state(state)
    return "pkg: ok (titles + description + tags + community post + X post draft)"


PARTS = {"deck": run_deck, "thumbs": run_thumbs, "pkg": run_pkg}


def main():
    parser = argparse.ArgumentParser(description="Generate post-script video artifacts.")
    parser.add_argument("--video", default="news", choices=["news", "feature", "ascent"])
    parser.add_argument("--week", default=None, help="the week's Monday (default: this week)")
    parser.add_argument("--part", default="all", choices=["all", "deck", "thumbs", "pkg"])
    args = parser.parse_args()
    week = args.week or _monday()
    parts = list(PARTS) if args.part == "all" else [args.part]
    failed = False
    for part in parts:
        msg = PARTS[part](week, args.video)
        print(msg)
        failed = failed or "FAIL" in msg
    # Clear the dashboard's in-flight marker so the cards stop saying
    # "generating" (set by spawn_video_artifacts; stale after 15 min anyway).
    state = read_state()
    slot = (state.get(week) or {}).get(args.video)
    if slot and slot.pop("artifacts_generating_since", None):
        write_state(state)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
