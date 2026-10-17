"""Memory notes (AIENG-50): what reviewers taught the workflow, per tenant and service.

Append-only, like the audit trail: a correction is a new row that supersedes the old one, and a
withdrawal is a superseding row with no text. A database trigger rejects UPDATE and DELETE, and a
unique `supersedes` means a note has at most one successor, so its history is a single line.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from incidentops_ai.db import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class MemoryRow(Base):
    __tablename__ = "memories"
    __table_args__ = (Index("ix_memories_scope", "tenant_id", "service"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(63))
    service: Mapped[str] = mapped_column(String(128))
    text: Mapped[str | None] = mapped_column(Text)  # None: the note it supersedes is withdrawn
    author: Mapped[str] = mapped_column(Text)
    source_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workflow_runs.id"))
    supersedes: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("memories.id"), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
