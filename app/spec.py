"""Spec extraction: prose system description -> normalised `SystemSpec`.

This is the one genuinely hard reasoning step in the pipeline. Everything
downstream is mechanical translation: the per-diagram generators never see the
user's prose, only this spec, so comprehension is paid for exactly once per
turn instead of once per diagram.

Two consequences shape this module:

* The names in the spec are the names that appear in every diagram. If the
  extractor calls something "Clause Parser" here and "ClauseParser" on the next
  turn, the sequence diagram and the component diagram disagree and the user
  sees two unrelated systems. The system prompt therefore spends most of its
  length on naming discipline.
* The system prompt is a fixed constant and the only per-call variation lives
  in the user message, so the cached prefix hits on every turn of every
  session. A revision passes the prior spec in the user message for the same
  reason — folding it into `system` would invalidate the cache on every edit.
"""

from __future__ import annotations

import json

from app.contracts import DiagramType, SystemSpec
from app.diagram_types import CAPABILITY, DEFAULT_TYPES
from app.llm import Usage, call_json

__all__ = ["SPEC_SCHEMA", "SYSTEM_PROMPT", "extract_spec"]


# JSON schema mirroring SystemSpec field-for-field. `suggested_diagram_types`
# is enum-constrained to the 14 wire values so the model cannot invent a type
# the capability matrix has no renderer for.
SPEC_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "entities": {"type": "array", "items": {"type": "string"}},
        "actors": {"type": "array", "items": {"type": "string"}},
        "services": {"type": "array", "items": {"type": "string"}},
        "flows": {"type": "array", "items": {"type": "string"}},
        "states": {"type": "array", "items": {"type": "string"}},
        "deployment_nodes": {"type": "array", "items": {"type": "string"}},
        "suggested_diagram_types": {
            "type": "array",
            "items": {"type": "string", "enum": [t.value for t in DiagramType]},
        },
    },
    "required": [
        "entities",
        "actors",
        "services",
        "flows",
        "states",
        "deployment_nodes",
        "suggested_diagram_types",
    ],
    "additionalProperties": False,
}


# One line per diagram type, built from the capability matrix so the selection
# guidance can never drift from what the platform can actually render.
_TYPE_MENU = "\n".join(
    f"- {t.value}: {CAPABILITY[t].blurb}"
    + (" (approximated in the target DSL — only pick it if the prompt really calls for it)"
       if CAPABILITY[t].approximated else "")
    for t in DiagramType
)


# Deliberately long and deliberately CONSTANT: this is the cached system prefix
# for every spec call in the system, so length here is paid for once and the
# detail buys naming consistency across diagrams.
SYSTEM_PROMPT = f"""\
You are a staff software architect. Your job is to read a prose description of
a software system and extract a single normalised model of it. You do not draw
diagrams and you do not write code. Downstream generators translate your model
into seven different diagram DSLs, mechanically, without ever seeing the user's
original prose. Your model is the only thing they get.

This has one overriding consequence: EVERY NAME YOU EMIT WILL BE REUSED
VERBATIM. A sequence diagram, a component diagram and a deployment diagram are
generated from your model independently and then shown side by side. If you
name the same thing two different ways, the user sees what looks like two
unrelated systems. So:

- Use the user's own vocabulary wherever they gave you one. If they wrote
  "circular", the entity is "Circular", not "RegulatoryDocument".
- Name each concept exactly once and reuse that spelling everywhere, including
  inside the flow strings. "Clause Parser" in `services` must appear as
  "Clause Parser" in every flow that touches it — never "ClauseParser",
  "the parser", or "Parser Service".
- Be specific. "Service" and "Database" are useless names; "Clause Parser" and
  "Control Register" are useful ones.
- Title Case for multi-word names. No trailing punctuation, no articles ("the
  Clause Parser" is wrong), no numbering prefixes.
- Infer what the prompt obviously implies but do not invent whole subsystems.
  A described HTTP API implies a web tier; it does not imply Kafka, Kubernetes,
  a cache or a CDN unless the user mentioned them or the domain demands them.

Fill the fields as follows.

`entities` — the domain nouns whose structure and lifecycle matter: the things
that get stored, versioned, audited or shown in a list. These become classes
and objects. Singular, Title Case ("Impact Report"). Exclude actors and
services. Aim for 4-8; fewer than 3 means you under-read the prompt.

`actors` — anyone or anything OUTSIDE the system that initiates or receives
interaction: human roles ("Compliance Officer", "Internal Auditor") and
external systems the system talks to ("SEBI Portal", "Payment Gateway").
External systems belong here, not in `services`. Role names, never person
names, and never a job title plus a department.

`services` — the internal runtime components that do the work: the things you
would draw as boxes and deploy independently. Name them by responsibility
("Requirement Extractor", "Gap Analysis Engine"), not by technology ("Lambda",
"Spring app"). Datastores that the system owns belong here too, named for what
they hold ("Control Register"), not for their engine.

`flows` — the ordered interaction steps, each ONE string in exactly this
format: "Source -> Target: imperative description". Source and Target must be
names you already listed in `actors` or `services` — spelled identically. One
step per string, in the order they happen, starting from whatever triggers the
process. Include the replies that matter to the user, and include the error or
fallback path if the prompt mentions one. 6-12 steps is the useful range; this
field is the backbone of the sequence, communication and activity diagrams, so
an empty or vague `flows` degrades three diagrams at once.

`states` — the named lifecycle states of the single most important entity, in
the order it passes through them. Single tokens in PascalCase ("Discovered",
"GapAnalysed", "Acknowledged") because they become state-machine node ids.
Include the terminal and exceptional states ("Quarantined", "Rejected").
Leave this empty ONLY if nothing in the prompt has a lifecycle.

`deployment_nodes` — the physical or virtual places software runs: hosts,
clusters, managed services, buckets, client devices. Name them concretely
("EKS cluster compliance-prod", "RDS Postgres compliance", "S3 bucket
sebi-circulars", "Compliance Analyst Workstation"). Leave empty if the prompt
says nothing about where anything runs — do not guess a cloud provider.

`suggested_diagram_types` — the diagram types this prompt actually warrants,
most valuable first, chosen from exactly these:

{_TYPE_MENU}

Pick 3-6. Be led by evidence in the prompt, not by completeness:
- statuses, a lifecycle, or "pending/approved/rejected" wording -> state_machine
- multi-region, cluster, cloud, on-prem or "deployed on" wording -> deployment
- retries, failure handling, branching business rules -> activity
- several named roles interacting with the system -> use_case
- several named internal components or integrations -> component
- rich domain nouns with attributes and relationships -> class
- a request/response or message-ordering story -> sequence
Always include at least one structural and one behavioural or interaction type,
so the set shows both what the system IS and what it DOES.

Return only the JSON object the schema describes. No prose, no commentary, no
markdown.
"""


