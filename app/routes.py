"""API routes for the UML generation platform.

Every handler here is a stub returning fixture data except
POST /v1/render/plantuml, which is real: Mermaid renders in the browser via
mermaid.js, but PlantUML has no browser-side renderer, so the UI depends on
this route to turn PlantUML source into SVG. Route shapes and SSE event
shapes are final as of this step — later steps (M6, M7, M9, ...) replace the
stub bodies in place without changing what callers see on the wire.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

from app.contracts import (
    DiagramComplete,
    DiagramResult,
    DiagramStatus,
    DiagramType,
    FeedbackRequest,
    GenerateRequest,
    JobComplete,
    JobStarted,
    Renderer,
    SpecReady,
)
from app.diagram_types import CAPABILITY, DEFAULT_TYPES, renderer_for
from app.fixtures import FIXTURE_DIAGRAMS, FIXTURE_SPEC

router = APIRouter()

_DEFAULT_PLANTUML_JAR = str(Path(__file__).resolve().parent.parent / "vendor" / "plantuml.jar")
PLANTUML_JAR = os.environ.get("PLANTUML_JAR", _DEFAULT_PLANTUML_JAR)

# 2-line valid placeholder per renderer, used for any requested diagram type
# that doesn't have a hand-written fixture source.
_PLACEHOLDER_SOURCE: dict[Renderer, str] = {
    Renderer.MERMAID: "flowchart TD\n    A --> B",
    Renderer.PLANTUML: "@startuml\nA -> B\n@enduml",
}


@router.post("/v1/generate")
async def generate(request: GenerateRequest) -> StreamingResponse:
    """Stream a generation job as SSE frames. STUB: replaced by M6."""

    async def event_stream() -> AsyncIterator[str]:
        start = time.monotonic()
        job_id = uuid4()
        version = 1
        types = request.diagram_types or DEFAULT_TYPES

        yield JobStarted(job_id=job_id, version=version, expected=types).to_sse()
        await asyncio.sleep(0.4)

        yield SpecReady(spec=FIXTURE_SPEC).to_sse()
        await asyncio.sleep(0.4)

        succeeded = 0
        for diagram_type in types:
            source = FIXTURE_DIAGRAMS.get(
                diagram_type, _PLACEHOLDER_SOURCE[renderer_for(diagram_type)]
            )
            result = DiagramResult(
                diagram_type=diagram_type,
                renderer=renderer_for(diagram_type),
                source=source,
                status=DiagramStatus.VALID,
            )
            yield DiagramComplete(result=result).to_sse()
            succeeded += 1
            await asyncio.sleep(0.4)

        latency_ms = int((time.monotonic() - start) * 1000)
        yield JobComplete(
            version=version, succeeded=succeeded, failed=0, latency_ms=latency_ms
        ).to_sse()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/v1/sessions")
async def list_sessions() -> list:
    """STUB: replaced by M7."""
    return []


@router.get("/v1/sessions/{session_id}")
async def get_session(session_id: str) -> Response:
    """STUB: replaced by M7."""
    raise HTTPException(status_code=404, detail="session not found")


@router.get("/v1/diagrams/{diagram_id}/versions")
async def list_diagram_versions(diagram_id: str) -> list:
    """STUB: replaced by M7."""
    return []


class FeedbackAccepted(BaseModel):
    status: str = "accepted"


@router.post("/v1/feedback", status_code=202)
async def submit_feedback(feedback: FeedbackRequest) -> FeedbackAccepted:
    """STUB: replaced by M9."""
    return FeedbackAccepted()


@router.get("/v1/diagram-types")
async def diagram_types() -> list[dict]:
    """The capability matrix as JSON.

    Iterates `DiagramType` in declaration order, which is already
    structure-then-behaviour-then-interaction (see contracts.DiagramType),
    so the UI can group the list by section without re-sorting it.
    """
    return [
        {
            "type": t.value,
            "renderer": CAPABILITY[t].renderer.value,
            "dsl_keyword": CAPABILITY[t].dsl_keyword,
            "blurb": CAPABILITY[t].blurb,
            "approximated": CAPABILITY[t].approximated,
        }
        for t in DiagramType
    ]


class PlantUMLRenderRequest(BaseModel):
    source: str


@router.post("/v1/render/plantuml")
async def render_plantuml(request: PlantUMLRenderRequest) -> Response:
    """Render PlantUML source to SVG. REAL — not a stub.

    Shells out to the PlantUML jar: stdin is the source, stdout is the SVG.
    Exit 0 means the render succeeded; any other exit means stderr holds a
    parse error (see AGENT_RULES.md local overrides: three lines, `ERROR`,
    line number, message — the last two are joined into `parse_error`).
    """
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
    stdout, stderr = await proc.communicate(request.source.encode("utf-8"))

    if proc.returncode == 0:
        return Response(content=stdout, media_type="image/svg+xml")

    stderr_text = stderr.decode("utf-8", errors="replace").strip()
    lines = stderr_text.splitlines()
    parse_error = " ".join(lines[-2:]) if len(lines) >= 2 else stderr_text
    return JSONResponse(status_code=422, content={"parse_error": parse_error})


@router.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}
