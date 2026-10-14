"""Claude API client wrapper, with a mock-mode branch.

This module is the single seam every pipeline stage calls through for a
structured-output LLM request (spec extraction, per-diagram generation,
repair). It decides — before anything else — whether to call the real
Anthropic API or delegate to the deterministic mock in `app/mock_llm.py`.
The rest of the app never makes that decision itself; it just calls
`call_json` and gets back a schema-shaped dict either way.

There is no `ANTHROPIC_API_KEY` on this machine, so `MOCK_LLM=1` (the
`.env.example` default) is the only mode that runs here. Mock mode is a
first-class code path, not a test fixture: it must never construct or import
a live client, since `anthropic.AsyncAnthropic()` raises when no credentials
are resolvable — the real client is built lazily, only once the mock branch
has been ruled out.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anthropic
from dotenv import load_dotenv

from app.mock_llm import mock_json

__all__ = ["Usage", "RefusalError", "is_mock", "call_json"]

load_dotenv()

REPO_ROOT = Path(__file__).resolve().parent.parent

MODEL = "claude-opus-5"
MAX_TOKENS = 16000

# .env ships with a placeholder so the file documents the variable; a
# placeholder must be treated exactly like an absent key — same convention as
# poc/generate.py's require_api_key().
_PLACEHOLDER_MARKERS = ("replace-me", "replace_me", "your-api-key", "changeme")

_TRUTHY = {"1", "true", "yes"}

# Simulated latency band per effort level (seconds), so the streaming UI
# visibly staggers between a quick low-effort repair and a slower high-effort
# spec extraction, instead of every mock call completing in lockstep.
_MOCK_LATENCY_S: dict[str, tuple[float, float]] = {
    "low": (0.30, 0.45),
    "medium": (0.40, 0.60),
    "high": (0.55, 0.70),
    "xhigh": (0.60, 0.75),
    "max": (0.65, 0.80),
}

# Lazily-constructed module-level client, reused across calls in the real
# branch. Never built in mock mode — construction itself talks to credential
# resolution and raises when none is found.
_client: anthropic.AsyncAnthropic | None = None


class RefusalError(RuntimeError):
    """Raised when the model declines a request (`stop_reason == "refusal"`)."""


@dataclass
class Usage:
    """Token accounting and timing for one `call_json` call."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    latency_ms: int = 0
    mocked: bool = False


def is_mock() -> bool:
    """True when `call_json` will delegate to the mock instead of the API.

    Mock mode is ON when `MOCK_LLM` is truthy ("1"/"true"/"yes" — matched
    case-insensitively), OR when `ANTHROPIC_API_KEY` is absent, empty, or the
    `.env.example` placeholder `sk-ant-replace-me`. This is the one place
    that decision is made; other modules (and the UI) call this instead of
    re-deriving it from the environment.
    """
    if os.environ.get("MOCK_LLM", "").strip().lower() in _TRUTHY:
        return True
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key or any(marker in key.lower() for marker in _PLACEHOLDER_MARKERS):
        return True
    return False


def _get_client() -> anthropic.AsyncAnthropic:
    """Build the module-level async client on first use, then reuse it."""
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic()
    return _client


