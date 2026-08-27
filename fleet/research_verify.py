"""Adversarial verification of the research run's promoted claims.

The 2026-08-16 run framed Tomo as "on a direct collision course" with any
beginner-education product; Tomo's own homepage says it is "Duolingo for
Everything" (a general AI course generator, ~500 learners). The sources were
real. The FRAME was wrong, and the researcher never consulted the primary
surface. This step is the structural fix (founder-approved shape 2026-08-17):
a fresh-context verifier takes ONLY the decision-carrying claims â€” the pull's
headline and competitor_note â€” fetches the named company's own web surface,
and tries to REFUTE each claim. Corrections fold back into the pull before the
run persists.

Scope is deliberate: verifying all ~20 digest bullets is cost without payoff;
the promoted claims are what the founder decides from. Extension path: scan
digest bullets for superlatives ("clearest yet", "fastest", "collision
course") and verify those the same way.

Why a separate agent and not a same-agent re-read: the researcher shares the
context and priors that produced the frame, so it re-approves its own story.
Cold context + the primary source is what catches a frame error. The model is
config (app.RESEARCH_VERIFY_MODEL) through fleet/llm.py like every other call,
so it can diverge from the researcher's model by changing one env var.

Budget: at most 1 extraction call + one verify call per claim (2 claims), all
metered by llm.py's per-run breaker. Fetching is stdlib urllib; every failure
degrades to an "unverifiable" note, never a crashed run.
"""

import json
import re
import urllib.error
import urllib.request
from html.parser import HTMLParser

import app
from fleet import llm
from fleet.verify_prompt import (
    CLAIM_VERIFY_SYSTEM_PROMPT,
    TARGET_EXTRACTION_SYSTEM_PROMPT,
)

# The pull fields worth the cost: the ones the founder decides from and the
# ones that carry claims about external companies.
PROMOTED_FIELDS = ("headline", "competitor_note")

FETCH_TIMEOUT_S = 20
FETCH_MAX_BYTES = 400_000   # read cap: a homepage, not an archive
PAGE_TEXT_MAX = 8_000       # chars of stripped text handed to the verifier
USER_AGENT = "Mozilla/5.0 (compatible; FibopranaResearchVerify/1.0)"