def _revision_instruction(prior: SystemSpec) -> str:
    """The user-message preamble that turns a fresh extraction into a patch."""
    return (
        "This is a REVISION of an existing system model, not a new system.\n\n"
        "Here is the model you produced for the previous turn:\n\n"
        f"{prior.model_dump_json(indent=2)}\n\n"
        "PATCH it. Preserve every existing name exactly as spelled above — the "
        "diagrams generated from the previous model are on screen next to the "
        "new ones, and a renamed service reads to the user as a different "
        "system. Keep all prior entries that the change below does not "
        "contradict, add only what the change introduces, and remove an entry "
        "only if the change explicitly retires it. Keep `flows` in execution "
        "order after inserting new steps, rather than appending them at the "
        "end. Extend `suggested_diagram_types` with any type the change now "
        "warrants, keeping the previously suggested ones.\n\n"
        "The user's requested change:\n"
    )


def _coerce_types(raw: list, prior: SystemSpec | None) -> list[DiagramType]:
    """Clean `suggested_diagram_types` into a non-empty, de-duplicated list.

    The schema's enum makes a bad value unlikely but not impossible (mock mode
    and a future model revision both bypass it), and the orchestrator treats
    this list as the diagram set when the request did not name one — so an
    empty list here would silently produce a job with zero diagrams.
    """
    types: list[DiagramType] = []
    for value in raw:
        try:
            diagram_type = DiagramType(value)
        except ValueError:
            continue
        if diagram_type not in types:
            types.append(diagram_type)

    if types:
        return types
    if prior is not None and prior.suggested_diagram_types:
        return list(prior.suggested_diagram_types)
    return list(DEFAULT_TYPES)


async def extract_spec(
    prompt: str, prior: SystemSpec | None = None
) -> tuple[SystemSpec, Usage]:
    """Extract a `SystemSpec` from a prose system description.

    One `call_json` at effort="high" — this is the hard reasoning step, and
    every diagram in the job inherits its mistakes.

    When `prior` is given the call becomes a patch: the previous spec is
    included in the user message and the model is told to preserve existing
    names and add only what the revision introduces, so a minor edit does not
    churn every name in every diagram.

    Returns the validated spec plus the `Usage` for the single call.
    """
    user = prompt if prior is None else _revision_instruction(prior) + prompt

    result, usage = await call_json(
        system=SYSTEM_PROMPT,
        user=user,
        schema=SPEC_SCHEMA,
        effort="high",
    )

    result["suggested_diagram_types"] = _coerce_types(
        result.get("suggested_diagram_types") or [], prior
    )
    return SystemSpec.model_validate(result), usage


if __name__ == "__main__":
    import asyncio

    # The prompt the whole project is demoed with (poc/generate.py's constant).
    SEBI_PROMPT = (
        "I am building a compliance monitoring solution which pulls the latest "
        "circulars from SEBI, parses them into a table of clauses, then extracts "
        "new compliance requirements, performs gap analysis against the existing "
        "compliance setup, and reports IT and operational impact."
    )

    async def _demo() -> None:
        from app.llm import is_mock

        print(f"is_mock() = {is_mock()}")
        print(f"system prompt: {len(SYSTEM_PROMPT)} chars (~{len(SYSTEM_PROMPT) // 4} tokens)")

        spec, usage = await extract_spec(SEBI_PROMPT)
        print("\n--- extract_spec(SEBI_PROMPT) ---")
        print(json.dumps(spec.model_dump(mode="json"), indent=2))
        print("usage:", usage)
        assert spec.suggested_diagram_types, "suggested_diagram_types must be non-empty"
        print(
            "suggested_diagram_types is non-empty:",
            [t.value for t in spec.suggested_diagram_types],
        )

        revised, usage2 = await extract_spec(
            "Also show the retry path when SEBI's circular feed is unavailable.",
            prior=spec,
        )
        print("\n--- extract_spec(revision, prior=spec) ---")
        print("entities preserved:", revised.entities == spec.entities)
        print("suggested:", [t.value for t in revised.suggested_diagram_types])
        print("usage:", usage2)

    asyncio.run(_demo())
