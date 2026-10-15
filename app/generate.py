"""Per-diagram generation: `SystemSpec` -> validated DSL for one diagram type.

One call to `generate_diagram` owns the full lifecycle of a single diagram:
pick the renderer and its prompt fragment, ask the model for DSL, run the real
parser over the answer, and — if the parser rejects it — hand the model its own
broken source plus the exact parser error and ask again, up to a hard budget of
`MAX_ATTEMPTS` calls.

Three details here are load-bearing rather than stylistic:

* `SHARED_RULES` comes first in `system` and is byte-identical for every
  diagram type, so the concurrent fan-out in the orchestrator shares one cached
  prompt prefix instead of paying full input price per diagram.
* The spec is serialised with sorted keys. Python dict ordering is stable but
  not guaranteed to match across turns, and a reordered JSON body is a
  different prefix to the cache.
* A diagram that never parses is still returned, with its last source and the
  parser error. A broken diagram the user can hand-fix beats an empty tab, and
  their fix is the strongest training signal the platform collects.
"""

from __future__ import annotations

import json

from app.contracts import (
    DiagramResult,
    DiagramStatus,
    DiagramType,
    Renderer,
    SystemSpec,
)
from app.diagram_types import CAPABILITY
from app.llm import Usage, call_json
from app.templates import mermaid as mermaid_templates
from app.templates import plantuml as plantuml_templates
from app.validate import lint, validate

__all__ = ["MAX_ATTEMPTS", "DIAGRAM_SCHEMA", "SHARED_RULES", "generate_diagram"]


# One generate call plus at most two repair calls. Raising this buys very
# little: a source that survives two targeted repairs is usually wrong in a way
# the parser error does not describe.
MAX_ATTEMPTS = 3

DIAGRAM_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "diagram_type": {"type": "string", "enum": [t.value for t in DiagramType]},
        "renderer": {"type": "string", "enum": [r.value for r in Renderer]},
        "source": {"type": "string"},
        "notes": {"type": "string"},
    },
    "required": ["diagram_type", "renderer", "source", "notes"],
    "additionalProperties": False,
}


# Byte-identical across every diagram type and every attempt — the cache prefix
# the whole fan-out shares. Append-only: editing this text invalidates the
# cached prefix for every in-flight session.
SHARED_RULES = """\
You translate an already-extracted model of a software system into one diagram,
written in a specific diagram DSL. The hard thinking has been done for you: the
model below is authoritative. Your job is faithful, syntactically valid
translation, not redesign.

Rules that apply to every diagram type:
- Use the model's names VERBATIM. Copy the spelling, casing and spacing of each
  entity, actor and service exactly as given. Other diagrams in this same set
  are generated from the same model in parallel and shown beside yours; a
  renamed service makes the set read as two unrelated systems. Where the DSL
  needs a single-token id, keep the given name as the display label and derive
  a short uppercase id from it.
- Cover the model, not your own additions. Do not invent services, actors,
  states or infrastructure that the model does not list. If the model is thin
  for your diagram type, draw a smaller diagram rather than padding it.
- Include every element that is relevant to YOUR diagram type and ignore the
  rest. A deployment diagram does not need the class attributes; a class
  diagram does not need the deployment nodes.
- Respect the direction of each flow: "A -> B: does something" means the arrow
  starts at A, ends at B, and is labelled with the action.
- Aim for 4-12 nodes. One or two nodes is not a diagram; twenty is unreadable.
- The `source` field must contain the diagram DSL and nothing else: no markdown
  fences, no explanation, no leading or trailing blank lines.
- Put any caveat, assumption or approximation you made in the `notes` field, as
  one or two plain sentences. Never put prose in `source`.
- Set `diagram_type` and `renderer` to exactly the values named in your
  instructions below.

"""


def _template_for(diagram_type: DiagramType) -> str:
    """Return the per-type prompt fragment from the renderer's template module."""
    renderer = CAPABILITY[diagram_type].renderer
    if renderer is Renderer.MERMAID:
        return mermaid_templates.get_template(diagram_type)
    return plantuml_templates.get_template(diagram_type)


def _spec_json(spec: SystemSpec) -> str:
    """The spec as sorted-key JSON — stable bytes for the prompt cache."""
    return json.dumps(spec.model_dump(mode="json"), sort_keys=True, indent=2)


def _generate_user_message(
    diagram_type: DiagramType, spec: SystemSpec, prior_source: str | None
) -> str:
    """User message for a fresh (or revising) generation attempt.

    The explicit `diagram_type:` line leads the message: it is what the model
    keys its two enum fields off, and in mock mode it is also the signal
    `app/mock_llm._detect_diagram_type` dispatches on — a bare mention further
    down would lose to a key inside the spec JSON.
    """
    renderer = CAPABILITY[diagram_type].renderer
    parts = [
        f"diagram_type: {diagram_type.value}",
        f"renderer: {renderer.value}",
        "",
        "System model:",
        _spec_json(spec),
    ]

    if prior_source is not None:
        parts += [
            "",
            "You produced this version of the same diagram on the previous turn:",
            prior_source,
            "",
            "The model above has since been revised. Apply the revision to that "
            "diagram and preserve everything unrelated to it: keep the existing "
            "nodes, ids, labels, ordering and layout as they are, and change "
            "only what the revision actually touches. A minor edit that "
            "reshuffles the whole diagram is worse than no edit — the user has "
            "to re-read it from scratch.",
        ]

    return "\n".join(parts)


