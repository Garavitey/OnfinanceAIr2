"""POC: the two-phase generation pipeline, end to end.

Phase 1 extracts a renderer-agnostic spec from the user's prose once. Phase 2
generates every diagram concurrently from that spec JSON — never from the
original prose — so comprehension is paid for once instead of once per diagram.
Each result then goes through the deterministic Mermaid parser, and a parse
failure is repaired by feeding the broken source and the exact parser error
back to the model.

Run with: .venv/bin/python poc/two_phase.py
Requires a real ANTHROPIC_API_KEY in .env — there is no mock path here.
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anthropic

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Imported after the sys.path fix so `python poc/two_phase.py` works the same
# as `python -m poc.two_phase`.
from poc.generate import (  # noqa: E402
    DIAGRAM_SCHEMA,
    MAX_TOKENS,
    MODEL,
    SEBI_PROMPT,
    PocError,
    first_json_payload,
    require_api_key,
)
from poc.validate import validate_mermaid  # noqa: E402

# Phase 2 diagram set. Both are Mermaid, so one validator covers both.
DIAGRAM_TYPES = ["sequence", "component"]

# One generate + at most two repairs.
MAX_ATTEMPTS = 3

# Deliberately flat: this JSON is pasted into the prompt of every phase-2 call,
# so nesting would cost tokens on every one of them.
SPEC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "entities": {"type": "array", "items": {"type": "string"}},
        "actors": {"type": "array", "items": {"type": "string"}},
        "services": {"type": "array", "items": {"type": "string"}},
        "flows": {"type": "array", "items": {"type": "string"}},
        "states": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["entities", "actors", "services", "flows", "states"],
    "additionalProperties": False,
}

SPEC_SYSTEM = """You are a systems analyst. You are given a prose description \
of a software system and you return a structured spec for it.

This spec is the only thing downstream diagram generators will see — they never \
read the original description. So it must stand alone: anything you leave out \
cannot be drawn.

- `entities`: the data/domain objects the system handles.
- `actors`: the humans and external organisations that interact with it.
- `services`: the internal components, jobs, and stores that do the work.
- `flows`: the steps of the main process, in execution order, each written as \
"Source -> Target: action".
- `states`: the lifecycle states a unit of work moves through.

Every element is a short human-readable name or phrase, not a sentence. Infer \
what the description clearly implies, but do not invent subsystems it gives no \
basis for."""

# Shared, byte-stable prefix for every phase-2 call, so the two concurrent
# generations and any repair can hit the same cached prefix.
GENERATOR_SYSTEM = """You are a UML diagram generator. You are given a JSON \
spec of a system and the diagram type to draw, and you emit one Mermaid diagram.

