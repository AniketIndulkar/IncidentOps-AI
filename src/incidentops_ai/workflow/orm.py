"""One row per workflow run: what an operator lists and queries. The step-by-step state lives in
the LangGraph checkpoint tables, keyed by the run id (the thread id)."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from incidentops_ai.db import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class WorkflowRunRow(Base):
    __tablename__ = "workflow_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)  # also the LangGraph thread id
    tenant_id: Mapped[str] = mapped_column(String(63))
    incident_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(32))  # running | recommended | needs_review | failed
    route: Mapped[str | None] = mapped_column(String(32))
    reasons: Mapped[list] = mapped_column(JSONB, default=list)
    error: Mapped[str | None] = mapped_column(String(128))  # exception type only, never its message
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), onupdate=_utcnow
    )
