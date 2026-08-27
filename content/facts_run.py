"""The facts agent: verify the picked story against live sources (wire 9).

    python -m content.facts_run --week 2026-08-24
    python -m content.facts_run --dry-run        # plan + searches, no report

Built LAST on purpose (founder rule): every downstream artifact trusts this
report and the voice clone narrates its numbers verbatim. Trigger: the news topic
pick (news/topic done-click) spawns this; the report lands on the facts card
as status "draft" and the founder's pass on that card stays the gate that
fires the script agent (wire 8). Regenerating after feedback stays an
in-session act.

How it works:
  1. PLAN (LLM): extract load-bearing claims from the picked idea + digest,
     write 3-5 search queries.
  2. SEARCH: Exa search_and_contents per query (num_results<=10 = 1 credit
     each on the free tier), collecting each result's page text.
  3. VERIFY (LLM): write the house-format report (Verdict / Corrections /
     Confirmed / Not verified / Sources) from those sources ONLY.
  4. The sources-resolve validator refuses any report citing a URL that was
     not actually fetched - the model cannot invent a source. Verdict line,
     section shape, and the no-em-dash rule are enforced the same way.

Costs per run: 3-5 Exa credits + two LLM calls (plan ~2K, verify ~40K in).
"""

import argparse
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from fleet import llm  # noqa: E402
from content.facts_prompts import (  # noqa: E402
    FACTS_PLAN_SYSTEM_PROMPT, FACTS_VERIFY_SYSTEM_PROMPT)
from content.video_artifacts_run import (  # noqa: E402
    EM_DASH, _generate_validated, _now, read_state, write_state)

MODEL = llm.model_for("facts_verify", "claude-sonnet-4-6")
URL_RE = re.compile(r"https?://[^\s)\"'>\]]+")
MAX_SOURCE_CHARS = 5000
MAX_SOURCES = 14


def _monday():
    return str(date.today() - timedelta(days=date.today().weekday()))


def latest_digest_md():
    """This week's research digest, the story's origin. Empty string if the
    read fails - the plan still works from the idea alone."""
    try:
        from content.x_run import load_latest_digest
        row = load_latest_digest()
        return (row or {}).get("digest_md") or ""
    except Exception:  # noqa: BLE001 - digest is helpful, not required
        return ""


def plan_checks(idea, digest_md):
    """(claims, queries) via the PLAN call."""
    user = (f"THE PICKED IDEA:\ntitle: {idea.get('title')}\n"
            f"angle: {idea.get('angle')}\n\n"
            f"THE WEEK'S DIGEST (the story's origin, untrusted):\n"
            f"<<<DIGEST\n{digest_md[:15000]}\nDIGEST>>>")

    def parse(text):
        s, e = text.find("{"), text.rfind("}")
        if s == -1:
            return None, "no JSON in the reply"
        try:
            data = json.loads(text[s:e + 1])
        except ValueError as err:
            return None, f"bad JSON: {err}"
        claims, queries = data.get("claims"), data.get("queries")
        if not (isinstance(claims, list) and 3 <= len(claims) <= 10):
            return None, "need 4-8 claims"
        if not (isinstance(queries, list) and 2 <= len(queries) <= 6):
            return None, "need 3-5 queries"
        if EM_DASH in json.dumps(data):
            return None, "em dash in the output"
        return data, None

    return _generate_validated(FACTS_PLAN_SYSTEM_PROMPT, user,
                               max_tokens=1500, temperature=0.2,
                               parse=parse, model=MODEL)


def run_searches(queries, lookback_days=45):
    """Exa search per query; returns deduped [{url,title,published,text}]."""
    from exa_py import Exa
    import os

    exa = Exa(api_key=(os.environ.get("EXA_API_KEY") or "").strip())
    start = (date.today() - timedelta(days=lookback_days)).isoformat()
    sources, seen = [], set()
    for q in queries:
        try:
            resp = exa.search_and_contents(q, num_results=8, type="auto",
                                           start_published_date=start,
                                           text=True)
        except Exception as e:  # noqa: BLE001 - one bad query must not kill the run
            print(f"  (search failed for {q!r}: {e})", file=sys.stderr)
            continue
        for r in resp.results:
            url = (getattr(r, "url", "") or "").split("#")[0]
            text = (getattr(r, "text", "") or "").strip()
            if not url or url in seen or len(text) < 300:
                continue
            seen.add(url)
            sources.append({
                "url": url,
                "title": getattr(r, "title", "") or url,
                "published": (getattr(r, "published_date", "") or "")[:10],
                "text": text[:MAX_SOURCE_CHARS],
            })
    return sources[:MAX_SOURCES]


