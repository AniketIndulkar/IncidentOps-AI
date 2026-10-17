import secrets
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from incidentops_ai.auth.principal import Principal, can
from incidentops_ai.auth.tokens import InvalidToken
from incidentops_ai.errors import (
    AuthNotConfiguredError,
    ForbiddenError,
    LLMNotConfiguredError,
    OpsNotConfiguredError,
    RateLimitExceededError,
    UnauthorizedError,
)
from incidentops_ai.graph.store import GraphStore
from incidentops_ai.incidents.repository import IncidentRepository
from incidentops_ai.llm.provider import LLMProvider, ModelConfig
from incidentops_ai.multimodal.attachments import AttachmentProcessor
from incidentops_ai.ops.database import OpsDatabase
from incidentops_ai.retrieval.retriever import Retriever
from incidentops_ai.workflow.runner import WorkflowRunner


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


def get_attachment_processor(request: Request) -> AttachmentProcessor:
    return request.app.state.attachment_processor


def get_workflow_runner(request: Request) -> WorkflowRunner:
    runner = request.app.state.workflow_runner
    if runner is None:
        raise LLMNotConfiguredError(
            "The workflow is off: it needs OPENAI_API_KEY and INCIDENTOPS_LLM_MODEL."
        )
    return runner


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
_bearer = HTTPBearer(
    auto_error=False, description="A JWT from the identity provider, or INCIDENTOPS_API_TOKEN."
)

# The static API token is a machine credential (development, automation): admin in the default
# tenant, no expiry. People and services in production present JWTs (W10).
SERVICE_PRINCIPAL = Principal("api-token", "api-token", "default", frozenset({"admin"}))


async def authenticate(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    """Who is calling. Runs before anything else on every route, so a refused caller costs
    nothing and learns nothing (not even whether an incident exists)."""
    token, verifier = request.app.state.api_token, request.app.state.token_verifier
    if token is None and verifier is None:
        raise AuthNotConfiguredError(
            "No caller can be authenticated: set INCIDENTOPS_API_TOKEN or a JWT key."
        )
    supplied = credentials.credentials if credentials else ""
    # compare_digest: the check takes the same time wherever the first wrong character is.
    if token is not None and secrets.compare_digest(
        supplied.encode(), token.get_secret_value().encode()
    ):
        return SERVICE_PRINCIPAL
    if verifier is not None and supplied:
        try:
            return verifier.verify(supplied)
        except InvalidToken:
            pass
    raise UnauthorizedError("A valid bearer token is required.")


def permit(permission: str):
    """A dependency that admits only callers whose roles grant `permission` (403 otherwise)."""

    async def check(principal: Annotated[Principal, Depends(authenticate)]) -> Principal:
        if not can(principal, permission):
            raise ForbiddenError(f"This needs the {permission} permission.")
        return principal

    check.__name__ = f"permit_{permission.replace(':', '_')}"
    return check


CanReadIncidents = Annotated[Principal, Depends(permit("incidents:read"))]
CanWriteIncidents = Annotated[Principal, Depends(permit("incidents:write"))]
CanRunAnalysis = Annotated[Principal, Depends(permit("analysis:run"))]
CanReadTriage = Annotated[Principal, Depends(permit("triage:read"))]
CanRunTriage = Annotated[Principal, Depends(permit("triage:run"))]
CanDecide = Annotated[Principal, Depends(permit("triage:decide"))]
CanResume = Annotated[Principal, Depends(permit("triage:resume"))]
CanReadMemory = Annotated[Principal, Depends(permit("memory:read"))]
CanWriteMemory = Annotated[Principal, Depends(permit("memory:write"))]
CanQueryOps = Annotated[Principal, Depends(permit("ops:query"))]


def get_ops_database(
    request: Request, principal: Annotated[Principal, Depends(authenticate)]
) -> OpsDatabase:
    """The reader for the caller's tenant: its login is the only thing row-level security trusts."""
    database = request.app.state.ops_databases.get(principal.tenant_id)
    if database is None:
        raise OpsNotConfiguredError("No ops database is configured for your tenant.")
    return database


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
AttachmentProcessorDep = Annotated[AttachmentProcessor, Depends(get_attachment_processor)]
WorkflowRunnerDep = Annotated[WorkflowRunner, Depends(get_workflow_runner)]
OpsDatabaseDep = Annotated[OpsDatabase, Depends(get_ops_database)]
