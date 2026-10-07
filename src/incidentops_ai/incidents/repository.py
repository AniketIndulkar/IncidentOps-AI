import uuid
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from incidentops_ai.errors import IdempotencyKeyReusedError, IncidentNotFoundError
from incidentops_ai.idempotency import IdempotencyKeyRow, find_key, request_fingerprint
from incidentops_ai.incidents.models import IncidentCreate, IncidentRead
from incidentops_ai.incidents.orm import IncidentRow

CREATE_SCOPE = "incidents.create"


@dataclass(frozen=True)
class CreateResult:
    incident: IncidentRead
    replayed: bool  # True when an earlier request with the same Idempotency-Key is returned


class IncidentRepository:
    """Persistence boundary for incidents. Callers get Pydantic models, never ORM rows."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self, data: IncidentCreate, idempotency_key: str | None = None
    ) -> CreateResult:
        if idempotency_key is None:
            row = IncidentRow(**data.model_dump())
            self._session.add(row)
            await self._session.commit()
            return CreateResult(IncidentRead.model_validate(row), replayed=False)

        fingerprint = request_fingerprint(data)

        # Fast path: key already used. Avoids a doomed insert in the common retry case.
        if existing := await find_key(self._session, CREATE_SCOPE, idempotency_key):
            return await self._replay(existing, fingerprint)

        row = IncidentRow(id=uuid.uuid4(), **data.model_dump())
        try:
            self._session.add(row)
            # Flush the incident first: the key row has an FK to it, and without an ORM
            # relationship SQLAlchemy does not order the two INSERTs for us.
            await self._session.flush()
            self._session.add(
                IdempotencyKeyRow(
                    scope=CREATE_SCOPE,
                    key=idempotency_key,
                    request_hash=fingerprint,
                    resource_id=row.id,
                )
            )
            await self._session.commit()
        except IntegrityError:
            # Lost a race: a concurrent request with the same key committed first. Our incident
            # insert is rolled back with the key, so no duplicate exists. Replay the winner.
            await self._session.rollback()
            existing = await find_key(self._session, CREATE_SCOPE, idempotency_key)
            if existing is None:  # IntegrityError was something else; don't mask it.
                raise
            return await self._replay(existing, fingerprint)

        return CreateResult(IncidentRead.model_validate(row), replayed=False)

    async def get(self, incident_id: uuid.UUID) -> IncidentRead:
        row = await self._session.get(IncidentRow, incident_id)
        if row is None:
            raise IncidentNotFoundError(f"Incident {incident_id} does not exist.")
        return IncidentRead.model_validate(row)

    async def _replay(self, existing: IdempotencyKeyRow, fingerprint: str) -> CreateResult:
        if existing.request_hash != fingerprint:
            raise IdempotencyKeyReusedError(
                "This Idempotency-Key was already used with a different request body. "
                "Use a new key for a new incident."
            )
        return CreateResult(await self.get(existing.resource_id), replayed=True)
