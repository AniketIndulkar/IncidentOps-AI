from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from incidentops_ai.incidents.repository import IncidentRepository


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """One DB session per request; closed (and any open transaction rolled back) afterwards."""
    async with request.app.state.session_factory() as session:
        yield session


async def get_incident_repository(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> IncidentRepository:
    return IncidentRepository(session)


IncidentRepo = Annotated[IncidentRepository, Depends(get_incident_repository)]
