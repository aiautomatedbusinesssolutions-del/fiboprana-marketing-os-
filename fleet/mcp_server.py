"""Fiboprana fleet — MCP server (stdio).

Exposes the fleet's agents to an MCP client as MCP tools over stdio. The MCP client
launches this as a subprocess — locally, or as a Docker container for isolation — and calls
the tools when you chat. Same brains as the cron entrypoints; these wrap the plain
run_*() functions ("two doors, one brain").

Research tools:
  run_research(scan=True)              -> run the weekly research; returns digest +
                                          content-pull markdown; persists the run.
  log_outcome(run_id, verdict, note)   -> record your verdict on a past run.

Reply tools (the finder/judge/drafter doors; the interactive review + SEND stay in
the CLI, `python -m fleet.reply_review` — automation never posts):
  run_reply_finder(channels, top_n)            -> scan + thin-judge new candidates
                                                  (channels="x" uses the Grok x_search
                                                  adapter once XAI_API_KEY is set).
  run_reply_drafter(candidate_id, ...)         -> 2-3 gated variants for 'engage'.
  reply_queue(platform, limit)                 -> what's waiting for review (read-only).
  reply_recent(limit, platform)                -> recent sent replies (the ledger).
  log_reply_outcome(ledger_id, ...)            -> record a sent reply's outcome.

Measure + learn tools (the Saturday jobs, callable any day from chat):
  run_outcome_checker(dry_run)                 -> read real engagement for sent replies
                                                  and write it to the ledger.
  run_distiller(dry_run)                       -> distill draft->final edits into
                                                  PROPOSED style rules (founder-gated).
  run_reply_eval(dry_run)                      -> blind pairwise eval: fresh drafts vs
                                                  gold replies; win-rate per guide.
  run_repurposer(clip_text, ...)               -> one clip -> platform-native post
                                                  packages (YT Short / X / IG / TikTok).

Run: python -m fleet.mcp_server
MCP client config.yaml points its `command` here and injects secrets via `env:`
(SUPABASE_URL, SUPABASE_ANON_KEY, ANTHROPIC_API_KEY, optional EXA_API_KEY).
"""

from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")  # local dev; in production your MCP client injects env directly

from mcp.server.fastmcp import FastMCP  # noqa: E402
from fleet import research_agent  # noqa: E402
from fleet import reply_finder_agent, reply_drafter_agent, reply_store  # noqa: E402
from fleet import (distiller_agent, outcome_checker_agent,  # noqa: E402
                   reply_eval_agent, repurposer_agent)

mcp = FastMCP("fiboprana-research")


@mcp.tool()
def run_research(scan: bool = True) -> str:
    """Run Fiboprana's weekly trend research. Scans HN/RSS/Exa (pass scan=False to
    reuse the existing pile), distills the in-lane digest, produces the five-field
    content pull, and saves the run to the shared ledger. Returns digest + pull as
    markdown, ending with the run_id you pass to log_outcome later."""
    result = research_agent.run_research(scan=scan)
    return research_agent.format_for_chat(result)


@mcp.tool()
def log_outcome(run_id: str, verdict: str = "", note: str = "") -> str:
    """Record the founder's verdict on a past research run so the agent learns from
    it next time. verdict is one of: strong | ok | off. note is a one-line read."""
    updated = research_agent.log_outcome(
        run_id, verdict=verdict or None, note=note or None
    )
    return "Outcome saved." if updated else "No run updated — check the run_id."


@mcp.tool()
def run_reply_finder(channels: str = "", top_n: int = 10) -> str:
    """Scan the enabled channels (default Reddit) for reply targets and thin-judge
    the new ones for strategic fit. channels: comma-separated names, or empty for the
    REPLY_CHANNELS default. Returns per-channel verdicts (engage / maybe / skip)."""
    chans = [c.strip() for c in channels.split(",") if c.strip()] or None
    result = reply_finder_agent.run_find_candidates(channels=chans, top_n=top_n)
    return reply_finder_agent.format_for_chat(result)


@mcp.tool()
def run_reply_drafter(candidate_id: str = "", platform: str = "reddit",
                      limit: int = 3) -> str:
    """Draft 2-3 in-voice reply variants for 'engage' candidates (or one candidate by
    id), gate each against the compliance floor, and store them for review. Returns
    how many variants landed per candidate. Does NOT post anything."""
    result = reply_drafter_agent.run_draft_reply(
        candidate_id=candidate_id or None, platform=platform, limit=limit)
    return reply_drafter_agent.format_for_chat(result)


