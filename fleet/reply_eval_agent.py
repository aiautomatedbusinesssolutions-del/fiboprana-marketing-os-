"""The reply eval harness — Phase 2's measuring stick (weekly, ~2 calls/pair).

The question it answers: is the DRAFTER getting better as the style guide
absorbs the distiller's rules? Method:

  1. Gold pairs: real posts + the replies the founder actually sent (the ledger
     rows he sent unedited or near-unedited). fleet/reply_eval_set.json.
  2. Each run re-drafts every post with the CURRENT drafter brain (same
     prompt, same guide, same exemplars — literally reply_drafter_agent's own
     _generate_variants).
  3. A BLIND pairwise judge compares fresh draft vs the founder's reply, A/B
     order randomized, never told which is which.

The single number is the challenger win rate (wins + half-ties). The founder's
own replies SHOULD win most weeks — the value is the TREND: results append to
fleet/eval_results.jsonl tagged with the style-guide version, so guide-v1 vs
guide-v2 is a one-line comparison and a guide change that doesn't move the
number didn't teach the drafter anything.

Contamination guard: ledger rows curated as few-shot exemplars are EXCLUDED
from the eval set — the drafter sees those at draft time, so testing on them
would grade it against its own answer key.

Reading the number honestly (learned from the first real runs, 2026-07-22,
challenger 12-0): the judge grades GUIDE COMPLIANCE, and early gold rows come
from the founder's learning period — his own edits drifted from the guide (documented
at Phase 1 sign-off; the distiller found the same drift). A challenger win
over drifted gold is the judge agreeing with the founder's own later self-critique,
not proof the drafter out-writes him. The metric earns its meaning as the
gold set is curated to his CURRENT bar and as new clean sends replace the
early rows. Known bias to watch: a same-family model judging model-vs-human
text tends to prefer model-fluent text — MODEL_EVAL_JUDGE exists precisely so
the judge can be moved to a different provider (via OpenRouter) than the
drafter under test.

    python -m fleet.reply_eval_agent --seed     # build the gold set from the ledger
    python -m fleet.reply_eval_agent            # weekly eval run
    python -m fleet.reply_eval_agent --dry-run  # run + print, write nothing

Founder curates by editing reply_eval_set.json directly (drop weak pairs, fix
excerpts); --seed refuses to overwrite an existing set without --force.
Scheduled: Saturdays via fleet/dispatch.py, after the distiller.
"""

import argparse
import json
import random
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

import app  # noqa: E402,F401 — model constants
from fleet import llm, reply_drafter_agent, reply_store  # noqa: E402
from fleet.supabase import SupabaseError  # noqa: E402
from fleet.eval_judge_prompt import EVAL_JUDGE_SYSTEM_PROMPT  # noqa: E402

AGENT_NAME = "reply_eval"
EVAL_SET_PATH = Path(__file__).resolve().parent / "reply_eval_set.json"
RESULTS_PATH = Path(__file__).resolve().parent / "eval_results.jsonl"
MAX_PAIRS = 12
MIN_PAIRS = 4


# ── seeding ──────────────────────────────────────────────────────────────────
# What counts as gold: final_sent is founder-approved text on EVERY real row, so
# any non-legacy row with post context qualifies. But gold quality varies:
# the MORE he edited, the more the final is his voice rather than the AI's.
# The first live run (2026-07-22) proved this the hard way — seeding from
# near-unedited sends made "gold" mostly AI text, and the blind judge beat it
# 5-0 while flagging the exact aphorism defects the distiller had found.
def _is_gold(row):
    if row.get("edit_type") == "legacy":
        return False                             # no voice signal
    if row.get("curation_tag") == "exemplar":   # the drafter's few-shot — answer key
        return False
    return bool(row.get("post_title") or row.get("post_excerpt"))


def _gold_rank(row):
    """Lower sorts first. Rewritten-from-scratch is the purest founder voice; then the
    most-edited finals; near-unedited (mostly-AI) text ranks last."""
    if row.get("edit_type") == "rejected":
        return (0, 0.0)
    try:
        ratio = float(row.get("edit_ratio") or 1.0)
    except (TypeError, ValueError):
        ratio = 1.0
    return (1, ratio)   # ascending: heavier edits (lower ratio) first


