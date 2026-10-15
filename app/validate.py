"""Validation service: parse-gate both renderers, then lint the result.

`validate()` is the deterministic parse gate. MERMAID always shells out to
the Node sidecar at validator/validate.mjs (same approach as poc/validate.py,
promoted to a service). PLANTUML defaults to the local jar (see AGENTS.md);
only an explicitly-set KROKI_URL redirects it to Kroki instead. Every
failure mode short of a genuine parse error --
timeout, missing jar, connection refused -- degrades to
`(False, "renderer unavailable: ...")` rather than raising, so a broken
PlantUML backend fails only the 7 PlantUML diagram types instead of crashing
every request.

`lint()` is a best-effort, regex-level semantic check that runs only after a
successful parse. It must never raise on arbitrary model output, so its
entire body is wrapped in a catch-all that degrades to a clean score.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path

import httpx
from dotenv import load_dotenv

from app.contracts import DiagramType, Renderer

load_dotenv()

__all__ = ["validate", "lint"]

REPO_ROOT = Path(__file__).resolve().parent.parent
VALIDATOR_PATH = REPO_ROOT / "validator" / "validate.mjs"

_DEFAULT_PLANTUML_JAR = str(REPO_ROOT / "vendor" / "plantuml.jar")
PLANTUML_JAR = os.environ.get("PLANTUML_JAR", _DEFAULT_PLANTUML_JAR)
KROKI_URL = os.environ.get("KROKI_URL")

_SUBPROCESS_TIMEOUT_S = 10.0
_HTTP_TIMEOUT_S = 10.0


# ---------------------------------------------------------------------------
# Mermaid
# ---------------------------------------------------------------------------


async def _validate_mermaid(source: str) -> tuple[bool, str | None]:
    """Validate Mermaid source via the Node sidecar (validator/validate.mjs).

    Same approach as poc/validate_mermaid, except the path is resolved from
    REPO_ROOT (not cwd) and the subprocess is killed if it runs past the
    timeout, so one pathological diagram can't hang a streaming response.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "node",
            str(VALIDATOR_PATH),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=REPO_ROOT,
        )
    except OSError as exc:
        return False, f"renderer unavailable: {exc}"

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(source.encode()), timeout=_SUBPROCESS_TIMEOUT_S
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return False, "renderer unavailable: mermaid validator timed out"

    if proc.returncode != 0:
        return False, f"validator process failed: {stderr.decode().strip()}"

    try:
        result = json.loads(stdout.decode())
    except json.JSONDecodeError:
        return False, f"validator produced non-JSON output: {stdout.decode()!r}"

    if result.get("ok"):
        return True, None
    return False, result.get("error", "unknown validation error")


# ---------------------------------------------------------------------------
# PlantUML
# ---------------------------------------------------------------------------


