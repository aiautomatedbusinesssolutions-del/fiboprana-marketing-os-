"""Publish hand-off — the thin layer between a logged reply and the live platform.

Login safety (REPLY_RUNBOOK §1): Fiboprana NEVER auto-posts. No platform credentials
live anywhere in this repo, and `post()` raises on purpose. A reply is the highest-stakes
legal + brand surface we have, so a human reads every word and presses send by hand.

This module's whole job is to (a) format paste-ready hand-off text and (b) be the single
place that would have to change — deliberately, behind a gate — if auto-publish ever
lands (e.g. Typefully for X, only if it can reply-target an external post). Until then,
the safe path is: log the reply, copy the text this prints, paste it yourself.
"""

_RULE = "-" * 60
_LABELS = {"reddit": "Reddit", "x": "X", "youtube": "YouTube"}


class PublishError(RuntimeError):
    """Raised on any attempt to auto-post. Fiboprana replies are sent by hand."""


def post(platform, text, **_ignored):
    """Hard stop. There is no auto-post path; this exists so a future wiring mistake
    fails loud instead of quietly touching a live account."""
    raise PublishError(
        f"Refusing to auto-post to {platform!r}: Fiboprana sends every reply by hand "
        "(no credentials are configured). Use handoff() to get paste-ready text.")


def handoff(platform, text, *, ledger_id=None):
    """Return paste-ready hand-off text for a reply you'll post yourself.

    Plain text only — no markdown blockquotes — so a copy-paste carries no stray
    vertical bar into the platform's composer.
    """
    text = (text or "").strip()
    label = _LABELS.get((platform or "").lower(), platform or "")
    where = f" on {label}" if label else ""
    lines = [f"PASTE-READY — post this by hand{where}:", _RULE, text, _RULE]
    if ledger_id:
        lines.append(
            f"then attach the link:  python -m fleet.reply_review url {ledger_id} <permalink>")
    return "\n".join(lines)
