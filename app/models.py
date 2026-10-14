"""ORM models for the UML generation platform's persistence layer.

Versioning is append-only: PromptVersion, SystemSpecRow, DiagramVersion, and
Feedback rows are written once and never UPDATEd. The only columns that are
ever mutated in place are the `head_version` pointers on Session and Diagram,
which simply track which version number is "current" for a session/diagram.

Note on naming: SQLAlchemy's own `Session` class (the sync ORM session) and
this app's chat `Session` concept would collide under the same name. The ORM
class here is named `ChatSession`, with `__tablename__ = "session"`, so any
import of `sqlalchemy.orm.Session` elsewhere stays unambiguous.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Text, Uuid, desc, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    """An authenticated end user."""

    __tablename__ = "user"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    external_sub: Mapped[str] = mapped_column(unique=True)
    email: Mapped[str]
    training_consent: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ChatSession(Base):
    """A conversation thread. Table name is "session" (see module docstring)."""

    __tablename__ = "session"
    __table_args__ = (
        Index("ix_session_user_id_created_at", "user_id", desc("created_at")),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("user.id"))
    title: Mapped[str]
    head_version: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class PromptVersion(Base):
    """One immutable turn of user prompt within a session.

    `parent_id` self-references the PromptVersion this one branched from
    (None means it branches from the session's current head).
    """

    __tablename__ = "prompt_version"
    __table_args__ = (
        Index("ix_prompt_version_session_id_version_no", "session_id", "version_no"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("session.id"))
    version_no: Mapped[int]
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("prompt_version.id"), nullable=True
    )
    prompt_text: Mapped[str] = mapped_column(Text)
    diagram_types_requested: Mapped[list] = mapped_column(JSON)
    model_id: Mapped[str]
    model_params: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SystemSpecRow(Base):
    """The extracted SystemSpec produced for one PromptVersion, append-only."""

    __tablename__ = "system_spec"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    prompt_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("prompt_version.id")
    )
    spec: Mapped[dict] = mapped_column(JSON)
    input_tokens: Mapped[int]
    output_tokens: Mapped[int]
    latency_ms: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Diagram(Base):
    """One diagram slot (of a given type) within a session.

    `head_version` is mutated in place to point at the current
    DiagramVersion.version_no for this diagram; the versions themselves are
    append-only.
    """

    __tablename__ = "diagram"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("session.id"))
    diagram_type: Mapped[str]
    head_version: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class DiagramVersion(Base):
    """One immutable, generated-and-validated rendering of a Diagram."""

    __tablename__ = "diagram_version"
    __table_args__ = (
        Index("ix_diagram_version_diagram_id_version_no", "diagram_id", "version_no"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    diagram_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("diagram.id"))
    prompt_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("prompt_version.id")
    )
    version_no: Mapped[int]
    renderer: Mapped[str]
    source_dsl: Mapped[str] = mapped_column(Text)
    status: Mapped[str]
    repair_attempts: Mapped[int] = mapped_column(default=0)
    parse_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    lint_score: Mapped[float] = mapped_column(default=1.0)
    lint_warnings: Mapped[list] = mapped_column(JSON)
    latency_ms: Mapped[int]
    input_tokens: Mapped[int]
    output_tokens: Mapped[int]
    cache_read_tokens: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Feedback(Base):
    """One feedback signal (explicit or implicit) about a DiagramVersion."""

    __tablename__ = "feedback"
    __table_args__ = (
        Index("ix_feedback_training_status_created_at", "training_status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    diagram_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("diagram_version.id")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("user.id"))
    signal_type: Mapped[str]
    rating: Mapped[int | None] = mapped_column(nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    corrected_dsl: Mapped[str | None] = mapped_column(Text, nullable=True)
    implicit: Mapped[dict] = mapped_column(JSON)
    training_status: Mapped[str] = mapped_column(default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
