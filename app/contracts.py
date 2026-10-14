"""Shared contracts for the UML generation platform.

This module is the single interface every other module imports from: enums,
request/response models, SSE event frames, and the feedback/reward types.
It contains types only — no business logic, no I/O, no framework imports
beyond pydantic. It is frozen: other steps import from here and never edit it.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

__all__ = [
    "DiagramType",
    "Renderer",
    "DiagramStatus",
    "GenerateOptions",
    "GenerateRequest",
    "SystemSpec",
    "DiagramResult",
    "SSEEvent",
    "JobStarted",
    "SpecReady",
    "DiagramComplete",
    "DiagramFailed",
    "JobComplete",
    "SignalType",
    "FeedbackRequest",
    "RewardBreakdown",
    "TrajectoryRecord",
]


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class DiagramType(StrEnum):
    """The 14 UML 2.x diagram types. Wire value == lowercase member name."""

    # Structural
    CLASS = "class"
    OBJECT = "object"
    COMPONENT = "component"
    COMPOSITE_STRUCTURE = "composite_structure"
    DEPLOYMENT = "deployment"
    PACKAGE = "package"
    PROFILE = "profile"
    # Behavioural
    USE_CASE = "use_case"
    ACTIVITY = "activity"
    STATE_MACHINE = "state_machine"
    # Interaction
    SEQUENCE = "sequence"
    COMMUNICATION = "communication"
    INTERACTION_OVERVIEW = "interaction_overview"
    TIMING = "timing"


class Renderer(StrEnum):
    """Which DSL / rendering backend a diagram's `source` is written for."""

    MERMAID = "mermaid"
    PLANTUML = "plantuml"


class DiagramStatus(StrEnum):
    """Outcome of generating one diagram.

    VALID    — parsed cleanly on the first attempt.
    REPAIRED — failed to parse, then passed after >= 1 repair attempt.
    FAILED   — still unparseable after the repair budget was exhausted.
    """

    VALID = "valid"
    REPAIRED = "repaired"
    FAILED = "failed"


class SignalType(StrEnum):
    """Kind of feedback signal attached to a rendered diagram version."""

    RATING = "rating"
    EDIT = "edit"
    REGENERATE = "regenerate"
    EXPORT = "export"
    ACCEPT = "accept"


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class GenerateOptions(BaseModel):
    """Per-request knobs for a generation job."""

    effort: str = "high"
    stream: bool = True


class GenerateRequest(BaseModel):
    """Body of POST /generate.

    `session_id` is None for the first turn of a conversation and set on every
    follow-up. `parent_version` pins the diagram version being iterated on;
    None means "branch from the latest". `diagram_types` None means "let the
    spec extractor choose" (its `suggested_diagram_types`).
    """

    session_id: UUID | None = None
    parent_version: int | None = None
    prompt: str = Field(max_length=8000)
    diagram_types: list[DiagramType] | None = None
    options: GenerateOptions = Field(default_factory=GenerateOptions)


# ---------------------------------------------------------------------------
# Intermediate representation
# ---------------------------------------------------------------------------


class SystemSpec(BaseModel):
    """Renderer-agnostic description of the system the user described.

    Deliberately flat: this is serialised to JSON and included in the prompt of
    every per-diagram generator call, so nesting costs tokens on every call.
    Each list holds short human-readable names, not structured objects.
    """

    entities: list[str] = Field(default_factory=list)
    actors: list[str] = Field(default_factory=list)
    services: list[str] = Field(default_factory=list)
    flows: list[str] = Field(default_factory=list)
    states: list[str] = Field(default_factory=list)
    deployment_nodes: list[str] = Field(default_factory=list)
    suggested_diagram_types: list[DiagramType] = Field(default_factory=list)


