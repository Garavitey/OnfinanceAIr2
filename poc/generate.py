"""POC: get structured diagram DSL out of Claude in a single call.

Proves the core generation shape the app is built on: one `messages.create`
with `output_config.format` set to a JSON schema, so the first text block of
the response is guaranteed-parseable JSON carrying the Mermaid source.

Run with: .venv/bin/python poc/generate.py
Requires a real ANTHROPIC_API_KEY in .env — there is no mock path here.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import anthropic
from dotenv import load_dotenv

__all__ = [
    "SEBI_PROMPT",
    "MODEL",
    "MAX_TOKENS",
    "DIAGRAM_SCHEMA",
    "PocError",
    "MISSING_KEY_MESSAGE",
    "require_api_key",
    "first_json_payload",
]

REPO_ROOT = Path(__file__).resolve().parent.parent

MODEL = "claude-opus-5"
MAX_TOKENS = 16000

# The one prompt every POC script in this directory runs against, so results
# are comparable between them. Later POC files import it from here.
SEBI_PROMPT = (
    "I am building a compliance monitoring solution which pulls the latest "
    "circulars from SEBI, parses them into a table of clauses, then extracts "
    "new compliance requirements, performs gap analysis against the existing "
    "compliance setup, and reports IT and operational impact."
)

# Mirrors the fields of contracts.DiagramResult that the model is responsible
# for. `additionalProperties: False` plus a full `required` list is what makes
# the structured-output guarantee strict.
DIAGRAM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "diagram_type": {"type": "string"},
        "renderer": {"type": "string", "enum": ["mermaid", "plantuml"]},
        "source": {"type": "string"},
        "notes": {"type": "string"},
    },
    "required": ["diagram_type", "renderer", "source", "notes"],
    "additionalProperties": False,
}

SYSTEM = """You are a UML diagram generator. You are given a prose description \
of a system and you emit one Mermaid sequence diagram for it.

Rules:
- `diagram_type` is "sequence" and `renderer` is "mermaid".
- `source` must contain ONLY valid Mermaid DSL. It must start with the line \
`sequenceDiagram`. No markdown code fences, no ``` markers, no prose, no \
leading or trailing commentary — the string is fed straight to a Mermaid parser.
- Model the real interaction: every participant the description implies, the \
messages between them in execution order, and the loops/alt blocks that the \
described control flow needs. Prefer a diagram that is faithful to the \
description over one that is short.
- Use `participant` for systems and services and `actor` for humans.
- `notes` is for your assumptions and anything you could not express in the \
diagram. Keep it to a few sentences. Never put DSL in `notes`."""

MISSING_KEY_MESSAGE = "set ANTHROPIC_API_KEY in .env to run the POC"

# .env ships with a placeholder value so the file documents the variable; a
# placeholder must be treated exactly like an absent key, not sent to the API.
_PLACEHOLDER_MARKERS = ("replace-me", "replace_me", "your-api-key", "changeme")


class PocError(RuntimeError):
    """A POC failed for a reason the operator can act on.

    Carries a single human-readable sentence. `main()` prints it and exits
    non-zero instead of letting a traceback through.
    """


def require_api_key() -> str:
    """Load .env and return a usable API key, or raise PocError.

    Treats an unset, empty, or placeholder `ANTHROPIC_API_KEY` as missing.
    """
    load_dotenv(REPO_ROOT / ".env")
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key or any(marker in key.lower() for marker in _PLACEHOLDER_MARKERS):
        raise PocError(MISSING_KEY_MESSAGE)
    return key


def first_json_payload(response: anthropic.types.Message) -> dict[str, Any]:
    """Parse the structured-output JSON out of a response.

    Checks `stop_reason` before touching `content` — on a refusal there may be
    no text block at all, and on `max_tokens` the JSON is truncated.
    """
    if response.stop_reason == "refusal":
        details = response.stop_details
        category = getattr(details, "category", None) or "unspecified"
        raise PocError(f"the model refused this request (category={category})")
    if response.stop_reason == "max_tokens":
        raise PocError(
            f"the response hit max_tokens ({MAX_TOKENS}) and the JSON is "
            "truncated — raise MAX_TOKENS or switch to streaming"
        )

    text = next((block.text for block in response.content if block.type == "text"), None)
    if text is None:
        raise PocError(f"no text block in the response (stop_reason={response.stop_reason})")

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PocError(f"structured output was not valid JSON: {exc}") from exc
    return payload


def main() -> int:
    """Generate one sequence diagram and print its Mermaid source."""
    try:
        client = anthropic.Anthropic(api_key=require_api_key())
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            thinking={"type": "adaptive"},
            output_config={
                "effort": "medium",
                "format": {"type": "json_schema", "schema": DIAGRAM_SCHEMA},
            },
            system=SYSTEM,
            messages=[{"role": "user", "content": SEBI_PROMPT}],
        )
        payload = first_json_payload(response)
    except PocError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except anthropic.AuthenticationError:
        print("error: ANTHROPIC_API_KEY was rejected by the API", file=sys.stderr)
        return 1
    except anthropic.APIStatusError as exc:
        print(f"error: API returned {exc.status_code}: {exc.message}", file=sys.stderr)
        return 1
    except anthropic.APIConnectionError as exc:
        print(f"error: could not reach the API: {exc}", file=sys.stderr)
        return 1

    print(
        f"diagram_type={payload['diagram_type']} renderer={payload['renderer']}\n"
        f"notes={payload['notes']}",
        file=sys.stderr,
    )
    print(payload["source"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
