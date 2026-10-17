"""Reading and writing memory notes, and turning them into evidence M1..Mn (AIENG-50)."""

import uuid
from collections.abc import Sequence
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from incidentops_ai.diagnosis.evidence import Evidence
from incidentops_ai.errors import MemoryAlreadySupersededError, MemoryNotFoundError
from incidentops_ai.memory.orm import MemoryRow
from incidentops_ai.retrieval.chunking import Chunk
from incidentops_ai.retrieval.documents import DocType

RECALL_LIMIT = 3  # notes per diagnosis: newest first


class MemoryCreate(BaseModel):
    """The author is the authenticated caller (W10), never a field of the request."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    service: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=2000)


class MemorySupersede(BaseModel):
    """`text: null` withdraws the note."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    text: str | None = Field(min_length=1, max_length=2000)


class MemoryRead(BaseModel):
    id: uuid.UUID
    service: str
    text: str | None
    author: str
    source_run_id: uuid.UUID | None
    supersedes: uuid.UUID | None
    created_at: datetime


def _read(row: MemoryRow) -> MemoryRead:
    return MemoryRead.model_validate(row, from_attributes=True)


class MemoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        *,
        tenant_id: str,
        service: str,
        text: str,
        author: str,
        source_run_id: uuid.UUID | None = None,
    ) -> MemoryRead:
        row = MemoryRow(
            tenant_id=tenant_id,
            service=service,
            text=text,
            author=author,
            source_run_id=source_run_id,
        )
        self._session.add(row)
        await self._session.flush()
        return _read(row)

    async def supersede(
        self, note_id: uuid.UUID, *, text: str | None, author: str, tenant_id: str | None = None
    ) -> MemoryRead:
        """`tenant_id`, when given, must be the note's: another tenant's note is not found."""
        old = await self._session.get(MemoryRow, note_id)
        if old is None or (tenant_id is not None and old.tenant_id != tenant_id):
            raise MemoryNotFoundError(f"No memory note {note_id}.")
        row = MemoryRow(
            tenant_id=old.tenant_id,
            service=old.service,
            text=text,
            author=author,
            supersedes=old.id,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as error:  # the unique `supersedes`: someone corrected it first
            await self._session.rollback()
            raise MemoryAlreadySupersededError(
                f"Note {note_id} was already superseded; correct its current version."
            ) from error
        return _read(row)

    async def current(self, *, tenant_id: str, service: str, limit: int = 50) -> list[MemoryRead]:
        """Notes nothing supersedes, with text (withdrawals are not notes), newest first."""
        later = aliased(MemoryRow)
        rows = await self._session.execute(
            select(MemoryRow)
            .where(
                MemoryRow.tenant_id == tenant_id,
                MemoryRow.service == service,
                MemoryRow.text.is_not(None),
                ~exists().where(later.supersedes == MemoryRow.id),
            )
            .order_by(MemoryRow.created_at.desc())
            .limit(limit)
        )
        return [_read(r) for r in rows.scalars()]

    async def history(
        self, note_id: uuid.UUID, *, tenant_id: str | None = None
    ) -> list[MemoryRead]:
        """Every version in the note's line, oldest first."""
        row = await self._session.get(MemoryRow, note_id)
        if row is None or (tenant_id is not None and row.tenant_id != tenant_id):
            raise MemoryNotFoundError(f"No memory note {note_id}.")
        line = [row]
        while line[0].supersedes is not None:
            line.insert(0, await self._session.get(MemoryRow, line[0].supersedes))
        while True:
            successor = (
                await self._session.execute(
                    select(MemoryRow).where(MemoryRow.supersedes == line[-1].id)
                )
            ).scalar_one_or_none()
            if successor is None:
                break
            line.append(successor)
        return [_read(r) for r in line]


def memory_evidence(notes: Sequence[dict], *, tenant_id: str) -> list[Evidence]:
    """The recalled notes as evidence M1..Mn, citable like a runbook excerpt."""
    evidence = []
    for number, note in enumerate(notes, start=1):
        chunk = Chunk(
            tenant_id=tenant_id,
            doc_id=f"mem-{note['id']}",
            doc_type=DocType.MEMORY,
            service=note["service"],
            title=f"Note by {note['author']} ({note['created_at'][:10]})",
            source_path=f"memory:{note['id']}",
            chunker="memory-note",
            ordinal=0,
            start=0,
            end=len(note["text"]),
            page=1,
            page_end=1,
            section=None,
            text=note["text"],
        )
        evidence.append(Evidence(f"M{number}", chunk))
    return evidence