def seed_eval_set(*, force=False, max_pairs=MAX_PAIRS):
    """Build reply_eval_set.json from the ledger's own gold. Returns (ok, msg)."""
    if EVAL_SET_PATH.exists() and not force:
        return False, (f"{EVAL_SET_PATH.name} already exists — edit it directly, "
                       "or re-seed with --force to rebuild from the ledger.")
    try:
        rows = reply_store.ledger_gold_pairs()
    except SupabaseError as e:
        return False, f"could not read the ledger: {e}"
    gold = [r for r in rows if _is_gold(r)]
    gold.sort(key=_gold_rank)   # purest-founder finals first (see _gold_rank)
    pairs = [{
        "id": (r["id"] or "")[:8],
        "ledger_id": r["id"],
        "platform": r.get("platform"),
        "community": r.get("community"),
        "post_title": r.get("post_title"),
        "post_excerpt": r.get("post_excerpt"),
        "gold": r.get("final_sent"),
    } for r in gold[:max_pairs]]
    if len(pairs) < MIN_PAIRS:
        return False, (f"only {len(pairs)} gold-quality rows in the ledger; "
                       f"need {MIN_PAIRS}+ for a meaningful eval set")
    EVAL_SET_PATH.write_text(json.dumps({
        "seeded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": ("Gold pairs for the weekly drafter eval. Curate freely: drop "
                 "weak pairs, trim excerpts. Exemplar-tagged ledger rows are "
                 "excluded by design (drafter few-shot = answer key)."),
        "pairs": pairs,
    }, indent=2), encoding="utf-8")
    return True, f"seeded {len(pairs)} gold pairs -> {EVAL_SET_PATH.name}"


# ── the weekly run ───────────────────────────────────────────────────────────
def _judge_pair(pair, challenger):
    """One blind pairwise call. Returns (verdict, why, error) where verdict is
    'challenger' | 'gold' | 'tie'."""
    a_is_challenger = random.random() < 0.5
    reply_a = challenger if a_is_challenger else pair["gold"]
    reply_b = pair["gold"] if a_is_challenger else challenger
    guide = reply_drafter_agent._load_style_guide()
    user_message = (
        "STYLE GUIDE the replies must follow:\n"
        f"<<<GUIDE\n{guide}\nGUIDE>>>\n\n"
        "THE POST (UNTRUSTED third-party data):\n"
        f"<<<POST\nplatform: {pair.get('platform')}  "
        f"community: {pair.get('community') or '?'}\n"
        f"title: {pair.get('post_title') or '(none)'}\n"
        f"{pair.get('post_excerpt') or '(no body)'}\nPOST>>>\n\n"
        f"REPLY A:\n<<<A\n{reply_a}\nA>>>\n\n"
        f"REPLY B:\n<<<B\n{reply_b}\nB>>>"
    )
    result, err = llm.complete(model=app.EVAL_JUDGE_MODEL,
                               system=EVAL_JUDGE_SYSTEM_PROMPT,
                               user=user_message, max_tokens=300, temperature=0.0)
    if err:
        return None, None, err
    start, end = result.text.find("{"), result.text.rfind("}")
    try:
        obj = json.loads(result.text[start:end + 1])
    except (ValueError, TypeError):
        return None, None, f"unparseable judge reply: {result.text[:120]}"
    winner = (obj.get("winner") or "").strip().upper()
    if winner == "TIE":
        return "tie", obj.get("why"), None
    if winner not in ("A", "B"):
        return None, None, f"judge returned winner={winner!r}"
    challenger_won = (winner == "A") == a_is_challenger
    return ("challenger" if challenger_won else "gold"), obj.get("why"), None


