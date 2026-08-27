"""One provider-agnostic door for every fleet agent's LLM call — stdlib only.

The rule (founder's, locked 2026-07-22): agents are model-agnostic. Swapping a
model is a CONFIG STRING change (env var / app.py constant), never a code
change, and nothing in fleet/ imports a provider SDK. Every agent calls
llm.complete(); this module owns the transport, the error ladder, and the
usage/cost metering, once.

Routing — decided by what's in the environment, per call:

  * OPENROUTER_API_KEY set  ->  everything goes through OpenRouter's
    OpenAI-compatible endpoint. ONE key for every provider's models; model
    strings are OpenRouter slugs ("anthropic/claude-sonnet-4.6",
    "google/gemini-2.5-flash", "openrouter/auto", ...). Cost comes back
    server-computed in each response — no price table to maintain.
  * else ANTHROPIC_API_KEY  ->  straight to Anthropic's REST API (urllib, no
    SDK). No middleman fee; cost computed from the PRICES table below. This
    is what the deployed Railway research agent uses today, so nothing breaks
    before the OpenRouter key exists.

Bare Claude ids and OpenRouter slugs are interchangeable: "claude-sonnet-4-6"
auto-translates to "anthropic/claude-sonnet-4.6" and back, so the same config
value works on either path. A non-Anthropic model with no OpenRouter key gets
a plain "needs OpenRouter" error instead of a guess. Note some newest models
reject sampling params (e.g. temperature on Opus 4.7+) — that surfaces as the
provider's own 400, fix by dropping the param at the call site or switching
the model string.

Metering is built in: reset_usage() at the start of a run, usage_totals() at
the end -> the dict store.record_heartbeat(**totals) expects (dominant model,
token sums, cost, per-model breakdown in detail). Same shape on both paths.

The meter doubles as a circuit breaker: once a run's spend or call count
crosses LLM_RUN_MAX_COST_USD / LLM_RUN_MAX_CALLS, complete() refuses further
calls with a plain error instead of hitting the API. Agents already treat any
error as "log it and stop", so a runaway loop dies at the budget line without
crashing anything. reset_usage() re-arms it (one run = one budget).
"""

import json
import os
import re
import urllib.error
import urllib.request

MODELS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models.json")
_models_cache = {"mtime": None, "data": {}}

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

# Direct-Anthropic path only (OpenRouter reports cost itself). USD per million
# tokens: (input, output), prefix-matched. Trying a new Claude model directly =
# add one line; unknown models record tokens with cost flagged unpriced.
PRICES = {
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-fable-5": (10.00, 50.00),
}
CACHE_WRITE_MULT = 1.25
CACHE_READ_MULT = 0.10

# Per-run circuit breaker. The dollar cap is the real guard; the call cap is
# its backstop for calls the meter can't price (cost_usd None — unlisted
# models, xAI x_search fed in via record_usage). Both are generous: a normal
# reply run is ~5 calls / well under $1, so tripping one means a bug.
RUN_MAX_COST_USD = float(os.environ.get("LLM_RUN_MAX_COST_USD", "2.00"))
RUN_MAX_CALLS = int(os.environ.get("LLM_RUN_MAX_CALLS", "40"))


def model_for(agent, default):
    """The one place an agent's model is decided (dashboard Models panel,
    2026-08-22). Precedence: env MODEL_<AGENT> (emergency override, works on
    Railway) > fleet/models.json (what the dashboard panel edits) > the
    call site's default. models.json is re-read on mtime change, and every
    agent runs as a fresh subprocess anyway, so a panel edit applies to the
    very next run - no restarts."""
    env = os.environ.get(f"MODEL_{agent.upper()}")
    if env:
        return env
    try:
        mtime = os.path.getmtime(MODELS_FILE)
        if _models_cache["mtime"] != mtime:
            with open(MODELS_FILE, encoding="utf-8-sig") as f:
                _models_cache["data"] = json.load(f)
            _models_cache["mtime"] = mtime
    except (OSError, ValueError):
        _models_cache["data"] = {}
    return _models_cache["data"].get(agent) or default


class LLMResult:
    """What a successful call hands back: .text, .model (as the provider
    reported it), .truncated (hit the max_tokens cap), .usage (raw dict)."""

    def __init__(self, text, model, truncated, usage):
        self.text = text
        self.model = model
        self.truncated = truncated
        self.usage = usage


