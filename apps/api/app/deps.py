from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from incidentops_ai.errors import LLMNotConfiguredError
from incidentops_ai.incidents.repository import IncidentRepository
from incidentops_ai.llm.provider import LLMProvider, ModelConfig


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """One DB session per request; closed (and any open transaction rolled back) afterwards."""
    async with request.app.state.session_factory() as session:
        yield session


async def get_incident_repository(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> IncidentRepository:
    return IncidentRepository(session)


IncidentRepo = Annotated[IncidentRepository, Depends(get_incident_repository)]


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    """For work that must not hold a DB connection while waiting on something slow (an LLM
    call): open a short session, close it, do the slow thing, open another."""
    return request.app.state.session_factory


def get_llm_provider(request: Request) -> LLMProvider:
    provider = request.app.state.llm_provider
    if provider is None:
        raise LLMNotConfiguredError("LLM features are off: OPENAI_API_KEY is not configured.")
    return provider


def get_llm_config(request: Request) -> ModelConfig:
    config = request.app.state.llm_config
    if config is None:
        raise LLMNotConfiguredError("LLM features are off: INCIDENTOPS_LLM_MODEL is not set.")
    return config


SessionFactory = Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)]
LLM = Annotated[LLMProvider, Depends(get_llm_provider)]
LLMConfig = Annotated[ModelConfig, Depends(get_llm_config)]