def _repair_user_message(
    diagram_type: DiagramType, spec: SystemSpec, source: str, parse_error: str
) -> str:
    """User message for a repair attempt: the broken source plus the exact error.

    Phrased to carry the parser's verbatim message — the word-for-word error is
    what makes a low-effort repair work at all, and it is also how mock mode
    tells a repair apart from a fresh generation.
    """
    renderer = CAPABILITY[diagram_type].renderer
    return "\n".join(
        [
            f"diagram_type: {diagram_type.value}",
            f"renderer: {renderer.value}",
            "",
            f"This {renderer.value} source failed to parse:",
            source,
            "",
            "The parser reported this error, verbatim:",
            parse_error,
            "",
            "Return the corrected source. Fix the syntax error and keep the "
            "content identical — same nodes, same edges, same labels. Do not "
            "simplify the diagram to make it parse, and do not re-draw it from "
            "scratch. If the error names a line number, look there first.",
            "",
            "System model, for reference:",
            _spec_json(spec),
        ]
    )


async def generate_diagram(
    diagram_type: DiagramType,
    spec: SystemSpec,
    prior_source: str | None = None,
) -> tuple[DiagramResult, list[Usage]]:
    """Generate, validate and (if needed) repair one diagram.

    Returns the result plus one `Usage` per LLM call made, in call order, so
    the caller can account for the repair calls separately from the first one.

    Status is VALID when the first attempt parsed, REPAIRED when a later
    attempt did (`repair_attempts` counts the extra calls), and FAILED when the
    budget ran out — in which case `source` still holds the last attempt and
    `parse_error` holds why the parser rejected it.
    """
    renderer = CAPABILITY[diagram_type].renderer
    system = SHARED_RULES + _template_for(diagram_type)
    usages: list[Usage] = []

    user = _generate_user_message(diagram_type, spec, prior_source)
    effort = "medium"
    source = ""
    notes = ""
    parse_error: str | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        result, usage = await call_json(
            system=system, user=user, schema=DIAGRAM_SCHEMA, effort=effort
        )
        usages.append(usage)

        source = (result.get("source") or "").strip()
        # Keep the first attempt's notes: a repair call's notes describe the
        # fix, which is not what the user wants to read about the diagram.
        notes = notes or (result.get("notes") or "")

        ok, parse_error = await validate(renderer, source)
        if ok:
            lint_score, lint_warnings = lint(diagram_type, source)
            return (
                DiagramResult(
                    diagram_type=diagram_type,
                    renderer=renderer,
                    source=source,
                    notes=notes,
                    status=(
                        DiagramStatus.VALID if attempt == 1 else DiagramStatus.REPAIRED
                    ),
                    repair_attempts=attempt - 1,
                    parse_error=None,
                    lint_score=lint_score,
                    lint_warnings=lint_warnings,
                ),
                usages,
            )

        # Parser rejected it: next attempt is a cheap, targeted repair.
        user = _repair_user_message(
            diagram_type, spec, source, parse_error or "unknown parse error"
        )
        effort = "low"

    # Budget exhausted. Return the last source anyway so the user can fix it in
    # the editor; lint is not meaningful on source that does not parse, so it is
    # reported as zero rather than left at its optimistic default.
    return (
        DiagramResult(
            diagram_type=diagram_type,
            renderer=renderer,
            source=source,
            notes=notes,
            status=DiagramStatus.FAILED,
            repair_attempts=MAX_ATTEMPTS - 1,
            parse_error=parse_error,
            lint_score=0.0,
            lint_warnings=["not linted: source does not parse"],
        ),
        usages,
    )


if __name__ == "__main__":
    import asyncio

    async def _demo() -> None:
        from app.llm import is_mock

        spec = SystemSpec(
            entities=["SEBI Circular", "Clause", "Compliance Requirement"],
            actors=["Compliance Officer", "SEBI Portal"],
            services=["Circular Ingestion Service", "Clause Parser", "Control Register"],
            flows=[
                "Compliance Officer -> Circular Ingestion Service: trigger circular sync",
                "Circular Ingestion Service -> SEBI Portal: poll for new circulars",
                "Circular Ingestion Service -> Clause Parser: parse circular into clauses",
                "Clause Parser -> Control Register: compare against existing controls",
                "Control Register -> Compliance Officer: return gap summary",
            ],
            states=["Discovered", "Parsing", "Parsed", "Acknowledged"],
            deployment_nodes=["EKS cluster compliance-prod", "RDS Postgres compliance"],
        )

        print(f"is_mock() = {is_mock()}")
        print(f"shared prefix: {len(SHARED_RULES)} chars (~{len(SHARED_RULES) // 4} tokens)\n")

        for diagram_type in (DiagramType.SEQUENCE, DiagramType.DEPLOYMENT):
            result, usages = await generate_diagram(diagram_type, spec)
            print(f"--- {diagram_type.value} ({result.renderer.value}) ---")
            print(f"status:         {result.status.value}")
            print(f"repair_attempts:{result.repair_attempts}")
            print(f"llm calls:      {len(usages)}")
            print(f"lint:           {result.lint_score} {result.lint_warnings}")
            print(f"parse_error:    {result.parse_error}")
            print(f"source ({len(result.source.splitlines())} lines):")
            print(result.source)
            print()

    asyncio.run(_demo())