# ── model-name translation (one config string, either provider) ──────────────
def _openrouter_name(model):
    if "/" in model:
        return model
    m = re.match(r"^(claude-[a-z]+)-(\d+)-(\d+)(?:-\d{8})?$", model)
    if m:  # "claude-sonnet-4-6" / dated haiku id -> "anthropic/claude-sonnet-4.6"
        return f"anthropic/{m.group(1)}-{m.group(2)}.{m.group(3)}"
    if model.startswith("claude"):  # e.g. "claude-fable-5"
        return f"anthropic/{model}"
    return model  # not ours to guess — let OpenRouter validate it


def _anthropic_name(model):
    """Native Anthropic id for a config string, or None if it's not an
    Anthropic model (i.e. it needs OpenRouter)."""
    if "/" not in model:
        return model
    provider, _, name = model.partition("/")
    if provider != "anthropic":
        return None
    return re.sub(r"(\d+)\.(\d+)", r"\1-\2", name)  # 4.6 -> 4-6


# ── the two transports ───────────────────────────────────────────────────────
def _post_json(url, body, headers):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _openrouter(key, model, system, user, max_tokens, temperature):
    messages = ([{"role": "system", "content": system}] if system else [])
    messages.append({"role": "user", "content": user})
    body = {"model": _openrouter_name(model), "messages": messages,
            "max_tokens": max_tokens, "usage": {"include": True}}
    if temperature is not None:
        body["temperature"] = temperature
    data = _post_json(OPENROUTER_URL, body, {
        "Authorization": f"Bearer {key}",
        "X-Title": "Fiboprana Marketing OS"})
    if data.get("error"):  # OpenRouter can 200 with an error envelope
        raise RuntimeError(data["error"].get("message") or str(data["error"]))
    choice = data["choices"][0]
    usage = data.get("usage") or {}
    return LLMResult(
        text=(choice["message"].get("content") or "").strip(),
        model=data.get("model") or model,
        truncated=choice.get("finish_reason") == "length",
        usage={"tokens_input": usage.get("prompt_tokens") or 0,
               "tokens_output": usage.get("completion_tokens") or 0,
               "cost_usd": usage.get("cost")},
    )


def _anthropic_cost(model, usage):
    for prefix, (in_rate, out_rate) in PRICES.items():
        if model.startswith(prefix):
            tok = lambda k: usage.get(k) or 0  # noqa: E731
            return ((tok("input_tokens")
                     + tok("cache_creation_input_tokens") * CACHE_WRITE_MULT
                     + tok("cache_read_input_tokens") * CACHE_READ_MULT) * in_rate
                    + tok("output_tokens") * out_rate) / 1e6
    return None  # unpriced — visible in totals, never guessed


def _anthropic(key, model, system, user, max_tokens, temperature):
    body = {"model": model, "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": user}]}
    if system:
        body["system"] = system
    if temperature is not None:
        body["temperature"] = temperature
    data = _post_json(ANTHROPIC_URL, body, {
        "x-api-key": key, "anthropic-version": ANTHROPIC_VERSION})
    usage = data.get("usage") or {}
    tokens_in = ((usage.get("input_tokens") or 0)
                 + (usage.get("cache_creation_input_tokens") or 0)
                 + (usage.get("cache_read_input_tokens") or 0))
    return LLMResult(
        text="".join(b.get("text", "") for b in data.get("content", [])
                     if b.get("type") == "text").strip(),
        model=data.get("model") or model,
        truncated=data.get("stop_reason") == "max_tokens",
        usage={"tokens_input": tokens_in,
               "tokens_output": usage.get("output_tokens") or 0,
               "cost_usd": _anthropic_cost(data.get("model") or model, usage)},
    )


def configured():
    """True when some LLM key is present — for cheap pre-checks (startup
    warnings, disable-the-button UX) before any complete() call is made."""
    return bool(os.environ.get("OPENROUTER_API_KEY")
                or os.environ.get("ANTHROPIC_API_KEY"))


