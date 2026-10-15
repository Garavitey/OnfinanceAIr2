"""Job orchestration: one prompt in, a stream of finished diagrams out.

This module owns the latency strategy of the whole platform. Spec extraction
happens once, up front, because every diagram needs it. Everything after that
is embarrassingly parallel, and each diagram is pushed to the client the
instant it parses — not when the slowest one in the set finishes. A job of six
diagrams therefore shows its first diagram in roughly the time of one
generation plus the spec, instead of six generations.

That is why this file uses a completion queue rather than `asyncio.gather`:
gathering and then yielding would produce the identical final state after the
identical total time, while making the user stare at an empty canvas for all
of it.

Persistence is deliberately absent — M7 owns writing specs and diagram
versions, and takes `user_id` from here when it lands.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

from app.contracts import (
    DiagramComplete,
    DiagramFailed,
    DiagramResult,
    DiagramStatus,
    DiagramType,
    GenerateRequest,
    JobComplete,
    JobStarted,
    SpecReady,
    SystemSpec,
)
from app.diagram_types import DEFAULT_TYPES
from app.generate import generate_diagram
from app.spec import extract_spec

__all__ = ["MAX_CONCURRENCY", "run_generation"]


# Concurrent in-flight generation calls per job. Six is the width of the widest
# diagram set we suggest, so a typical job runs fully parallel; the cap exists
# to stop a hand-picked 14-type request from opening 14 sockets at once.
MAX_CONCURRENCY = 6


def _resolve_types(
    requested: list[DiagramType] | None,
    announced: list[DiagramType],
    spec: SystemSpec,
) -> list[DiagramType]:
    """Decide the final diagram set once the spec is known.

    An explicit `diagram_types` in the request always wins — the user ticked
    those boxes. Otherwise the spec's `suggested_diagram_types` replaces the
    provisional default set.

    The announced types are unioned back in rather than dropped: `job_started`
    has already gone out over the wire naming them, the client has already
    drawn a pending tab per type, and silently never resolving one of those
    tabs looks like a hang. Announcing less and delivering more is fine;
    announcing more and delivering less is a bug.
    """
    if requested:
        return list(requested)

    resolved = list(spec.suggested_diagram_types) or list(announced)
    for diagram_type in announced:
        if diagram_type not in resolved:
            resolved.append(diagram_type)
    return resolved


async def _generate_one(
    diagram_type: DiagramType,
    spec: SystemSpec,
    prior_source: str | None,
    semaphore: asyncio.Semaphore,
) -> tuple[DiagramType, DiagramResult | None, Exception | None]:
    """Run one diagram under the concurrency cap, never raising.

    One diagram blowing up must not take the job with it: the exception is
    returned alongside its type so the caller can turn it into a
    `diagram_failed` frame and carry on with the rest of the set.
    """
    async with semaphore:
        try:
            result, _usages = await generate_diagram(diagram_type, spec, prior_source)
            return diagram_type, result, None
        except Exception as exc:  # noqa: BLE001 - every failure mode is per-diagram
            return diagram_type, None, exc


async def run_generation(
    req: GenerateRequest,
    user_id: UUID,
    prior_sources: dict[DiagramType, str] | None = None,
) -> AsyncIterator[str]:
    """Run one generation job, yielding SSE frames as work completes.

    Frame order is `job_started`, `spec_ready`, then one `diagram_complete` or
    `diagram_failed` per diagram **in completion order**, then `job_complete`.

    `prior_sources` carries the previous version of each diagram when this is a
    revision, so the generator can apply the change without re-drawing from
    scratch; M7 populates it from the session's stored versions.
    """
    start = time.monotonic()
    job_id = uuid4()
    # Versioning belongs to the session, which M7 owns; until then every job is
    # the first version of its session.
    version = 1
    prior_sources = prior_sources or {}

    announced = list(req.diagram_types or DEFAULT_TYPES)
    yield JobStarted(job_id=job_id, version=version, expected=announced).to_sse()

    spec, _spec_usage = await extract_spec(req.prompt)
    yield SpecReady(spec=spec).to_sse()

    types = _resolve_types(req.diagram_types, announced, spec)

    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
    pending = {
        asyncio.create_task(
            _generate_one(t, spec, prior_sources.get(t), semaphore),
            name=f"generate:{t.value}",
        )
        for t in types
    }

    succeeded = 0
    failed = 0

    try:
        while pending:
            done, pending = await asyncio.wait(
                pending, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                diagram_type, result, exc = task.result()

                if result is None:
                    # No source at all — a refusal or a crash. Nothing for the
                    # user to salvage, so this is the one genuine drop.
                    failed += 1
                    yield DiagramFailed(
                        diagram_type=diagram_type,
                        error=f"{type(exc).__name__}: {exc}",
                        attempts=0,
                    ).to_sse()
                    continue

                if result.status is DiagramStatus.FAILED:
                    failed += 1
                else:
                    succeeded += 1

                # Even an unparseable result ships as `diagram_complete`: it
                # carries the last source and the parser error, which is what
                # lets the user hand-fix it in the editor. `diagram_failed`
                # would give them a dead tab with nothing in it.
                yield DiagramComplete(result=result).to_sse()
    finally:
        # A disconnected client closes the generator mid-stream; without this
        # the in-flight generation tasks keep burning tokens for nobody.
        for task in pending:
            task.cancel()

    latency_ms = int((time.monotonic() - start) * 1000)
    yield JobComplete(
        version=version, succeeded=succeeded, failed=failed, latency_ms=latency_ms
    ).to_sse()


if __name__ == "__main__":
    import json

    async def _demo() -> None:
        from app.llm import is_mock

        SEBI_PROMPT = (
            "I am building a compliance monitoring solution which pulls the latest "
            "circulars from SEBI, parses them into a table of clauses, then extracts "
            "new compliance requirements, performs gap analysis against the existing "
            "compliance setup, and reports IT and operational impact."
        )

        print(f"is_mock() = {is_mock()}")
        req = GenerateRequest(
            prompt=SEBI_PROMPT,
            diagram_types=[DiagramType.SEQUENCE, DiagramType.COMPONENT, DiagramType.DEPLOYMENT],
        )

        start = time.monotonic()
        first_diagram_at: float | None = None
        async for frame in run_generation(req, uuid4()):
            elapsed = (time.monotonic() - start) * 1000
            name = frame.split("\n", 1)[0].removeprefix("event: ")
            payload = json.loads(frame.split("data: ", 1)[1])
            if name == "diagram_complete":
                if first_diagram_at is None:
                    first_diagram_at = elapsed
                detail = (
                    f"{payload['result']['diagram_type']} "
                    f"status={payload['result']['status']}"
                )
            elif name == "diagram_failed":
                detail = f"{payload['diagram_type']} error={payload['error']}"
            elif name == "spec_ready":
                detail = f"{len(payload['spec']['services'])} services"
            else:
                detail = ""
            print(f"{elapsed:8.0f}ms  {name:18} {detail}")

        print(f"\ntime to first diagram_complete: {first_diagram_at:.0f}ms")

    asyncio.run(_demo())
