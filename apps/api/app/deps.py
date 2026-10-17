import secrets
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from incidentops_ai.errors import (
    LLMNotConfiguredError,
    RateLimitExceededError,
    UnauthorizedError,
)
from incidentops_ai.graph.store import GraphStore
from incidentops_ai.incidents.repository import IncidentRepository
from incidentops_ai.llm.provider import LLMProvider, ModelConfig
from incidentops_ai.retrieval.retriever import Retriever


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


def get_retriever(request: Request) -> Retriever:
    retriever = request.app.state.retriever
    if retriever is None:
        raise LLMNotConfiguredError("Evidence retrieval is off: OPENAI_API_KEY is not configured.")
    return retriever


def get_graph_store(request: Request) -> GraphStore | None:
    """Optional: None when no graph is configured. Routes treat the graph as context."""
    return request.app.state.graph_store


def _idempotency_header(description: str):
    # Printable ASCII, no spaces: safe to log and to store; UUIDv4 is the recommended client choice.
    return Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            min_length=1,
            max_length=255,
            pattern=r"^[\x21-\x7e]+$",
            description=description,
        ),
    ]


IdempotencyKey = _idempotency_header(
    "Client-generated key (UUIDv4 recommended). Retrying with the same key and body returns the "
    "original incident instead of creating a duplicate."
)
LLMIdempotencyKey = _idempotency_header(
    "Client-generated key (UUIDv4 recommended). Retrying with the same key returns the first "
    "successful result instead of calling the model (and paying) again. A failed run is not "
    "kept, so retrying after an error really retries. 409 means the first request is still "
    "running: wait for Retry-After and ask again."
)

# auto_error=False: a missing or malformed header reaches us as None, so the refusal is our
# problem+json 401 like every other error, not FastAPI's default body.
_bearer = HTTPBearer(auto_error=False, description="INCIDENTOPS_API_TOKEN of the server.")


async def require_llm_access(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> None:
    """Gate for every endpoint that spends model budget. Runs before anything else on the route,
    so a refused caller costs nothing and learns nothing (not even whether an incident exists)."""
    token = request.app.state.api_token
    if token is None:
        raise LLMNotConfiguredError("LLM features are off: INCIDENTOPS_API_TOKEN is not set.")
    supplied = credentials.credentials if credentials else ""
    # compare_digest: the check takes the same time wherever the first wrong character is.
    if not secrets.compare_digest(supplied.encode(), token.get_secret_value().encode()):
        raise UnauthorizedError("A valid bearer token is required.")


async def enforce_llm_rate_limit(request: Request) -> None:
    """After login, so callers without the token cannot use up the budget of those with it."""
    wait = request.app.state.llm_limiter.hit()
    if wait is not None:
        raise RateLimitExceededError("Too many LLM requests. Retry shortly.", wait)


SessionFactory = Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)]
LLM = Annotated[LLMProvider, Depends(get_llm_provider)]
LLMConfig = Annotated[ModelConfig, Depends(get_llm_config)]
Retrieval = Annotated[Retriever, Depends(get_retriever)]
Graph = Annotated[GraphStore | None, Depends(get_graph_store)]
