"""Idempotency keys for LLM runs: a client that retries a request must not be billed twice.

Unlike creating an incident, the work here is slow and costs money, so a key is *claimed before*
the model is called. The primary key on (operation, key) makes the claim race-safe: of two
requests that arrive together, the database lets exactly one insert; the other is told to wait.

Life of a key:
    claimed   run_id is NULL: a request is running the model right now.
    settled   run_id is set, in the same transaction as the run: later requests replay that run.
    released  the row is deleted: the run failed (retrying must really retry) or the request died.

A claim whose request died without releasing it (process killed, connection lost) would block
the key forever, so a claim older than IN_FLIGHT_TTL counts as dead and can be taken over.
Only successful runs are replayed; the key is for "don't pay twice", not for remembering errors.
"""

import uuid
from datetime import datetime, timedelta

from sqlalchemy import DateTime, ForeignKey, String, Uuid, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from incidentops_ai.db import Base
from incidentops_ai.errors import IdempotencyKeyReusedError, RequestInProgressError

# Longer than the slowest run: a tool conversation is up to 3 model turns of 60 s each.
IN_FLIGHT_TTL = timedelta(minutes=5)
RETRY_AFTER_S = 5.0  # what a client told "still running" should wait before asking again


class LLMRunKeyRow(Base):
    __tablename__ = "llm_run_keys"

    # operation namespaces keys per endpoint, like `scope` in idempotency_keys.
    operation: Mapped[str] = mapped_column(String(64), primary_key=True)
    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    incident_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("llm_runs.id", ondelete="CASCADE"))
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


def _this(operation: str, key: str):
    return (LLMRunKeyRow.operation == operation, LLMRunKeyRow.key == key)


async def claim_key(
    session: AsyncSession, *, operation: str, key: str, incident_id: uuid.UUID
) -> uuid.UUID | None:
    """Reserve `key` for a new run. Returns None when reserved: the caller must now call the
    model, then `LLMRunRepository.record(..., idempotency_key=key)`. Returns the id of the
    earlier successful run when there is one to replay.

    Raises IdempotencyKeyReusedError (key already used for another incident) or
    RequestInProgressError (another request holds the key right now).
    """
    for _ in range(3):  # a retry is only needed if the holder releases the key mid-check
        claimed = await session.execute(
            insert(LLMRunKeyRow)
            .values(operation=operation, key=key, incident_id=incident_id)
            .on_conflict_do_nothing()
            .returning(LLMRunKeyRow.key)
        )
        if claimed.first() is not None:
            await session.commit()
            return None

        existing = await session.scalar(select(LLMRunKeyRow).where(*_this(operation, key)))
        if existing is None:
            continue
        if existing.incident_id != incident_id:
            raise IdempotencyKeyReusedError(
                "This Idempotency-Key was already used for another incident. "
                "Use a new key for a new request."
            )
        if existing.run_id is not None:
            return existing.run_id

        # In flight. Take it over only if its holder is long dead; `now()` is the database's
        # clock, the same one that stamped claimed_at.
        taken = await session.execute(
            update(LLMRunKeyRow)
            .where(
                *_this(operation, key),
                LLMRunKeyRow.run_id.is_(None),
                LLMRunKeyRow.claimed_at < func.now() - IN_FLIGHT_TTL,
            )
            .values(claimed_at=func.now(), incident_id=incident_id)
            .returning(LLMRunKeyRow.key)
        )
        if taken.first() is not None:
            await session.commit()
            return None
        await session.rollback()
        raise RequestInProgressError(
            "A request with this Idempotency-Key is still running. "
            "Retry shortly to get its result instead of starting another.",
            RETRY_AFTER_S,
        )
    raise RequestInProgressError("This Idempotency-Key is busy. Retry shortly.", RETRY_AFTER_S)


async def settle_key(
    session: AsyncSession, *, operation: str, key: str, run_id: uuid.UUID, succeeded: bool
) -> None:
    """Called inside the transaction that records the run, so the key and its run appear
    together or not at all. A success keeps the key (pointing at the run); a failure frees it."""
    if succeeded:
        await session.execute(
            update(LLMRunKeyRow).where(*_this(operation, key)).values(run_id=run_id)
        )
    else:
        await session.execute(delete(LLMRunKeyRow).where(*_this(operation, key)))


async def release_key(session: AsyncSession, *, operation: str, key: str) -> None:
    """Free a claim whose request failed before a run was recorded. Never touches a settled key:
    only rows still waiting for a run (run_id NULL) are deleted."""
    await session.execute(
        delete(LLMRunKeyRow).where(*_this(operation, key), LLMRunKeyRow.run_id.is_(None))
    )
    await session.commit()