# ── the one entry point ──────────────────────────────────────────────────────
def _over_budget():
    """The run-level circuit breaker: a reason string when the next call must be
    refused, else None. Reads the same _calls list the meter fills, so anything
    metered (including record_usage'd tool calls) counts against the budget."""
    if len(_calls) >= RUN_MAX_CALLS:
        return (f"Run budget breaker: {len(_calls)} LLM calls this run "
                f"(cap {RUN_MAX_CALLS}; raise LLM_RUN_MAX_CALLS if intended).")
    spent = sum(c["cost_usd"] for c in _calls if c["cost_usd"] is not None)
    if spent >= RUN_MAX_COST_USD:
        return (f"Run budget breaker: ${spent:.2f} spent this run "
                f"(cap ${RUN_MAX_COST_USD:.2f}; raise LLM_RUN_MAX_COST_USD if intended).")
    return None


def complete(*, model, system, user, max_tokens, temperature=None):
    """One completion: (LLMResult, None) on success, (None, error) on failure.
    The error strings are human-readable and safe to surface in run output —
    every agent's old per-call except-ladder now lives here, once."""
    breaker = _over_budget()
    if breaker:
        return None, breaker
    or_key = os.environ.get("OPENROUTER_API_KEY")
    an_key = os.environ.get("ANTHROPIC_API_KEY")
    try:
        if or_key:
            result = _openrouter(or_key, model, system, user, max_tokens, temperature)
        elif an_key:
            name = _anthropic_name(model)
            if name is None:
                return None, (f"Model '{model}' needs OpenRouter — set "
                              "OPENROUTER_API_KEY in .env.")
            result = _anthropic(an_key, name, system, user, max_tokens, temperature)
        else:
            return None, ("No LLM API key configured — set OPENROUTER_API_KEY "
                          "(one key, any provider) or ANTHROPIC_API_KEY in .env.")
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:  # noqa: BLE001
            pass
        if e.code == 429:
            return None, "Model API rate limit hit. Wait a moment and try again."
        return None, f"Model API error ({e.code}): {detail or e.reason}"
    except urllib.error.URLError as e:
        return None, f"Couldn't reach the model API: {e.reason}"
    except Exception as e:  # noqa: BLE001
        return None, f"Unexpected LLM error: {e}"

    if not result.text:
        return None, "Empty response from the model."
    _calls.append({"model": result.model, **result.usage})
    return result, None


# ── built-in metering (reset per run; totals ride the heartbeat) ─────────────
_calls = []


def reset_usage():
    _calls.clear()


def record_usage(*, model, tokens_input, tokens_output, cost_usd=None):
    """Feed the run meter from a call that did NOT go through complete() —
    e.g. a provider-specific tool call (xAI x_search). Unpriced calls pass
    cost_usd=None and show up flagged in totals rather than guessed."""
    _calls.append({"model": model, "tokens_input": tokens_input or 0,
                   "tokens_output": tokens_output or 0, "cost_usd": cost_usd})


def usage_totals():
    """Roll-up shaped for store.record_heartbeat(**usage_totals()). Empty dict
    when the run made no calls. `model` is the run's dominant model; the full
    per-model breakdown rides in detail["by_model"]."""
    if not _calls:
        return {}
    by_model = {}
    for c in _calls:
        m = by_model.setdefault(c["model"], {"calls": 0, "tokens_input": 0,
                                             "tokens_output": 0, "cost_usd": 0.0,
                                             "priced": True})
        m["calls"] += 1
        m["tokens_input"] += c["tokens_input"]
        m["tokens_output"] += c["tokens_output"]
        if c["cost_usd"] is None:
            m["priced"] = False
            m["cost_usd"] = None
        elif m["cost_usd"] is not None:
            m["cost_usd"] = round(m["cost_usd"] + c["cost_usd"], 4)
    dominant = max(by_model, key=lambda k: (by_model[k]["tokens_input"]
                                            + by_model[k]["tokens_output"]))
    priced = [c["cost_usd"] for c in _calls if c["cost_usd"] is not None]
    return {
        "model": dominant,
        "tokens_input": sum(c["tokens_input"] for c in _calls),
        "tokens_output": sum(c["tokens_output"] for c in _calls),
        "cost_usd": round(sum(priced), 4) if priced else None,
        "detail": {"llm_calls": len(_calls), "by_model": by_model},
    }