def validate_report(md, fetched_urls):
    md = (md or "").strip()
    if not md.startswith("**Verdict:"):
        return "must start with the **Verdict:** line"
    if EM_DASH in md:
        return "em dash in the report"
    if len(md) < 1500:
        return "too short to be a full facts report"
    for heading in ("### Confirmed facts", "### Sources"):
        if heading not in md:
            return f"missing section: {heading}"
    cited = {u.rstrip(".,;") for u in URL_RE.findall(md)}
    ghosts = {u for u in cited
              if not any(u.startswith(f.split("#")[0]) or f.startswith(u)
                         for f in fetched_urls)}
    if ghosts:
        return ("cites URLs that were not in the fetched sources (never "
                f"invent a source): {sorted(ghosts)[:3]}")
    if len(cited) < 3:
        return "must cite at least 3 of the fetched sources by URL"
    return None


def write_report(idea, digest_md, claims, sources):
    listing = "\n\n".join(
        f"SOURCE {i + 1}\ntitle: {s['title']}\nurl: {s['url']}\n"
        f"published: {s['published'] or 'unknown'}\ntext:\n{s['text']}"
        for i, s in enumerate(sources))
    user = (f"THE PICKED IDEA:\ntitle: {idea.get('title')}\n"
            f"angle: {idea.get('angle')}\n\n"
            f"THE DIGEST'S VERSION (untrusted, check it):\n"
            f"<<<DIGEST\n{digest_md[:8000]}\nDIGEST>>>\n\n"
            f"LOAD-BEARING CLAIMS TO CHECK:\n"
            + "\n".join(f"- {c}" for c in claims)
            + f"\n\nFETCHED SOURCES ({len(sources)}):\n{listing}")
    urls = [s["url"] for s in sources]

    def parse(text):
        md = text.strip()
        verr = validate_report(md, urls)
        return (md, None) if verr is None else (None, verr)

    return _generate_validated(FACTS_VERIFY_SYSTEM_PROMPT, user,
                               max_tokens=5000, temperature=0.2,
                               parse=parse, model=MODEL)


def run(week, dry_run=False):
    state = read_state()
    slot = state.get(week, {}).get("news")
    if not slot:
        return f"facts: SKIP (no news slot for week {week})"
    if (slot.get("facts") or {}).get("report_md"):
        return "facts: SKIP (report exists; regenerate in session after feedback)"
    picked = slot.get("picked")
    idea = next((i for i in slot.get("ideas", []) if i.get("id") == picked), None)
    if not idea:
        return "facts: SKIP (no picked idea yet)"

    digest_md = latest_digest_md()
    plan, gerr = plan_checks(idea, digest_md)
    if gerr:
        return f"facts: FAIL (plan: {gerr})"
    print(f"claims: {len(plan['claims'])}, queries: {len(plan['queries'])}")
    sources = run_searches(plan["queries"])
    if len(sources) < 3:
        return (f"facts: FAIL (only {len(sources)} usable sources fetched - "
                "check EXA_API_KEY / the queries; card stays manual)")
    print(f"sources fetched: {len(sources)}")
    if dry_run:
        for s in sources:
            print(f"  {s['published']} {s['url'][:90]}")
        return "facts: DRY RUN complete (no report written)"

    report, gerr = write_report(idea, digest_md, plan["claims"], sources)
    # re-read: other cards may have written while the model ran
    state = read_state()
    slot = state.get(week, {}).get("news") or {}
    slot.pop("facts_generating_since", None)
    if gerr:
        state.setdefault(week, {})["news"] = slot
        write_state(state)
        return f"facts: FAIL (verify: {gerr})"
    slot["facts"] = {
        "status": "draft",
        "checked_at": _now(),
        "checked_by": "content.facts_run",
        "report_md": report,
        "sources": [{"url": s["url"], "title": s["title"]} for s in sources],
    }
    state.setdefault(week, {})["news"] = slot
    write_state(state)
    verdict = report.splitlines()[0][:100]
    return f"facts: ok ({verdict}) - awaiting founder pass"


def main():
    parser = argparse.ArgumentParser(description="Verify the picked story's facts.")
    parser.add_argument("--week", default=None, help="Monday of the week (default: current)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    msg = run(args.week or _monday(), dry_run=args.dry_run)
    print(msg)
    return 1 if ": FAIL" in msg else 0


if __name__ == "__main__":
    raise SystemExit(main())