class DiagramResult(BaseModel):
    """One generated diagram plus everything known about its quality.

    `source` is the DSL text for `renderer`. `lint_score` is 1.0 for a clean
    diagram and decreases with each style/quality warning; `parse_error` is set
    only when `status` is FAILED (or on the last failed attempt before repair).
    """

    diagram_type: DiagramType
    renderer: Renderer
    source: str
    notes: str = ""
    status: DiagramStatus
    repair_attempts: int = 0
    parse_error: str | None = None
    lint_score: float = 1.0
    lint_warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# SSE events
# ---------------------------------------------------------------------------


class SSEEvent(BaseModel):
    """Base class for server-sent events on the /generate stream.

    Subclasses declare a `Literal` `event` field holding the wire name, so the
    name travels with the payload and is never typed twice.
    """

    event: str

    def to_sse(self) -> str:
        """Render this event as a single SSE frame.

        Returns exactly ``"event: <name>\\ndata: <json>\\n\\n"``. The payload is
        dumped in JSON mode (UUIDs and enums become strings) which also escapes
        every newline inside string fields — a raw newline in `data:` would
        split the frame and desynchronise the client.
        """
        return f"event: {self.event}\ndata: {self.model_dump_json()}\n\n"


class JobStarted(SSEEvent):
    """First frame: the job is accepted and the diagram set is decided."""

    event: Literal["job_started"] = "job_started"
    job_id: UUID
    version: int
    expected: list[DiagramType]


class SpecReady(SSEEvent):
    """The extracted `SystemSpec`, emitted before any diagram generation."""

    event: Literal["spec_ready"] = "spec_ready"
    spec: SystemSpec


class DiagramComplete(SSEEvent):
    """One diagram finished and parsed (possibly after repair)."""

    event: Literal["diagram_complete"] = "diagram_complete"
    result: DiagramResult


class DiagramFailed(SSEEvent):
    """One diagram exhausted its repair budget and is being dropped."""

    event: Literal["diagram_failed"] = "diagram_failed"
    diagram_type: DiagramType
    error: str
    attempts: int


class JobComplete(SSEEvent):
    """Terminal frame: per-job totals and wall-clock latency."""

    event: Literal["job_complete"] = "job_complete"
    version: int
    succeeded: int
    failed: int
    latency_ms: int


# ---------------------------------------------------------------------------
# Feedback and reward
# ---------------------------------------------------------------------------


class FeedbackRequest(BaseModel):
    """Body of POST /feedback — one signal about one diagram version.

    Which optional fields matter depends on `signal_type`: `rating` for RATING,
    `corrected_dsl` for EDIT, `implicit` for passively observed signals such as
    dwell time or zoom/pan counts.
    """

    diagram_version_id: UUID
    signal_type: SignalType
    rating: int | None = Field(default=None, ge=-1, le=1)
    comment: str | None = None
    corrected_dsl: str | None = None
    implicit: dict = Field(default_factory=dict)


class RewardBreakdown(BaseModel):
    """Per-component reward for one trajectory, each normalised to [0, 1].

    `edit_distance` is a cost (0.0 == the user changed nothing), so it enters
    `total` inverted; every other component is a benefit.
    """

    parse_success: float = 0.0
    lint_score: float = 0.0
    human_rating: float = 0.0
    edit_distance: float = 0.0
    implicit_accept: float = 0.0

    @property
    def total(self) -> float:
        """Weighted scalar reward used for training and for ranking variants."""
        return (
            0.30 * self.parse_success
            + 0.20 * self.lint_score
            + 0.30 * self.human_rating
            + 0.15 * (1 - self.edit_distance)
            + 0.05 * self.implicit_accept
        )


class TrajectoryRecord(BaseModel):
    """One logged LLM step, with its reward, for offline training.

    `stage` names the pipeline step ("spec", "generate", "repair", ...).
    `inputs`/`output` are the raw JSON-able payloads of that call;
    `human_correction` holds the user's corrected DSL when one exists.
    """

    trajectory_id: str
    stage: str
    inputs: dict
    output: dict
    reward: RewardBreakdown
    human_correction: str | None = None