def _mock_usage(system: str, user: str, result: dict[str, Any], latency_ms: int) -> Usage:
    """Plausible token counts (chars / 4) for a canned mock response."""
    return Usage(
        input_tokens=max(1, len(system + user) // 4),
        output_tokens=max(1, len(json.dumps(result)) // 4),
        cache_read_tokens=0,
        latency_ms=latency_ms,
        mocked=True,
    )


async def _call_mock(system: str, user: str, schema: dict, effort: str) -> tuple[dict, Usage]:
    """Mock branch: canned, schema-shaped JSON with no network call."""
    start = time.monotonic()
    lo, hi = _MOCK_LATENCY_S.get(effort, (0.30, 0.80))
    await asyncio.sleep(random.uniform(lo, hi))

    result = mock_json(system, user, schema)

    latency_ms = int((time.monotonic() - start) * 1000)
    return result, _mock_usage(system, user, result, latency_ms)


async def _call_real(
    system: str,
    user: str,
    schema: dict,
    effort: str,
    cache_system: bool,
) -> tuple[dict, Usage]:
    """Real branch: one `messages.create` with structured output."""
    client = _get_client()

    # Order matters: system is rendered before messages, so the stable
    # prefix (cached across the concurrent per-diagram generator calls)
    # belongs here, never in `messages`, which carries the volatile content.
    system_param: Any = system
    if cache_system:
        system_param = [
            {
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }
        ]

    start = time.monotonic()
    response = await client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        thinking={"type": "adaptive"},
        output_config={
            "effort": effort,
            "format": {"type": "json_schema", "schema": schema},
        },
        system=system_param,
        messages=[{"role": "user", "content": user}],
    )
    latency_ms = int((time.monotonic() - start) * 1000)

    # Check stop_reason before touching content — on a refusal there may be
    # no text block at all.
    if response.stop_reason == "refusal":
        details = response.stop_details
        category = getattr(details, "category", None) or "unspecified"
        raise RefusalError(f"the model refused this request (category={category})")

    text = next((block.text for block in response.content if block.type == "text"), None)
    if text is None:
        raise RefusalError(
            f"no text block in the response (stop_reason={response.stop_reason})"
        )
    result = json.loads(text)

    usage = Usage(
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        cache_read_tokens=response.usage.cache_read_input_tokens or 0,
        latency_ms=latency_ms,
        mocked=False,
    )
    return result, usage


async def call_json(
    system: str,
    user: str,
    schema: dict,
    effort: str = "medium",
    cache_system: bool = True,
) -> tuple[dict, Usage]:
    """Get one structured-output JSON response from Claude, or the mock.

    Mock-vs-real is decided first, via `is_mock()`, before any client is
    touched. In mock mode, `schema`/`system`/`user` are forwarded to
    `app/mock_llm.mock_json` and no network call is made. In real mode, this
    issues one `messages.create` with `output_config.format` set to `schema`
    so the first text block is guaranteed-parseable JSON, and `system` is
    wrapped with an ephemeral cache breakpoint when `cache_system` is True.

    Returns the parsed dict plus a `Usage` recording token counts and
    wall-clock latency. Raises `RefusalError` if the model declines.
    """
    if is_mock():
        return await _call_mock(system, user, schema, effort)
    return await _call_real(system, user, schema, effort, cache_system)


if __name__ == "__main__":

    async def _demo() -> None:
        # Force mock mode explicitly for call 1, rather than relying on the
        # absent key alone, so the verify output is unambiguous about why.
        os.environ["MOCK_LLM"] = "1"

        trivial_schema = {
            "type": "object",
            "properties": {"greeting": {"type": "string"}},
            "required": ["greeting"],
            "additionalProperties": False,
        }
        spec_schema = {
            "type": "object",
            "properties": {
                "entities": {"type": "array", "items": {"type": "string"}},
                "actors": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["entities", "actors"],
            "additionalProperties": False,
        }

        print(f"is_mock() = {is_mock()}")

        print("\n--- call 1: trivial schema, mock mode forced via MOCK_LLM=1 ---")
        result1, usage1 = await call_json(
            system="You are a terse greeter.",
            user="Say hello.",
            schema=trivial_schema,
            effort="low",
        )
        print("result:", result1)
        print("usage:", usage1)

        print(
            "\n--- call 2: spec-shaped schema (entities/actors), exercises "
            "mock_llm's spec dispatch ---"
        )
        result2, usage2 = await call_json(
            system="You are a systems analyst.",
            user=(
                "I am building a compliance monitoring solution which pulls "
                "the latest circulars from SEBI and extracts requirements."
            ),
            schema=spec_schema,
            effort="high",
        )
        print("result:", result2)
        print("usage:", usage2)

        print(
            "\nBranch exercised: MOCK only, both calls. is_mock() is True on "
            "this machine because no real ANTHROPIC_API_KEY is configured "
            "(.env has the sk-ant-replace-me placeholder) and MOCK_LLM=1 was "
            "also forced above. The REAL branch (_call_real / the actual "
            "anthropic.AsyncAnthropic().messages.create call) was NOT "
            "exercised here and cannot be, without a live API key — this "
            "script does not claim otherwise."
        )

    asyncio.run(_demo())
