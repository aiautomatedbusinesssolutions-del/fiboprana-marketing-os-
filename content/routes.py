"""Flask blueprint for the content module (X/TikTok/LinkedIn generation)."""

from flask import Blueprint, jsonify, redirect, render_template, request, url_for

from db import ensure_schema, get_connection
from fleet import llm

from content.x_post_prompt import X_POST_SYSTEM_PROMPT
from content import utils as content_utils
from content.image_prompt import IMAGE_PROMPT_SYSTEM_PROMPT
from content.tiktok_post_prompt import TIKTOK_POST_SYSTEM_PROMPT
from content.tiktok_image_prompt import TIKTOK_IMAGE_PROMPT_SYSTEM_PROMPT
from content.linkedin_post_prompt import LINKEDIN_POST_SYSTEM_PROMPT
from content.linkedin_image_prompt import LINKEDIN_IMAGE_PROMPT_SYSTEM_PROMPT

from app import (
    X_POST_MODEL, TIKTOK_POST_MODEL, LINKEDIN_POST_MODEL,
    IMAGE_PROMPT_MODEL, TIKTOK_IMAGE_PROMPT_MODEL, LINKEDIN_IMAGE_PROMPT_MODEL,
)


bp = Blueprint("content", __name__)

def _x_posts_error(error, observation=None):
    return render_template(
        "content/x_posts_error.html",
        error=error,
        observation=observation,
    )


@bp.route("/content/x-posts", methods=["POST"])
def generate_x_posts():
    observation_id = request.form.get("observation_id", type=int)
    if not observation_id:
        return _x_posts_error("No observation_id provided.")

    conn = get_connection()
    ensure_schema(conn)
    observation = conn.execute(
        "SELECT * FROM observations WHERE id = :id", {"id": observation_id}
    ).fetchone()

    if observation is None:
        return _x_posts_error(f"Observation #{observation_id} not found.")

    user_message = content_utils.format_observation_for_x_prompt(observation)

    result, err = llm.complete(model=X_POST_MODEL, system=X_POST_SYSTEM_PROMPT,
                               user=user_message, max_tokens=1500, temperature=0.7)
    if err:
        return _x_posts_error(err, observation=observation)
    raw_text = result.text

    try:
        posts = content_utils.parse_x_posts_response(raw_text)
    except ValueError:
        return _x_posts_error(
            "The AI response wasn't valid. Try again.",
            observation=observation,
        )

    return render_template("content/x_posts.html", observation=observation, posts=posts)


def _tiktok_posts_error(error, observation=None):
    return render_template(
        "content/tiktok_posts_error.html",
        error=error,
        observation=observation,
    )


@bp.route("/content/tiktok-posts", methods=["POST"])
def generate_tiktok_posts():
    observation_id = request.form.get("observation_id", type=int)
    if not observation_id:
        return _tiktok_posts_error("No observation_id provided.")

    conn = get_connection()
    ensure_schema(conn)
    observation = conn.execute(
        "SELECT * FROM observations WHERE id = :id", {"id": observation_id}
    ).fetchone()

    if observation is None:
        return _tiktok_posts_error(f"Observation #{observation_id} not found.")

    user_message = content_utils.format_observation_for_tiktok_prompt(observation)

    result, err = llm.complete(model=TIKTOK_POST_MODEL, system=TIKTOK_POST_SYSTEM_PROMPT,
                               user=user_message, max_tokens=1500, temperature=0.7)
    if err:
        return _tiktok_posts_error(err, observation=observation)
    raw_text = result.text

    try:
        posts = content_utils.parse_tiktok_posts_response(raw_text)
    except ValueError:
        return _tiktok_posts_error(
            "The AI response wasn't valid. Try again.",
            observation=observation,
        )

    return render_template("content/tiktok_posts.html", observation=observation, posts=posts)


@bp.route("/content/image-prompts", methods=["POST"])
def generate_image_prompts():
    """Per-post image-prompt generator. Called via AJAX from the X posts page;
    returns one Ideogram prompt and one GPT Image 2.0 prompt as JSON, both
    grounded in the specific post text and the source observation."""
    payload = request.get_json(silent=True) or {}
    raw_obs_id = payload.get("observation_id")
    post_text = (payload.get("post_text") or "").strip()
    post_style = (payload.get("post_style") or "").strip()

    try:
        observation_id = int(raw_obs_id) if raw_obs_id is not None else None
    except (TypeError, ValueError):
        observation_id = None

    if not observation_id or not post_text or not post_style:
        return jsonify({
            "error": "observation_id, post_text and post_style are all required.",
        }), 400

    conn = get_connection()
    ensure_schema(conn)
    observation = conn.execute(
        "SELECT * FROM observations WHERE id = :id", {"id": observation_id}
    ).fetchone()
    if observation is None:
        return jsonify({"error": f"Observation #{observation_id} not found."}), 404

    user_message = content_utils.format_post_for_image_prompt(
        post_text, post_style, observation
    )

    result, err = llm.complete(model=IMAGE_PROMPT_MODEL, system=IMAGE_PROMPT_SYSTEM_PROMPT,
                               user=user_message, max_tokens=1500, temperature=0.7)
    if err:
        return jsonify({"error": err}), 502
    raw_text = result.text

    try:
        prompts = content_utils.parse_image_prompts_response(raw_text)
    except ValueError as e:
        return jsonify({"error": f"AI response wasn't valid: {e}"}), 500

    return jsonify(prompts)