Rules:
- `renderer` is always "mermaid" and `diagram_type` echoes the requested type.
- `source` must contain ONLY valid Mermaid DSL, starting with the opening \
keyword for the requested diagram type. No markdown code fences, no ``` \
markers, no prose — the string is fed straight to a Mermaid parser.
- Use the spec as the full source of truth: draw the elements it lists rather \
than generic placeholders, and follow the ordering of `flows`.
- Quote any label containing a character Mermaid treats as syntax.
- `notes` is for your assumptions and anything the diagram type cannot express. \
Keep it to a few sentences. Never put DSL in `notes`."""

# Per-type instruction. Mermaid has no component diagram, so that one is
# approximated with a flow graph — same mapping as app/diagram_types.py.
DIAGRAM_GUIDANCE = {
    "sequence": (
        "Draw a UML sequence diagram. Open with `sequenceDiagram`. Use `actor` "
        "for the entries in `actors` and `participant` for the entries in "
        "`services`. Turn each entry of `flows` into a message in that order, "
        "and use `loop`/`alt` blocks where the flow implies repetition or a "
        "branch."
    ),
    "component": (
        "Draw a UML component diagram. Mermaid has no native component "
        "diagram, so approximate it with `graph LR`: one node per entry in "
        "`services`, `subgraph` blocks to group related ones, and edges "
        "labelled with the interface or payload that crosses them. Show the "
        "entries in `actors` as nodes on the boundary."
    ),
}

REPAIR_SYSTEM = """You are a Mermaid syntax repair tool. You are given a \
Mermaid diagram that failed to parse and the exact error from the parser.

Return a corrected version of the same diagram. Fix the syntax error and \
nothing else: keep every participant, node, message, and label that was already \
there. Do not simplify the diagram to make it parse.

`source` must contain ONLY the corrected Mermaid DSL — no code fences, no \
prose. Use `notes` to say what you changed."""


@dataclass
class DiagramOutcome:
    """Final state of one diagram after generation, validation, and repair."""

    diagram_type: str
    status: str  # "valid" | "repaired" | "failed"
    attempts: int
    source: str
    parse_error: str | None = None


async def extract_spec(client: anthropic.AsyncAnthropic) -> dict[str, list[str]]:
    """Phase 1 — one high-effort call turning the prose prompt into a spec."""
    response = await client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        thinking={"type": "adaptive"},
        output_config={
            "effort": "high",
            "format": {"type": "json_schema", "schema": SPEC_SCHEMA},
        },
        system=SPEC_SYSTEM,
        messages=[{"role": "user", "content": SEBI_PROMPT}],
    )
    return first_json_payload(response)


async def generate_diagram(
    client: anthropic.AsyncAnthropic, diagram_type: str, spec_json: str
) -> dict[str, Any]:
    """Phase 2 — one call for one diagram type, driven by the spec JSON.

    The spec block carries the cache breakpoint and is identical across the
    concurrent calls, so everything up to the per-type instruction is a shared
    prefix. (At this spec size the prefix is likely under the ~1024-token
    caching minimum, so a hit is not guaranteed — the layout is what matters
    once real specs are bigger.)
    """
    response = await client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        thinking={"type": "adaptive"},
        output_config={
            "effort": "medium",
            "format": {"type": "json_schema", "schema": DIAGRAM_SCHEMA},
        },
        system=GENERATOR_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": f"System spec:\n{spec_json}",
                        "cache_control": {"type": "ephemeral"},
                    },
                    {"type": "text", "text": DIAGRAM_GUIDANCE[diagram_type]},
                ],
            }
        ],
    )
    return first_json_payload(response)


async def repair_diagram(
    client: anthropic.AsyncAnthropic,
    diagram_type: str,
    broken_source: str,
    parse_error: str,
) -> dict[str, Any]:
    """Re-call at low effort with the broken source and the parser's error."""
    response = await client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        thinking={"type": "adaptive"},
        output_config={
            "effort": "low",
            "format": {"type": "json_schema", "schema": DIAGRAM_SCHEMA},
        },
        system=REPAIR_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Diagram type: {diagram_type}\n\n"
                    f"Parser error:\n{parse_error}\n\n"
                    f"Broken Mermaid source:\n{broken_source}"
                ),
            }
        ],
    )
    return first_json_payload(response)


async def validate_and_repair(
    client: anthropic.AsyncAnthropic,
    diagram_type: str,
    spec_json: str,
    payload: dict[str, Any],
) -> DiagramOutcome:
    """Phase 3 — parse-gate one generated diagram, repairing on failure.

    The generation that produced `payload` counts as attempt 1, so there are
    `MAX_ATTEMPTS - 1` repairs available.
    """
    source = payload["source"]
    attempts = 1

    ok, error = await validate_mermaid(source)
    if ok:
        return DiagramOutcome(diagram_type, "valid", attempts, source)

    while attempts < MAX_ATTEMPTS:
        print(f"[{diagram_type}] attempt {attempts} failed to parse: {error}")
        payload = await repair_diagram(client, diagram_type, source, error or "")
        source = payload["source"]
        attempts += 1

        ok, error = await validate_mermaid(source)
        if ok:
            return DiagramOutcome(diagram_type, "repaired", attempts, source)

    return DiagramOutcome(diagram_type, "failed", attempts, source, error)


def print_summary(outcomes: list[DiagramOutcome]) -> None:
    """Print the per-diagram result table."""
    header = f"{'diagram_type':<16}{'status':<12}{'attempts':>8}{'lines':>8}"
    print("\n" + header)
    print("-" * len(header))
    for outcome in outcomes:
        lines = len(outcome.source.splitlines())
        print(
            f"{outcome.diagram_type:<16}{outcome.status:<12}"
            f"{outcome.attempts:>8}{lines:>8}"
        )
    failed = [o for o in outcomes if o.status == "failed"]
    for outcome in failed:
        print(f"\n[{outcome.diagram_type}] last parser error: {outcome.parse_error}")


async def run(api_key: str) -> int:
    """Run the full pipeline once against SEBI_PROMPT."""
    async with anthropic.AsyncAnthropic(api_key=api_key) as client:
        print("phase 1: extracting spec...")
        spec = await extract_spec(client)
        # sort_keys keeps this string byte-identical across calls and runs,
        # which is what makes the cached prefix reusable.
        spec_json = json.dumps(spec, indent=2, sort_keys=True)
        print(spec_json)

        print(f"\nphase 2: generating {', '.join(DIAGRAM_TYPES)} concurrently...")
        payloads = await asyncio.gather(
            *(generate_diagram(client, dt, spec_json) for dt in DIAGRAM_TYPES)
        )

        print("\nphase 3: validating and repairing...")
        outcomes = await asyncio.gather(
            *(
                validate_and_repair(client, dt, spec_json, payload)
                for dt, payload in zip(DIAGRAM_TYPES, payloads)
            )
        )

    print_summary(list(outcomes))
    return 0 if all(o.status != "failed" for o in outcomes) else 1


def main() -> int:
    """Resolve the API key, then run the pipeline."""
    try:
        api_key = require_api_key()
        return asyncio.run(run(api_key))
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


if __name__ == "__main__":
    sys.exit(main())