async def _validate_plantuml_local(source: str) -> tuple[bool, str | None]:
    """Validate PlantUML source against the local jar.

    exit 0 -> valid, regardless of stderr content (a successful render can
    still print unrelated Java warnings there). exit 200 -> syntax error;
    stderr holds exactly three lines (`ERROR`, line number, message) -- the
    last two become "<line no> <message>". Any other outcome (missing jar,
    timeout, java missing) degrades to "renderer unavailable: ...".
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "java",
            "-jar",
            PLANTUML_JAR,
            "-tsvg",
            "-pipe",
            "-failfast2",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        return False, f"renderer unavailable: {exc}"

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(source.encode("utf-8")), timeout=_SUBPROCESS_TIMEOUT_S
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return False, "renderer unavailable: plantuml jar timed out"

    if proc.returncode == 0:
        return True, None

    stderr_text = stderr.decode("utf-8", errors="replace").strip()

    if proc.returncode == 200:
        lines = stderr_text.splitlines()
        if len(lines) >= 3:
            return False, f"{lines[1]} {lines[2]}"
        return False, stderr_text or "syntax error"

    return False, f"renderer unavailable: plantuml jar exited {proc.returncode}: {stderr_text}"


async def _validate_plantuml_kroki(source: str) -> tuple[bool, str | None]:
    """Validate PlantUML source via a Kroki server (opt-in, KROKI_URL only)."""
    url = f"{KROKI_URL.rstrip('/')}/plantuml/svg"
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
            response = await client.post(url, content=source.encode("utf-8"))
    except httpx.HTTPError as exc:
        return False, f"renderer unavailable: {exc}"

    if response.status_code == 200:
        return True, None
    if response.status_code == 400:
        return False, response.text.strip()
    return False, f"renderer unavailable: kroki returned HTTP {response.status_code}"


async def _validate_plantuml(source: str) -> tuple[bool, str | None]:
    if KROKI_URL:
        return await _validate_plantuml_kroki(source)
    return await _validate_plantuml_local(source)


# ---------------------------------------------------------------------------
# Public entry point: parse gate
# ---------------------------------------------------------------------------


async def validate(renderer: Renderer, source: str) -> tuple[bool, str | None]:
    """Parse-gate `source` against `renderer`'s backend.

    Returns (True, None) if it parses, or (False, <error message>) if it
    doesn't -- including when the backend itself is unavailable.
    """
    if renderer is Renderer.MERMAID:
        return await _validate_mermaid(source)
    return await _validate_plantuml(source)


# ---------------------------------------------------------------------------
# Lint: deterministic semantic checks, run only after a successful parse
# ---------------------------------------------------------------------------

# Keywords that can appear adjacent to arrow-like tokens in Mermaid/PlantUML
# source but are never node identifiers themselves.
_KEYWORDS = {
    "end", "else", "alt", "opt", "loop", "par", "and", "note", "over",
    "activate", "deactivate", "title", "autonumber", "rect", "break",
    "critical", "option", "left", "right", "of", "as", "state",
    "participant", "actor", "class", "object", "node", "component",
    "package", "interface", "enum", "abstract", "namespace", "direction",
    "style", "classdef", "click", "link", "subgraph", "startuml", "enduml",
    "graph", "flowchart", "sequencediagram", "classdiagram", "statediagram",
    "statediagram-v2", "td", "lr", "tb", "rl", "bt",
}

_DECL_RE = re.compile(
    r"(?mi)^\s*(?:class|participant|actor|state|object|node|component)\s+"
    r"([A-Za-z_]\w*)"
)
_BRACKET_RE = re.compile(r"\b([A-Za-z_]\w*)\s*(?:\[[^\]\n]*\]|\([^)\n]*\)|\{)")
_EDGE_RE = re.compile(
    r"\b([A-Za-z_]\w*)\b(?:\[[^\]\n]*\]|\([^)\n]*\)|\"[^\"\n]*\")?"
    r"[ \t]*[-.=<>|*o]{2,6}[ \t]*"
    r"\b([A-Za-z_]\w*)\b"
)
_PARTICIPANT_RE = re.compile(
    r"(?mi)^\s*(?:participant|actor)\s+([A-Za-z_]\w*)(?:\s+as\s+([A-Za-z_]\w*))?"
)
_MESSAGE_RE = re.compile(
    r"(?m)^\s*([A-Za-z_]\w*)\s*[-.]{1,2}[>x]{1,2}\+?\s*([A-Za-z_]\w*)\s*:"
)
_INITIAL_STATE_RE = re.compile(r"\[\*\]\s*-+>")


def _is_keyword(name: str) -> bool:
    return name.lower() in _KEYWORDS


def _extract_graph(source: str) -> tuple[set[str], list[tuple[str, str]]]:
    """Best-effort node/edge extraction shared across Mermaid and PlantUML.

    Nodes come from explicit declarations (`class X`, `participant X`, ...),
    bracketed definitions (`X[label]`, `X(label)`, `X {`), and edge endpoints.
    Edges come from any `A <arrow-ish> B` pair found on a single line.
    """
    nodes: set[str] = set()
    edges: list[tuple[str, str]] = []

    for m in _DECL_RE.finditer(source):
        if not _is_keyword(m.group(1)):
            nodes.add(m.group(1))

    for m in _BRACKET_RE.finditer(source):
        if not _is_keyword(m.group(1)):
            nodes.add(m.group(1))

    for line in source.splitlines():
        for m in _EDGE_RE.finditer(line):
            a, b = m.group(1), m.group(2)
            if _is_keyword(a) or _is_keyword(b):
                continue
            nodes.add(a)
            nodes.add(b)
            edges.append((a, b))

    return nodes, edges


def _sequence_participants(source: str) -> set[str]:
    names: set[str] = set()
    for m in _PARTICIPANT_RE.finditer(source):
        names.add(m.group(2) or m.group(1))
    return names


def lint(diagram_type: DiagramType, source: str) -> tuple[float, list[str]]:
    """Deterministic semantic lint. Call only after `validate()` succeeds.

    Returns a 0.0-1.0 score and a list of warnings; findings here are never
    a hard failure, only a quality signal. Regex-level and defensive by
    design -- it runs on arbitrary model output, so any unexpected exception
    is swallowed and reported as a single skipped-lint warning instead of
    propagating.
    """
    try:
        warnings: list[str] = []
        nodes, edges = _extract_graph(source)

        if len(nodes) < 2:
            warnings.append(
                f"only {len(nodes)} node(s)/participant(s)/class(es) found; "
                "a diagram needs at least 2"
            )

        connected = {n for pair in edges for n in pair}
        orphans = sorted(nodes - connected)
        if orphans and len(nodes) >= 2:
            warnings.append(
                f"orphan node(s) with no edges: {', '.join(orphans)}"
            )

        if diagram_type is DiagramType.SEQUENCE:
            explicit = _sequence_participants(source)
            messages = list(_MESSAGE_RE.finditer(source))
            senders = {m.group(1) for m in messages}
            targets = {m.group(2) for m in messages}
            declared = explicit | senders
            undeclared = sorted(t for t in targets if t not in declared)
            if undeclared:
                warnings.append(
                    "message target(s) never declared as a participant: "
                    + ", ".join(undeclared)
                )

        if diagram_type is DiagramType.STATE_MACHINE:
            if not _INITIAL_STATE_RE.search(source):
                warnings.append("no initial transition (`[*] -->`) found")

        score = max(0.0, 1.0 - 0.25 * len(warnings))
        return score, warnings
    except Exception as exc:  # defensive: lint must never raise
        return 1.0, [f"lint skipped: {exc}"]


# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    GOOD_MERMAID_SEQUENCE = """\
sequenceDiagram
    participant Alice
    participant Bob
    Alice->>Bob: Hello
    Bob-->>Alice: Hi
