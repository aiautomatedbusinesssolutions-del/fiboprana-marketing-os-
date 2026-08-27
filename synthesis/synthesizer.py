"""Synthesis generator: call Claude with observations + positioning context,
write the output to SYNTHESIS.md, and store the run in synthesis_runs.

The file at the project root (SYNTHESIS.md) is the consumer side — the idea
proposer reads it as raw text and passes it to its own prompt. This module is
the generator side: produce and update the file when the regenerate button is
clicked. The synthesis_runs table is the history layer.
"""

import json
from datetime import datetime
from pathlib import Path

from fleet import llm
from synthesis.synthesis_prompt import SYNTHESIS_SYSTEM_PROMPT


REPO_ROOT = Path(__file__).resolve().parent.parent
SYNTHESIS_MD_PATH = REPO_ROOT / "SYNTHESIS.md"
POSITIONING_PATH = REPO_ROOT / "POSITIONING.md"
PRODUCT_PRINCIPLES_PATH = REPO_ROOT / "PRODUCT_PRINCIPLES.md"

# Observation fields included in the synthesis prompt. raw_content is the full
# thread; the interpretive fields below are what synthesis works on (matching
# how the founder reads observations when reviewing the corpus by hand).
SYNTHESIS_OBSERVATION_FIELDS = [
    ("segment_guess",        "Segment guess"),
    ("source",               "Source"),
    ("source_detail",        "Source detail"),
    ("pain_points",          "Pain points"),
    ("notable_quotes",       "Notable quotes"),
    ("feature_implications", "Feature implications"),
    ("content_hooks",        "Content hooks"),
    ("tags",                 "Tags"),
]


def format_observations_for_synthesis(rows):
    """Build the user-message body containing positioning context + observations."""
    blocks = []

    if POSITIONING_PATH.is_file():
        positioning = POSITIONING_PATH.read_text(encoding="utf-8").strip()
        if positioning:
            blocks.append(positioning)

    if PRODUCT_PRINCIPLES_PATH.is_file():
        principles = PRODUCT_PRINCIPLES_PATH.read_text(encoding="utf-8").strip()
        if principles:
            blocks.append(principles)

    obs_lines = ["# Observations\n"]
    for row in rows:
        parts = [f"## Observation #{row['id']} ({row['date_observed'] or 'undated'})"]
        for field, label in SYNTHESIS_OBSERVATION_FIELDS:
            value = (row[field] or "").strip()
            if value:
                parts.append(f"**{label}:**\n{value}")
        obs_lines.append("\n\n".join(parts))
    blocks.append("\n\n".join(obs_lines))

    return "\n\n---\n\n".join(blocks)


def run_synthesis(observations, model="claude-sonnet-4-6", max_tokens=10000):
    """Run the observations + positioning through the model (via fleet/llm.py)
    and return the parsed result.

    Returns dict with: synthesis_md, observation_ids, observation_count,
    input_tokens, output_tokens, model.

    Raises ValueError on any API failure or empty response.
    """
    user_message = format_observations_for_synthesis(observations)
    result, err = llm.complete(model=model, system=SYNTHESIS_SYSTEM_PROMPT,
                               user=user_message, max_tokens=max_tokens,
                               temperature=0.3)
    if err:
        raise ValueError(err)

    return {
        "synthesis_md":      result.text,
        "observation_ids":   [row["id"] for row in observations],
        "observation_count": len(observations),
        "input_tokens":      result.usage.get("tokens_input"),
        "output_tokens":     result.usage.get("tokens_output"),
        "model":             result.model,
    }


def write_synthesis_md(text):
    """Overwrite SYNTHESIS.md at the project root with the new content."""
    SYNTHESIS_MD_PATH.write_text(text, encoding="utf-8")


def store_run(conn, result):
    """Insert a row into synthesis_runs and return its id. Commits the connection."""
    cur = conn.execute(
        "INSERT INTO synthesis_runs "
        "(generated_at, observation_count, observation_ids, model, synthesis_md, "
        " input_token_count, output_token_count) "
        "VALUES (:generated_at, :observation_count, :observation_ids, :model, "
        " :synthesis_md, :input_token_count, :output_token_count)",
        {
            "generated_at":       datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            "observation_count":  result["observation_count"],
            "observation_ids":    json.dumps(result["observation_ids"]),
            "model":              result["model"],
            "synthesis_md":       result["synthesis_md"],
            "input_token_count":  result["input_tokens"],
            "output_token_count": result["output_tokens"],
        },
    )
    conn.commit()
    return cur.lastrowid


def latest_run(conn):
    """Most recent synthesis_runs row, or None."""
    return conn.execute(
        "SELECT * FROM synthesis_runs ORDER BY generated_at DESC, id DESC LIMIT 1"
    ).fetchone()


def list_runs(conn, limit=50):
    """synthesis_runs newest first, limited. Omits the synthesis_md blob for the
    list view — call get_run() to pull a specific row's full text."""
    return conn.execute(
        "SELECT id, generated_at, observation_count, model, "
        "       input_token_count, output_token_count "
        "FROM synthesis_runs ORDER BY generated_at DESC, id DESC LIMIT :limit",
        {"limit": limit},
    ).fetchall()


def get_run(conn, run_id):
    """Full synthesis_runs row including the synthesis_md text, or None."""
    return conn.execute(
        "SELECT * FROM synthesis_runs WHERE id = :id", {"id": run_id}
    ).fetchone()