@mcp.tool()
def reply_queue(platform: str = "reddit", limit: int = 20) -> str:
    """Show the review queue: candidates with drafts ready for me to review and send
    by hand in the CLI (python -m fleet.reply_review). Read-only."""
    rows = reply_store.review_queue(platform=platform, limit=limit)
    if not rows:
        return "Review queue empty — run the finder + drafter first."
    lines = [f"### Review queue ({len(rows)})"]
    for c in rows:
        lines.append(f"- [{c.get('judge_score')}] {c.get('community')} — "
                     f"{(c.get('post_title') or '')[:80]}  `{c.get('id')}`")
    return "\n".join(lines)


@mcp.tool()
def reply_recent(limit: int = 20, platform: str = "") -> str:
    """Recent sent replies from the ledger (the voice-training rows), newest first."""
    rows = reply_store.recent_sent(limit=limit, platform=platform or None)
    if not rows:
        return "No sent replies logged yet."
    lines = [f"### Recent sent ({len(rows)})"]
    for r in rows:
        lines.append(
            f"- [{r.get('predicted_grade') or '-'}] edit_ratio {r.get('edit_ratio')} "
            f"· {r.get('community')} — {(r.get('post_title') or '')[:60]} `{r.get('id')}`")
    return "\n".join(lines)


@mcp.tool()
def log_reply_outcome(ledger_id: str, outcome_score: int = -1, op_replied: str = "",
                      led_to_profile: str = "", note: str = "") -> str:
    """Record a sent reply's outcome once it's landed (the weekly close). outcome_score
    = upvotes / likes (0 is valid — a dud; leave it -1 to not set it). op_replied /
    led_to_profile = yes | no | unknown."""
    updated = reply_store.update_outcome(
        ledger_id,
        outcome_score=outcome_score if outcome_score >= 0 else None,
        op_replied=op_replied or None,
        led_to_profile=led_to_profile or None,
        outcome_notes=note or None,
    )
    return "Outcome saved." if updated else "No ledger row updated — check the id."


@mcp.tool()
def run_outcome_checker(dry_run: bool = False) -> str:
    """Read real engagement for every sent reply still missing an outcome (Reddit via
    the official API when REDDIT_* creds are set; X free via Typefully analytics) and
    write the numbers to the ledger. dry_run=True reads + reports without writing."""
    result = outcome_checker_agent.run_check_outcomes(dry_run=dry_run)
    return outcome_checker_agent.format_for_chat(result)


@mcp.tool()
def run_distiller(dry_run: bool = False) -> str:
    """Distill the captured draft->final reply edits into PROPOSED style-guide rules
    (voice edits) + judge/drafter notes (substance edits). Nothing auto-commits — the
    founder approves before any rule lands in the guide. dry_run skips the proposal
    file write."""
    result = distiller_agent.run_distill(dry_run=dry_run)
    return distiller_agent.format_for_chat(result)


@mcp.tool()
def run_reply_eval(dry_run: bool = False) -> str:
    """Re-draft the gold-set posts with the current drafter brain and blind
    pairwise-judge them against the founder's sent replies. Reports the challenger
    win-rate for the current style-guide version (the metric that shows whether
    approved distiller rules actually helped). dry_run appends no results."""
    previous = reply_eval_agent._previous_win_rate()
    result = reply_eval_agent.run_eval(dry_run=dry_run)
    return reply_eval_agent.format_for_chat(result, previous=previous)


@mcp.tool()
def run_repurposer(clip_text: str, video_slug: str = "", clip_label: str = "",
                   platforms: str = "youtube_short,x,instagram",
                   dry_run: bool = False) -> str:
    """Package one video clip into platform-native post drafts (YouTube Short title +
    description, one-breath X post, Instagram caption + hashtags, TikTok caption).
    Pass the clip's transcript text; packages store as content_calendar drafts with
    video lineage. Never posts anything."""
    result = repurposer_agent.run_repurpose(
        clip_text=clip_text, video_slug=video_slug or None,
        clip_label=clip_label or None,
        platforms=[p.strip() for p in platforms.split(",") if p.strip()],
        dry_run=dry_run)
    return repurposer_agent.format_for_chat(result)


if __name__ == "__main__":
    mcp.run()  # stdio transport by default