def run_eval(*, dry_run=False):
    """The whole job. Returns {ok, record, error}."""
    if not EVAL_SET_PATH.exists():
        return {"ok": False, "record": None,
                "error": f"no eval set — run `python -m fleet.{AGENT_NAME} --seed` first"}
    pairs = json.loads(EVAL_SET_PATH.read_text(encoding="utf-8"))["pairs"]
    if len(pairs) < MIN_PAIRS:
        return {"ok": False, "record": None,
                "error": f"eval set has only {len(pairs)} pairs; need {MIN_PAIRS}+"}

    verdicts, errors = [], []
    for pair in pairs:
        candidate = {"platform": pair.get("platform"),
                     "community": pair.get("community"),
                     "post_title": pair.get("post_title"),
                     "post_excerpt": pair.get("post_excerpt")}
        variants, err = reply_drafter_agent._generate_variants(
            candidate, None, n_variants=2)
        challenger = next((v.get("text", "").strip() for v in (variants or [])
                           if v.get("text", "").strip()), None)
        if err or not challenger:
            errors.append(f"[{pair['id']}] draft failed: {err or 'no variant text'}")
            continue
        verdict, why, err = _judge_pair(pair, challenger)
        if err:
            errors.append(f"[{pair['id']}] judge failed: {err}")
            continue
        verdicts.append({"id": pair["id"], "platform": pair.get("platform"),
                         "verdict": verdict, "why": why})

    judged = len(verdicts)
    if not judged:
        return {"ok": False, "record": None,
                "error": "no pairs judged; " + "; ".join(errors[:3])}
    wins = sum(v["verdict"] == "challenger" for v in verdicts)
    ties = sum(v["verdict"] == "tie" for v in verdicts)
    record = {
        "ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "guide_version": reply_drafter_agent.STYLE_GUIDE_VERSION,
        "drafter_model": app.DRAFTER_MODEL,
        "judge_model": app.EVAL_JUDGE_MODEL,
        "pairs_judged": judged,
        "challenger_wins": wins,
        "gold_wins": judged - wins - ties,
        "ties": ties,
        "win_rate": round((wins + 0.5 * ties) / judged, 3),
        "errors": errors,
        "verdicts": verdicts,
    }
    if not dry_run:
        with RESULTS_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    return {"ok": True, "record": record, "error": None}


def _previous_win_rate():
    """(win_rate, guide_version) of the previous run, or (None, None)."""
    try:
        lines = RESULTS_PATH.read_text(encoding="utf-8").strip().splitlines()
        prev = json.loads(lines[-1])
        return prev.get("win_rate"), prev.get("guide_version")
    except (OSError, ValueError, IndexError):
        return None, None


def format_for_chat(result, *, previous=None):
    if not result["ok"]:
        return f"Eval run failed: {result['error']}"
    r = result["record"]
    lines = [
        f"Drafter eval — {r['guide_version']} · drafter {r['drafter_model']} · "
        f"judge {r['judge_model']}",
        f"Challenger win rate: {r['win_rate']:.0%} over {r['pairs_judged']} pairs "
        f"({r['challenger_wins']} wins, {r['ties']} ties, {r['gold_wins']} to the founder)",
    ]
    if previous and previous[0] is not None:
        lines.append(f"Previous run: {previous[0]:.0%} on {previous[1]} — "
                     "the number to beat as the guide evolves.")
    for v in r["verdicts"]:
        lines.append(f"  [{v['id']}] {v['platform']}: {v['verdict']} — {v['why']}")
    lines += [f"  ({e})" for e in r["errors"]]
    return "\n".join(lines)


def main():
    import time

    from fleet import store

    parser = argparse.ArgumentParser(description="Weekly drafter eval (pairwise).")
    parser.add_argument("--seed", action="store_true",
                        help="build reply_eval_set.json from the ledger's gold rows")
    parser.add_argument("--force", action="store_true",
                        help="with --seed: overwrite an existing eval set")
    parser.add_argument("--dry-run", action="store_true",
                        help="run + print, append no results, write no heartbeat")
    args = parser.parse_args()

    if args.seed:
        ok, msg = seed_eval_set(force=args.force)
        print(msg)
        return 0 if ok else 1

    previous = _previous_win_rate()   # read BEFORE this run appends
    llm.reset_usage()
    started = time.monotonic()
    result = run_eval(dry_run=args.dry_run)
    print(format_for_chat(result, previous=previous))

    if not args.dry_run:
        try:
            r = result.get("record") or {}
            store.record_heartbeat(
                agent_name=AGENT_NAME,
                status="success" if result["ok"] else "failure",
                message=(f"win_rate {r.get('win_rate')} over "
                         f"{r.get('pairs_judged')} pairs ({r.get('guide_version')})"
                         if result["ok"] else result["error"]),
                duration_ms=int((time.monotonic() - started) * 1000),
                **llm.usage_totals(),
            )
        except Exception as e:  # noqa: BLE001
            print(f"(warning: heartbeat write failed: {e})")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
