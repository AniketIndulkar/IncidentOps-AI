"""Persistent record of every LLM run: which prompt version and model produced what outcome.

One row per operation (an extraction, a tool conversation), whether it succeeded or failed.
This is the audit trail that later weeks build on: evals compare prompt versions (W11), tracing
and cost dashboards read tokens and latency (W11), and reviews need to know what was asked (W08).
"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import DateTime, ForeignKey, Integer, String, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from incidentops_ai.db import Base
from incidentops_ai.llm.provider import LLMStatus, RunMetadata
from incidentops_ai.llm.run_keys import settle_key
from incidentops_ai.tools.registry import ToolExecution


class LLMRunRow(Base):
    __tablename__ = "llm_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # SET NULL: deleting an incident must not erase the record that a model was called.
    incident_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("incidents.id", ondelete="SET NULL"), index=True
    )
    operation: Mapped[str] = mapped_column(String(64))  # e.g. "extract_incident"
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(128))
    prompt_name: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))
    error_detail: Mapped[str | None] = mapped_column(String(255))
    attempts: Mapped[int] = mapped_column(Integer)
    attempt_statuses: Mapped[list[str]] = mapped_column(JSONB)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int] = mapped_column(Integer)
    request_id: Mapped[str | None] = mapped_column(String(128))
    # none_as_null: store a real SQL NULL, not the JSON value null, so `IS NULL` works in queries.
    tool_calls: Mapped[list[dict] | None] = mapped_column(JSONB(none_as_null=True))
    output: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))  # validated result
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LLMRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    incident_id: uuid.UUID | None
    operation: str
    provider: str
    model: str
    prompt_name: str
    prompt_version: str
    status: LLMStatus
    error_detail: str | None
    attempts: int
    attempt_statuses: list[LLMStatus]
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int
    request_id: str | None
    tool_calls: list[dict] | None
    output: dict | None
    created_at: datetime

    @property
    def metadata(self) -> RunMetadata:
        """The run as an API client first saw it, for replaying a stored result."""
        return RunMetadata(
            provider=self.provider,
            model=self.model,
            prompt_name=self.prompt_name,
            prompt_version=self.prompt_version,
            attempts=self.attempts,
            latency_ms=self.latency_ms,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            request_id=self.request_id,
            attempt_statuses=self.attempt_statuses,
        )


class LLMRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        operation: str,
        incident_id: uuid.UUID | None,
        status: LLMStatus,
        metadata: RunMetadata,
        error_detail: str | None = None,
        tool_executions: list[ToolExecution] | None = None,
        output: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> uuid.UUID:
        """Store the run. With `idempotency_key` (claimed earlier with claim_key) the key is
        settled in the same transaction: kept for a success, freed for a failure."""
        row = LLMRunRow(
            id=uuid.uuid4(),
            incident_id=incident_id,
            operation=operation,
            provider=metadata.provider,
            model=metadata.model,
            prompt_name=metadata.prompt_name,
            prompt_version=metadata.prompt_version,
            status=status,
            error_detail=error_detail,
            attempts=metadata.attempts,
            attempt_statuses=list(metadata.attempt_statuses),
            input_tokens=metadata.input_tokens,
            output_tokens=metadata.output_tokens,
            latency_ms=metadata.latency_ms,
            request_id=metadata.request_id,
            tool_calls=(
                None
                if tool_executions is None
                else [execution.model_dump(mode="json") for execution in tool_executions]
            ),
            output=output,
        )
        self._session.add(row)
        if idempotency_key is not None:
            await self._session.flush()  # the run must exist before a key can point at it
            await settle_key(
                self._session,
                operation=operation,
                key=idempotency_key,
                run_id=row.id,
                succeeded=status is LLMStatus.SUCCEEDED,
            )
        await self._session.commit()
        return row.id

    async def get(self, run_id: uuid.UUID) -> LLMRunRead | None:
        row = await self._session.get(LLMRunRow, run_id)
        return LLMRunRead.model_validate(row) if row else None

    async def list_for_incident(self, incident_id: uuid.UUID) -> list[LLMRunRead]:
        rows = await self._session.scalars(
            select(LLMRunRow)
            .where(LLMRunRow.incident_id == incident_id)
            .order_by(LLMRunRow.created_at.desc())
        )
        return [LLMRunRead.model_validate(row) for row in rows]
