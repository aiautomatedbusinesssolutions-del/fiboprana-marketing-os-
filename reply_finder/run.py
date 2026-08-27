"""Run a Reddit-RSS reply-target scan and print a ranked queue.

    python -m reply_finder.run            # scan, print the ranked top 25
    python -m reply_finder.run --top 10
    python -m reply_finder.run --all

No Reddit API key, no EXA — reads public /r/<sub>/new/.rss feeds. No DB: prints
what it finds each run. Persistence (mark-replied / dedupe across weeks) is
deferred to the dashboard-transfer step.
"""

import argparse
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

from reply_finder.config import load_config  # noqa: E402
from reply_finder.scanner import scan_for_reply_targets  # noqa: E402


def _fmt_target(rank, t):
    age = f"{t['age_hours']}h old" if t["age_hours"] is not None else "age unknown"
    flips = ", ".join(f'"{p}"' for _, p in t["flips"]) or "-"
    softs = ", ".join(t["soft_triggers"]) or "-"
    lines = [
        f"{rank:>2}. [{t['score']:>3}]  r/{t['subreddit']}  |  {age}",
        f"     {t['title']}",
        f"     flip: {flips}    beginner: {softs}",
    ]
    if t["angle"]:
        lines.append(f"     angle: {t['angle']}")
    lines.append(f"     {t['url']}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Rank Reddit reply targets from RSS.")
    parser.add_argument("--top", type=int, default=25, help="max rows to show")
    parser.add_argument("--all", action="store_true", help="show every candidate")
    args = parser.parse_args()

    config = load_config()
    targets, summary = scan_for_reply_targets(config)

    print("\n=== Reply finder (Reddit RSS) ===")
    print(
        f"scanned {summary['subreddits_scanned']} subs, "
        f"examined {summary['posts_examined']} posts  ->  "
        f"{summary['candidates']} candidates"
    )
    print(
        f"  filtered: {summary['skipped_too_old']} too old, "
        f"{summary['skipped_excluded']} excluded, "
        f"{summary['skipped_off_topic']} off-topic, "
        f"{summary['skipped_low_score']} below floor"
    )
    for sub, err in summary["subreddits_failed"]:
        print(f"  ! r/{sub} failed: {err}")
    print()

    shown = targets if args.all else targets[: args.top]
    if not shown:
        print("No reply targets cleared the score floor this run.")
        return 0

    for i, t in enumerate(shown, 1):
        print(_fmt_target(i, t))
        print()

    if not args.all and len(targets) > len(shown):
        print(f"... {len(targets) - len(shown)} more below (use --all to see them).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
