"""Scan + score + rank Reddit reply targets from public RSS (no Reddit API, no EXA).

Pure functions over the loaded config. No DB, no Flask — the scan returns a ranked
list of plain dicts, keeping the tunable core (the scoring) separate from however
it's surfaced (a CLI now, a dashboard later).

Source: /r/<sub>/new/.rss via radar/sources/rss.py (browser-UA fetch). RSS gives
title + blurb + date + thread URL but NOT comment count, so unlike a Reddit-API
version there's no "answer-room" signal; ranking leans on flip-fit + freshness.
"""

import time
import urllib.error
from datetime import datetime, timezone

from radar.sources import rss


def _read_feed_with_retry(name, url, lookback_days, retry_backoff=20):
    """rss.read, but ride out one Reddit 429 with a backoff before giving up.
    Reddit rate-limits unauthenticated RSS hard, so a single retry after a pause
    rescues most transient throttles."""
    try:
        return rss.read(name, url, lookback_days=lookback_days)
    except urllib.error.HTTPError as e:
        if e.code != 429:
            raise
        time.sleep(retry_backoff)
        return rss.read(name, url, lookback_days=lookback_days)


def match_flip_groups(text, flip_groups):
    """Return [(group_name, phrase), ...] for every flip phrase found in text.

    Order follows config (pre_decision, post_decision, journey_validation), so the
    first match also decides which group's angle we suggest.
    """
    low = text.lower()
    matched = []
    for group in flip_groups:
        for phrase in group["phrases"]:
            if phrase.lower() in low:
                matched.append((group["name"], phrase))
    return matched


def match_phrases(text, phrases):
    low = text.lower()
    return [p for p in phrases if p.lower() in low]


def _age_hours(published_at):
    if not published_at:
        return None
    try:
        stamp = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - stamp).total_seconds() / 3600)


def score_reply_target(age_hours, flips, softs, scoring, lookback_hours):
    """Higher = better reply target. Asset-neutral: nothing here depends on which
    ticker/asset the thread is about, only on question-shape and freshness."""
    score = scoring["flip_match"] * len(flips)
    if len(flips) >= 2:
        score += scoring["multiple_flips"]
    score += scoring["soft_trigger"] * len(softs)
    if age_hours is not None:
        freshness = max(0.0, 1 - age_hours / lookback_hours)
        score += scoring["freshness_max"] * freshness
    return round(score)


def _angle_for(flips, flip_groups):
    if not flips:
        return None
    first_group = flips[0][0]
    for group in flip_groups:
        if group["name"] == first_group:
            return " ".join(group["angle"].split())  # collapse YAML folded whitespace
    return None


def _apify_items(sub, limit, lookback_hours):
    """Winner of the 9-run finder trial (called 2026-08-07): Apify sees post
    BODIES, so flip matching reads the whole selftext instead of an often-empty
    RSS blurb, and it never got throttled while Reddit 429'd the RSS feeds on
    every recent run. ~$0.46 per 7-sub scan at 25 posts/sub. Needs APIFY_TOKEN;
    raises without it so the per-sub error lands in the scan summary."""
    import os

    from apify_trial.run import fetch_sub_via_apify, normalize_item

    token = os.environ.get("APIFY_TOKEN")
    if not token:
        raise RuntimeError("APIFY_TOKEN missing (config source: apify)")
    raw = fetch_sub_via_apify(token, sub, lookback_hours, limit)
    return [normalize_item(r, sub) for r in raw if r.get("title")]


def scan_for_reply_targets(config):
    """Scan all configured subreddits and return (targets, summary).

    The per-sub fetch comes from config["source"]: "apify" (production since
    the 2026-08-07 trial call) or "rss" (the retired fallback — free, but
    throttled and body-blind). Everything downstream of the fetch is identical.

    targets: ranked list of dicts, best first.
    """
    flip_groups = config["flip_groups"]
    soft_phrases = config["soft_trigger_phrases"]
    exclusion_phrases = config["exclusion_phrases"]
    scoring = config["scoring"]
    min_score = config["scan"]["min_score_to_display"]
    feed_cfg = config["feed"]
    limit = feed_cfg["limit_per_sub"]
    lookback_hours = feed_cfg["lookback_hours"]
    delay = feed_cfg.get("delay_seconds", 2)

    summary = {
        "subreddits_scanned": 0,
        "subreddits_failed": [],
        "posts_examined": 0,
        "candidates": 0,
        "skipped_too_old": 0,
        "skipped_excluded": 0,
        "skipped_off_topic": 0,
        "skipped_low_score": 0,
    }
    targets = []

    source = (config.get("source") or "rss").lower()
    subs = config["subreddits"]
    for i, sub in enumerate(subs):
        try:
            if source == "apify":
                items = _apify_items(
                    sub, feed_cfg.get("apify_limit_per_sub", 25), lookback_hours)
            else:
                url = f"https://www.reddit.com/r/{sub}/new/.rss?limit={limit}"
                items = _read_feed_with_retry(
                    f"r/{sub}", url, lookback_days=max(1, lookback_hours // 24 + 1)
                )
            summary["subreddits_scanned"] += 1
        except Exception as e:  # noqa: BLE001 — one bad feed shouldn't kill the scan
            summary["subreddits_failed"].append((sub, str(e)))
            continue

        for it in items:
            summary["posts_examined"] += 1
            text = (it["title"] + " " + (it.get("blurb") or ""))

            age_hours = _age_hours(it.get("published_at"))
            if age_hours is not None and age_hours > lookback_hours:
                summary["skipped_too_old"] += 1
                continue
            if match_phrases(text, exclusion_phrases):
                summary["skipped_excluded"] += 1
                continue

            flips = match_flip_groups(text, flip_groups)
            softs = match_phrases(text, soft_phrases)
            if not flips and not softs:
                summary["skipped_off_topic"] += 1
                continue

            score = score_reply_target(age_hours, flips, softs, scoring, lookback_hours)
            if score < min_score:
                summary["skipped_low_score"] += 1
                continue

            targets.append({
                "score": score,
                "subreddit": sub,
                "title": it["title"],
                "url": it["url"],
                "author": it.get("author"),
                "age_hours": round(age_hours, 1) if age_hours is not None else None,
                "flips": flips,
                "soft_triggers": softs,
                "angle": _angle_for(flips, flip_groups),
                # surfaced for the channel adapter (additive; the CLI ignores them)
                "blurb": it.get("blurb"),
                "published_at": it.get("published_at"),
                "flip_group": flips[0][0] if flips else None,
            })
            summary["candidates"] += 1

        # The politeness pause exists for Reddit's RSS throttling; Apify actor
        # runs are already serialized on Apify's side and don't need it.
        if source != "apify" and delay and i < len(subs) - 1:
            time.sleep(delay)

    targets.sort(key=lambda t: (-t["score"], t["age_hours"] if t["age_hours"] is not None else 1e9))
    return targets, summary