def _linkedin_posts_error(error, observation=None):
    return render_template(
        "content/linkedin_posts_error.html",
        error=error,
        observation=observation,
    )


@bp.route("/content/linkedin-posts", methods=["POST"])
def generate_linkedin_posts():
    observation_id = request.form.get("observation_id", type=int)
    if not observation_id:
        return _linkedin_posts_error("No observation_id provided.")

    conn = get_connection()
    ensure_schema(conn)
    observation = conn.execute(
        "SELECT * FROM observations WHERE id = :id", {"id": observation_id}
    ).fetchone()

    if observation is None:
        return _linkedin_posts_error(f"Observation #{observation_id} not found.")

    user_message = content_utils.format_observation_for_linkedin_prompt(observation)

    result, err = llm.complete(model=LINKEDIN_POST_MODEL, system=LINKEDIN_POST_SYSTEM_PROMPT,
                               user=user_message, max_tokens=2500, temperature=0.7)
    if err:
        return _linkedin_posts_error(err, observation=observation)
    raw_text = result.text

    try:
        posts = content_utils.parse_linkedin_posts_response(raw_text)
    except ValueError:
        return _linkedin_posts_error(
            "The AI response wasn't valid. Try again.",
            observation=observation,
        )

    return render_template("content/linkedin_posts.html", observation=observation, posts=posts)


@bp.route("/content/tiktok-image-prompts", methods=["POST"])
def generate_tiktok_image_prompts():
    """Per-caption image-prompt generator for TikTok. Called via AJAX from the
    TikTok posts page; returns one Ideogram prompt and one GPT Image 2.0 prompt
    as JSON, both grounded in the specific caption and the source observation,
    targeted at a 9:16 vertical TikTok post image (or first carousel slide)."""
    payload = request.get_json(silent=True) or {}
    raw_obs_id = payload.get("observation_id")
    caption_text = (payload.get("caption_text") or "").strip()
    caption_style = (payload.get("caption_style") or "").strip()

    try:
        observation_id = int(raw_obs_id) if raw_obs_id is not None else None
    except (TypeError, ValueError):
        observation_id = None

    if not observation_id or not caption_text or not caption_style:
        return jsonify({
            "error": "observation_id, caption_text and caption_style are all required.",
        }), 400

    conn = get_connection()
    ensure_schema(conn)
    observation = conn.execute(
        "SELECT * FROM observations WHERE id = :id", {"id": observation_id}
    ).fetchone()
    if observation is None:
        return jsonify({"error": f"Observation #{observation_id} not found."}), 404

    user_message = content_utils.format_caption_for_tiktok_image_prompt(
        caption_text, caption_style, observation
    )

    result, err = llm.complete(model=TIKTOK_IMAGE_PROMPT_MODEL,
                               system=TIKTOK_IMAGE_PROMPT_SYSTEM_PROMPT,
                               user=user_message, max_tokens=1500, temperature=0.7)
    if err:
        return jsonify({"error": err}), 502
    raw_text = result.text

    try:
        prompts = content_utils.parse_image_prompts_response(raw_text)
    except ValueError as e:
        return jsonify({"error": f"AI response wasn't valid: {e}"}), 500

    return jsonify(prompts)


@bp.route("/content/linkedin-image-prompts", methods=["POST"])
def generate_linkedin_image_prompts():
    """Per-post image-prompt generator for LinkedIn. Called via AJAX from the
    LinkedIn posts page; returns one Ideogram prompt and one GPT Image 2.0
    prompt as JSON, both grounded in the specific post and source observation,
    targeted at a 1:1 square LinkedIn feed image."""
    payload = request.get_json(silent=True) or {}
    raw_obs_id = payload.get("observation_id")
    post_text = (payload.get("post_text") or "").strip()
    post_style = (payload.get("post_style") or "").strip()

    try:
        observation_id = int(raw_obs_id) if raw_obs_id is not None else None
    except (TypeError, ValueError):
        observation_id = None

    if not observation_id or not post_text or not post_style:
        return jsonify({
            "error": "observation_id, post_text and post_style are all required.",
        }), 400

    conn = get_connection()
    ensure_schema(conn)
    observation = conn.execute(
        "SELECT * FROM observations WHERE id = :id", {"id": observation_id}
    ).fetchone()
    if observation is None:
        return jsonify({"error": f"Observation #{observation_id} not found."}), 404

    user_message = content_utils.format_post_for_linkedin_image_prompt(
        post_text, post_style, observation
    )

    result, err = llm.complete(model=LINKEDIN_IMAGE_PROMPT_MODEL,
                               system=LINKEDIN_IMAGE_PROMPT_SYSTEM_PROMPT,
                               user=user_message, max_tokens=1500, temperature=0.7)
    if err:
        return jsonify({"error": err}), 502
    raw_text = result.text

    try:
        prompts = content_utils.parse_image_prompts_response(raw_text)
    except ValueError as e:
        return jsonify({"error": f"AI response wasn't valid: {e}"}), 500

    return jsonify(prompts)