"""

    BROKEN_MERMAID = """\
sequenceDiagram
  Alice->>>>Bob: broken ][ nonsense
  this is not sequence syntax at all
"""

    GOOD_PLANTUML_OBJECT = """\
@startuml
object Foo
object Bar
Foo --> Bar
@enduml
"""

    BROKEN_PLANTUML = """\
@startuml
this is not valid plantuml at all ][
@enduml
"""

    ONE_BOX_MERMAID = """\
flowchart TD
    A[Only One Box]
"""

    CASES = [
        ("1. valid Mermaid sequenceDiagram", Renderer.MERMAID, DiagramType.SEQUENCE, GOOD_MERMAID_SEQUENCE),
        ("2. broken Mermaid", Renderer.MERMAID, DiagramType.SEQUENCE, BROKEN_MERMAID),
        ("3. valid PlantUML object diagram", Renderer.PLANTUML, DiagramType.OBJECT, GOOD_PLANTUML_OBJECT),
        ("4. broken PlantUML", Renderer.PLANTUML, DiagramType.OBJECT, BROKEN_PLANTUML),
        ("5. one-box Mermaid diagram", Renderer.MERMAID, DiagramType.CLASS, ONE_BOX_MERMAID),
    ]

    async def _main() -> None:
        for label, renderer, diagram_type, source in CASES:
            ok, error = await validate(renderer, source)
            print(f"{label}: validate -> ok={ok} error={error!r}")
            if ok:
                score, lint_warnings = lint(diagram_type, source)
                print(f"{label}: lint     -> score={score} warnings={lint_warnings}")
            else:
                print(f"{label}: lint     -> skipped (parse failed)")
            print()

    asyncio.run(_main())