class _TextExtractor(HTMLParser):
    """Strip a page to its visible text: everything outside script/style."""

    SKIP = {"script", "style", "noscript", "template", "svg"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if not self._skip_depth and data.strip():
            self.parts.append(data.strip())


def _fetch_page_text(url):
    """(text, error) â€” the page's visible text, whitespace-collapsed and
    capped, or (None, why)."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_S) as resp:
            raw = resp.read(FETCH_MAX_BYTES)
    except (urllib.error.URLError, OSError, ValueError) as e:
        return None, f"fetch failed: {e}"
    parser = _TextExtractor()
    try:
        parser.feed(raw.decode("utf-8", "replace"))
    except Exception as e:  # noqa: BLE001 â€” malformed HTML must not kill the run
        return None, f"parse failed: {e}"
    text = re.sub(r"\s+", " ", " ".join(parser.parts)).strip()
    if not text:
        return None, "page yielded no text"
    return text[:PAGE_TEXT_MAX], None


def _fetch_page_text_exa(url):
    """(text, error) â€” the Exa-contents fallback for pages that block direct
    fetches (SEC.gov 403'd the stdlib fetch, week of 2026-08-24; government
    and bank sites do this routinely). Exa's own fetcher gets past the bot
    walls, same as the facts agent's searches. ~1 credit per call, and it only
    runs when the free stdlib fetch already failed."""
    import os
    key = (os.environ.get("EXA_API_KEY") or "").strip()
    if not key:
        return None, "no EXA_API_KEY for the fallback fetch"
    try:
        from exa_py import Exa
        resp = Exa(api_key=key).get_contents([url], text=True)
        results = getattr(resp, "results", None) or []
        text = (getattr(results[0], "text", "") or "").strip() if results else ""
    except Exception as e:  # noqa: BLE001 â€” a fallback must never kill the run
        return None, f"Exa fetch failed: {e}"
    if not text:
        return None, "Exa returned no text for the page"
    return re.sub(r"\s+", " ", text)[:PAGE_TEXT_MAX], None


def _parse_json(text):
    """The model's JSON reply, tolerating fences/prose around the outermost
    JSON value. Returns the parsed value or None."""
    if not text:
        return None
    for opener, closer in (("[", "]"), ("{", "}")):
        start, end = text.find(opener), text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except ValueError:
                continue
    return None


def _extract_targets(claims, digest_md):
    """One model call: which company + owned URL can check each claim.
    Returns a list of {field, company, url} (possibly empty)."""
    # The digest's Sources section is where owned domains usually already
    # appear (e.g. heytomo.app); hand the tail of the digest as the link list.
    sources = digest_md[digest_md.rfind("### Sources"):] if "### Sources" in digest_md else ""
    claims_block = "\n".join(f'- field "{field}": {text}' for field, text in claims)
    user = ("CLAIMS (UNTRUSTED DATA):\n"
            f"<<<CLAIMS\n{claims_block}\nCLAIMS>>>\n\n"
            "SOURCE LINKS (UNTRUSTED DATA):\n"
            f"<<<SOURCES\n{sources}\nSOURCES>>>")
    result, err = llm.complete(model=app.RESEARCH_VERIFY_MODEL,
                               system=TARGET_EXTRACTION_SYSTEM_PROMPT,
                               user=user, max_tokens=500, temperature=0.0)
    if err:
        return [], err
    targets = _parse_json(result.text)
    if not isinstance(targets, list):
        return [], f"could not parse targets from: {result.text[:200]}"
    fields = {f for f, _ in claims}
    return [t for t in targets
            if isinstance(t, dict) and t.get("field") in fields
            and t.get("url") and str(t["url"]).startswith("http")], None


def _verify_claim(field, claim, company, page_text):
    """One adversarial model call. Returns {verdict, corrected_text, note}."""
    user = (f'CLAIM (field "{field}", about {company}):\n'
            f"<<<CLAIM\n{claim}\nCLAIM>>>\n\n"
            f"THE COMPANY'S OWN PAGE TEXT (UNTRUSTED DATA):\n"
            f"<<<PAGE\n{page_text}\nPAGE>>>")
    result, err = llm.complete(model=app.RESEARCH_VERIFY_MODEL,
                               system=CLAIM_VERIFY_SYSTEM_PROMPT,
                               user=user, max_tokens=700, temperature=0.0)
    if err:
        return {"verdict": "unverifiable", "corrected_text": None,
                "note": f"verify call failed: {err}"}
    verdict = _parse_json(result.text)
    if not isinstance(verdict, dict) or verdict.get("verdict") not in (
            "confirmed", "adjust", "refuted", "unverifiable"):
        return {"verdict": "unverifiable", "corrected_text": None,
                "note": f"could not parse verdict from: {result.text[:150]}"}
    return verdict


def verify_pull(pull, digest_md):
    """The step: check the pull's promoted claims against primary surfaces.

    Returns (pull, report). The pull comes back with adjusted/refuted claims
    rewritten in place; report is a list of {field, company, url, verdict,
    note} â€” one entry per claim examined, including the unverifiable ones, so
    the run's record shows what was and wasn't checked. Never raises: any
    failure shows up as an "unverifiable" report row instead.
    """
    claims = [(f, pull.get(f)) for f in PROMOTED_FIELDS if pull.get(f)]
    if not claims:
        return pull, []

    targets, err = _extract_targets(claims, digest_md)
    report = []
    if err:
        report.append({"field": "_extraction", "company": None, "url": None,
                       "verdict": "unverifiable", "note": err})

    by_field = {t["field"]: t for t in targets}
    for field, claim in claims:
        target = by_field.get(field)
        if not target:
            report.append({"field": field, "company": None, "url": None,
                           "verdict": "unverifiable",
                           "note": "no checkable company/URL identified"})
            continue
        page_text, fetch_err = _fetch_page_text(target["url"])
        if fetch_err:
            # Blind-spot fix (founder-directed 2026-08-24): bot-walled sites
            # made real claims report "unverifiable" â€” retry through Exa.
            page_text, exa_err = _fetch_page_text_exa(target["url"])
            fetch_err = None if page_text else f"{fetch_err}; {exa_err}"
        if fetch_err:
            report.append({"field": field, "company": target.get("company"),
                           "url": target["url"], "verdict": "unverifiable",
                           "note": fetch_err})
            continue
        verdict = _verify_claim(field, claim, target.get("company"), page_text)
        corrected = (verdict.get("corrected_text") or "").strip()
        if verdict["verdict"] in ("adjust", "refuted") and corrected:
            pull[field] = corrected
        report.append({"field": field, "company": target.get("company"),
                       "url": target["url"], "verdict": verdict["verdict"],
                       "note": verdict.get("note")})
    return pull, report


def format_report(report):
    """One readable line per checked claim, for reasoning/chat."""
    if not report:
        return None
    return "; ".join(
        f"{r['field']}: {r['verdict']}"
        + (f" vs {r['url']}" if r.get("url") else "")
        + (f" ({r['note']})" if r.get("note") else "")
        for r in report)
