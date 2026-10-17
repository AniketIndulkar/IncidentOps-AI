"""Idempotency-key storage for create operations.

A key is recorded in the *same transaction* as the resource it created. The primary key on
(scope, key) is what makes this race-safe: if two requests with the same key run at once, the
database lets exactly one commit, and the loser replays the winner's result.

resource_id currently references incidents only; generalise when a second resource needs keys.
"""

import hashlib
import uuid
from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import DateTime, ForeignKey, String, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from incidentops_ai.db import Base


class IdempotencyKeyRow(Base):
    __tablename__ = "idempotency_keys"

    # Keys are per tenant (W10) and per operation: scope namespaces them, e.g.
    # "incidents.create", so the same client key on two endpoints, or in two tenants, never collides.
    tenant_id: Mapped[str] = mapped_column(
        String(63), primary_key=True, default="default", server_default="default"
    )
    scope: Mapped[str] = mapped_column(String(64), primary_key=True)
    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    resource_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


def request_fingerprint(payload: BaseModel) -> str:
    """SHA-256 of the *validated* payload, so formatting/whitespace differences don't matter."""
    return hashlib.sha256(payload.model_dump_json().encode()).hexdigest()


async def find_key(
    session: AsyncSession, scope: str, key: str, *, tenant_id: str = "default"
) -> IdempotencyKeyRow | None:
    return await session.scalar(
        select(IdempotencyKeyRow).where(
            IdempotencyKeyRow.tenant_id == tenant_id,
            IdempotencyKeyRow.scope == scope,
            IdempotencyKeyRow.key == key,
        )
    )
