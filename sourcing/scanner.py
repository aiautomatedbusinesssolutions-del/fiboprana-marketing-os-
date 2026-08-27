"""Reddit scanning + thread-fetch logic for the sourcing module.

Two entry points used by app.py:
    scan_subreddits(reddit, config, conn) -> summary dict
    fetch_thread_markdown(reddit, reddit_id, subreddit) -> markdown string
"""

from datetime import datetime, timezone
import time


def match_triggers(title, selftext, phrases):
    """Return the list of phrases found as case-insensitive substrings in title+selftext."""
    haystack = (title + " " + (selftext or "")).lower()
    return [p for p in phrases if p.lower() in haystack]


def score_post(num_comments, triggers_matched, scoring_cfg):
    score = scoring_cfg["trigger_match"] * len(triggers_matched)
    if len(triggers_matched) >= 2:
        score += scoring_cfg["multiple_triggers"]
    score += num_comments * scoring_cfg["comment_count_weight"]
    if num_comments < scoring_cfg["min_comment_count"]:
        score -= scoring_cfg["trigger_match"]
    return score


def _already_seen(conn, reddit_id):
    row = conn.execute(
        "SELECT 1 FROM seen_posts WHERE reddit_id = :id", {"id": reddit_id}
    ).fetchone()
    return row is not None


def _insert_candidate(conn, post, triggers, score, subreddit_name):
    conn.execute(
        """
        INSERT INTO seen_posts (
            reddit_id, subreddit, title, url, author, score,
            matched_triggers, comment_count, posted_at, status
        ) VALUES (
            :reddit_id, :subreddit, :title, :url, :author, :score,
            :matched_triggers, :comment_count, :posted_at, 'new'
        )
        """,
        {
            "reddit_id": post.id,
            "subreddit": subreddit_name,
            "title": post.title,
            "url": f"https://www.reddit.com{post.permalink}",
            "author": str(post.author) if post.author else "[deleted]",
            "score": score,
            "matched_triggers": ",".join(triggers),
            "comment_count": post.num_comments,
            "posted_at": datetime.fromtimestamp(post.created_utc, tz=timezone.utc).isoformat(),
        },
    )
    conn.commit()


def scan_subreddits(reddit, config, conn):
    """Run a scan across all configured subreddits. Returns a summary dict
    suitable for display in the post-scan banner.
    """
    summary = {
        "subreddits_scanned": 0,
        "subreddits_failed": [],
        "posts_examined": 0,
        "new_candidates": 0,
        "skipped_already_seen": 0,
        "skipped_too_old": 0,
        "skipped_no_triggers": 0,
        "skipped_excluded": 0,
        "skipped_low_score": 0,
    }

    cutoff = time.time() - config["scoring"]["max_age_hours"] * 3600
    scoring_cfg = config["scoring"]
    trigger_phrases = config["trigger_phrases"]
    exclusion_phrases = config["exclusion_phrases"]
    limit = config["scan"]["limit_per_subreddit"]
    min_score = config["scan"]["min_score_to_display"]

    for sub_name in config["subreddits"]:
        try:
            for post in reddit.subreddit(sub_name).new(limit=limit):
                summary["posts_examined"] += 1

                if post.created_utc < cutoff:
                    summary["skipped_too_old"] += 1
                    continue
                if _already_seen(conn, post.id):
                    summary["skipped_already_seen"] += 1
                    continue

                triggers = match_triggers(post.title, post.selftext, trigger_phrases)
                if not triggers:
                    summary["skipped_no_triggers"] += 1
                    continue
                if match_triggers(post.title, post.selftext, exclusion_phrases):
                    summary["skipped_excluded"] += 1
                    continue

                score = score_post(post.num_comments, triggers, scoring_cfg)
                if score < min_score:
                    summary["skipped_low_score"] += 1
                    continue

                _insert_candidate(conn, post, triggers, score, sub_name)
                summary["new_candidates"] += 1
            summary["subreddits_scanned"] += 1
        except Exception as e:  # noqa: BLE001 — any per-sub failure shouldn't kill the scan
            summary["subreddits_failed"].append((sub_name, str(e)))

    return summary


def _is_junk_comment(c):
    if not c.author:
        return True
    if str(c.author).lower() == "automoderator":
        return True
    if c.body in ("[deleted]", "[removed]"):
        return True
    return False


def fetch_thread_markdown(reddit, reddit_id, subreddit):
    """Fetch the post + top-level comments (no reply trees) and format as
    markdown suitable for pasting into the raw_content field.
    """
    submission = reddit.submission(id=reddit_id)
    submission.comments.replace_more(limit=0)

    age_hours = int((time.time() - submission.created_utc) / 3600)
    lines = [
        f"# {submission.title}",
        f"Subreddit: r/{subreddit}",
        f"Posted: {age_hours}h ago",
        "",
        submission.selftext or "(no body)",
    ]
    for c in submission.comments:
        if _is_junk_comment(c):
            continue
        lines += ["", "---", "", f"**{c.author}**: {c.body}"]
    return "\n".join(lines)
