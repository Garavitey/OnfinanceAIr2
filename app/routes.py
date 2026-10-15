"""API routes for the UML generation platform.

POST /v1/generate and POST /v1/render/plantuml are real; the session, version
and feedback handlers are still stubs (M7 and M9 replace their bodies in
place). Mermaid renders in the browser via mermaid.js, but PlantUML has no
browser-side renderer, so the UI depends on the render route to turn PlantUML
source into SVG. Route shapes and SSE event shapes are final: stub bodies get
replaced without changing what callers see on the wire.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

from app.auth import current_user
from app.contracts import DiagramType, FeedbackRequest, GenerateRequest
from app.diagram_types import CAPABILITY
from app.models import User
from app.orchestrator import run_generation

router = APIRouter()

_DEFAULT_PLANTUML_JAR = str(Path(__file__).resolve().parent.parent / "vendor" / "plantuml.jar")
PLANTUML_JAR = os.environ.get("PLANTUML_JAR", _DEFAULT_PLANTUML_JAR)


@router.post("/v1/generate")
async def generate(
    request: GenerateRequest, user: User = Depends(current_user)
) -> StreamingResponse:
    """Stream a real generation job as SSE frames.

    The handler is a thin shell on purpose: `run_generation` is the async
    generator that does the work, and it is handed `user.id` from the auth
    dependency — never a user id from the request body, which would let any
    caller generate (and later, persist) as somebody else.
    """
    return StreamingResponse(
        run_generation(request, user.id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/v1/sessions")
async def list_sessions(user: User = Depends(current_user)) -> list:
    """STUB: replaced by M7."""
    return []


@router.get("/v1/sessions/{session_id}")
async def get_session(session_id: str, user: User = Depends(current_user)) -> Response:
    """STUB: replaced by M7."""
    raise HTTPException(status_code=404, detail="session not found")


@router.get("/v1/diagrams/{diagram_id}/versions")
async def list_diagram_versions(
    diagram_id: str, user: User = Depends(current_user)
) -> list:
    """STUB: replaced by M7."""
    return []


class FeedbackAccepted(BaseModel):
    status: str = "accepted"


@router.post("/v1/feedback", status_code=202)
async def submit_feedback(
    feedback: FeedbackRequest, user: User = Depends(current_user)
) -> FeedbackAccepted:
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
async def render_plantuml(
    request: PlantUMLRenderRequest, user: User = Depends(current_user)
) -> Response:
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
