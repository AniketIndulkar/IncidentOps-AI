import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from incidentops_ai.incidents.models import IncidentCreate, IncidentRead
from incidentops_ai.incidents.orm import IncidentRow


class IncidentRepository:
    """Persistence boundary for incidents. Callers get Pydantic models, never ORM rows."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, data: IncidentCreate) -> IncidentRead:
        row = IncidentRow(**data.model_dump())
        self._session.add(row)
        await self._session.commit()
        return IncidentRead.model_validate(row)

    async def get(self, incident_id: uuid.UUID) -> IncidentRead | None:
        row = await self._session.get(IncidentRow, incident_id)
        return IncidentRead.model_validate(row) if row else None
