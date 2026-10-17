"""LLM-backed analysis of a stored incident. Every call is recorded in `llm_runs`."""

import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from app.deps import (
    LLM,
    CanRunAnalysis,
    Graph,
    LLMConfig,
    LLMIdempotencyKey,
    Retrieval,
    SessionFactory,
    enforce_llm_rate_limit,
    permit,
)
from app.errors import ERROR_RESPONSES, PROBLEM_JSON, Problem
from incidentops_ai.diagnosis.models import Diagnosis
from incidentops_ai.diagnosis.service import diagnose
from incidentops_ai.errors import LLMCallFailedError, RetrievalFailedError
from incidentops_ai.extraction.models import IncidentExtraction
from incidentops_ai.extraction.review import ReviewDecision, assess
from incidentops_ai.extraction.service import RecordedFields, extract_incident
from incidentops_ai.graph.store import GraphStore, GraphUnavailableError, ServiceContext
from incidentops_ai.incidents.models import IncidentRead
from incidentops_ai.incidents.repository import IncidentRepository
from incidentops_ai.llm.provider import LLMStatus, RunMetadata
from incidentops_ai.llm.run_keys import claim_key, release_key
from incidentops_ai.llm.runs import LLMRunRead, LLMRunRepository
from incidentops_ai.multimodal.attachments import AttachmentRepository, attachment_evidence
from incidentops_ai.related import find_related_incidents
from incidentops_ai.retrieval.embeddings import EmbeddingError
from incidentops_ai.retrieval.rerank import RerankerError
from incidentops_ai.retrieval.retriever import QueryHints
from incidentops_ai.tools.recent_incidents import make_recent_incidents_tool
from incidentops_ai.tools.registry import ToolExecution, ToolRegistry

logger = logging.getLogger(__name__)

EXTRACT_OPERATION = "extract_incident"
RELATED_OPERATION = "related_incidents"
DIAGNOSE_OPERATION = "diagnose_incident"

_PROBLEM = {"model": Problem, "content": {PROBLEM_JSON: {}}}
router = APIRouter(
    prefix="/incidents/{incident_id}",
    tags=["analysis"],
    # Every route here spends model budget: login first, then the rate limit (order matters).
    dependencies=[Depends(permit("analysis:run")), Depends(enforce_llm_rate_limit)],
    responses={
        **ERROR_RESPONSES,
        401: _PROBLEM,
        404: _PROBLEM,
        409: _PROBLEM,
        429: _PROBLEM,
        502: _PROBLEM,
        504: _PROBLEM,
    },
)


class ExtractionResponse(BaseModel):
    run_id: uuid.UUID
    incident_id: uuid.UUID
    extraction: IncidentExtraction
    review: ReviewDecision  # whether a human should confirm this result, and why
    run: RunMetadata


class RelatedIncidentsResponse(BaseModel):
    run_id: uuid.UUID
    incident_id: uuid.UUID
    summary: str
    tool_calls: list[ToolExecution]
    run: RunMetadata


class RetrievalInfo(BaseModel):
    stack: str  # which retriever supplied the evidence
    latency_ms: float


GraphStatus = Literal["off", "ok", "unavailable"]


class DiagnosisResponse(BaseModel):
    run_id: uuid.UUID
    incident_id: uuid.UUID
    diagnosis: Diagnosis  # only causes whose citations the server has checked
    retrieval: RetrievalInfo
    # From the knowledge graph (W05): who owns the service, what it depends on, recent incidents.
    # Structured facts, shown beside the diagnosis, never as citations.
    service_context: ServiceContext | None = None
    graph_status: GraphStatus = "off"
    run: RunMetadata


async def _service_context(
    graph: GraphStore | None, service: str, tenant_id: str
) -> tuple[ServiceContext | None, GraphStatus]:
    if graph is None:
        return None, "off"
    try:
        return await graph.service_context(service, tenant=tenant_id), "ok"
    except GraphUnavailableError as error:
        logger.warning("Service context unavailable: %s", error)
        return None, "unavailable"


async def _load(sessions: SessionFactory, incident_id: uuid.UUID, tenant_id: str) -> IncidentRead:
    # Short session, closed before the slow LLM call so no DB connection is held meanwhile.
    # Another tenant's incident is not found (W10).
    async with sessions() as session:
        return await IncidentRepository(session).get(incident_id, tenant_id=tenant_id)


def _report_text(incident: IncidentRead) -> str:
    return f"{incident.title}\n\n{incident.description}"


async def _claim(
    sessions: SessionFactory, operation: str, incident_id: uuid.UUID, key: str | None
) -> LLMRunRead | None:
    """Reserve the Idempotency-Key before the model is called. Returns the earlier successful
    run to replay, or None: go ahead (no key, or the key is now ours)."""
    if key is None:
        return None
    async with sessions() as session:
        run_id = await claim_key(session, operation=operation, key=key, incident_id=incident_id)
        return None if run_id is None else await LLMRunRepository(session).get(run_id)


@asynccontextmanager
async def _release_claim_on_error(
    sessions: SessionFactory, operation: str, key: str | None
) -> AsyncIterator[None]:
    """If the call or the recording raises, free the key: otherwise the client's retry would be
    told to wait for a request that no longer exists. (A claim that cannot be freed here, say
    the process is killed, expires by itself: see run_keys.IN_FLIGHT_TTL.)"""
    try:
        yield
    except Exception:
        if key is not None:
            try:
                async with sessions() as session:
                    await release_key(session, operation=operation, key=key)
            except Exception:
                logger.exception("Could not release idempotency key for %s", operation)
        raise


@router.post("/extraction", response_model=ExtractionResponse)
async def extract(
    incident_id: uuid.UUID,
    principal: CanRunAnalysis,
    sessions: SessionFactory,
    llm: LLM,
    config: LLMConfig,
    response: Response,
    idempotency_key: LLMIdempotencyKey = None,
):
    """Extract structured data from the incident's text. Takes several seconds."""
    incident = await _load(sessions, incident_id, principal.tenant_id)
    if replay := await _claim(sessions, EXTRACT_OPERATION, incident.id, idempotency_key):
        response.headers["Idempotent-Replayed"] = "true"
        return ExtractionResponse(
            run_id=replay.id,
            incident_id=incident.id,
            extraction=IncidentExtraction.model_validate(replay.output["extraction"]),
            review=ReviewDecision.model_validate(replay.output["review"]),
            run=replay.metadata,
        )

    async with _release_claim_on_error(sessions, EXTRACT_OPERATION, idempotency_key):
        recorded = RecordedFields(
            service=incident.service, severity=incident.severity, environment=incident.environment
        )
        result = await extract_incident(llm, _report_text(incident), config, recorded=recorded)

        review = assess(result.value) if result.value else None
        async with sessions() as session:
            run_id = await LLMRunRepository(session).record(
                operation=EXTRACT_OPERATION,
                incident_id=incident.id,
                status=result.status,
                metadata=result.metadata,
                error_detail=result.error_detail,
                output=(
                    {
                        "extraction": result.value.model_dump(mode="json"),
                        "review": review.model_dump(mode="json"),
                    }
                    if result.value and review
                    else None
                ),
                idempotency_key=idempotency_key,
            )
    if result.status is not LLMStatus.SUCCEEDED or result.value is None or review is None:
        raise LLMCallFailedError(result.status, run_id, "Extraction", result.retry_after_s)
    return ExtractionResponse(
        run_id=run_id,
        incident_id=incident.id,
        extraction=result.value,
        review=review,
        run=result.metadata,
    )


@router.post("/related-incidents", response_model=RelatedIncidentsResponse)
async def related_incidents(
    incident_id: uuid.UUID,
    principal: CanRunAnalysis,
    sessions: SessionFactory,
    llm: LLM,
    config: LLMConfig,
    response: Response,
    idempotency_key: LLMIdempotencyKey = None,
):
    """Ask the model which earlier incidents look related. It looks them up with a read-only
    tool; every tool call it requested, run or rejected, is returned and recorded."""
    incident = await _load(sessions, incident_id, principal.tenant_id)
    if replay := await _claim(sessions, RELATED_OPERATION, incident.id, idempotency_key):
        response.headers["Idempotent-Replayed"] = "true"
        return RelatedIncidentsResponse(
            run_id=replay.id,
            incident_id=incident.id,
            summary=replay.output["summary"],
            tool_calls=[ToolExecution.model_validate(call) for call in replay.tool_calls or []],
            run=replay.metadata,
        )

    async with _release_claim_on_error(sessions, RELATED_OPERATION, idempotency_key):
        tool = make_recent_incidents_tool(
            sessions, exclude_id=incident.id, tenant_id=principal.tenant_id
        )
        registry = ToolRegistry([tool])
        result = await find_related_incidents(llm, registry, _report_text(incident), config)

        async with sessions() as session:
            run_id = await LLMRunRepository(session).record(
                operation=RELATED_OPERATION,
                incident_id=incident.id,
                status=result.status,
                metadata=result.metadata,
                error_detail=result.error_detail,
                tool_executions=result.executions,
                output={"summary": result.final_text} if result.final_text else None,
                idempotency_key=idempotency_key,
            )
    if result.status is not LLMStatus.SUCCEEDED or not result.final_text:
        raise LLMCallFailedError(
            result.status, run_id, "Related-incident lookup", result.retry_after_s
        )
    return RelatedIncidentsResponse(
        run_id=run_id,
        incident_id=incident.id,
        summary=result.final_text,
        tool_calls=result.executions,
        run=result.metadata,
    )


@router.post("/diagnosis", response_model=DiagnosisResponse)
async def diagnose_incident(
    incident_id: uuid.UUID,
    principal: CanRunAnalysis,
    sessions: SessionFactory,
    llm: LLM,
    config: LLMConfig,
    retriever: Retrieval,
    graph: Graph,
    response: Response,
    idempotency_key: LLMIdempotencyKey = None,
):
    """Likely causes of the incident, each backed by a quote from a runbook or postmortem that
    the server has checked against the source. Causes whose citation does not check out are
    dropped; with no usable evidence the answer is an explicit `no_answer`, never a guess."""
    incident = await _load(sessions, incident_id, principal.tenant_id)
    if replay := await _claim(sessions, DIAGNOSE_OPERATION, incident.id, idempotency_key):
        response.headers["Idempotent-Replayed"] = "true"
        return DiagnosisResponse(
            run_id=replay.id,
            incident_id=incident.id,
            diagnosis=Diagnosis.model_validate(replay.output["diagnosis"]),
            retrieval=RetrievalInfo.model_validate(replay.output["retrieval"]),
            service_context=replay.output.get("service_context"),
            graph_status=replay.output.get("graph_status", "off"),
            run=replay.metadata,
        )

    async with _release_claim_on_error(sessions, DIAGNOSE_OPERATION, idempotency_key):
        async with sessions() as session:
            sources = await AttachmentRepository(session).texts_for_incident(incident.id)
        attachments = await attachment_evidence(
            _report_text(incident), sources, tenant_id=principal.tenant_id
        )
        try:
            result = await diagnose(
                llm,
                retriever,
                query=_report_text(incident),
                tenant_id=principal.tenant_id,
                config=config,
                hints=QueryHints(services=(incident.service,)),
                attachments=attachments,
            )
        except (EmbeddingError, RerankerError) as error:
            # The detail may name providers or limits; it goes to the log, not to the client.
            logger.warning("Evidence retrieval failed: %s", error)
            raise RetrievalFailedError(
                "The evidence for this diagnosis could not be fetched. Try again shortly."
            ) from error

        retrieval = RetrievalInfo(stack=result.stack, latency_ms=round(result.retrieval_ms, 1))
        context, graph_status = await _service_context(graph, incident.service, principal.tenant_id)
        async with sessions() as session:
            run_id = await LLMRunRepository(session).record(
                operation=DIAGNOSE_OPERATION,
                incident_id=incident.id,
                status=result.status,
                metadata=result.metadata,
                error_detail=result.error_detail,
                output=(
                    {
                        "diagnosis": result.diagnosis.model_dump(mode="json"),
                        "retrieval": retrieval.model_dump(mode="json"),
                        "service_context": asdict(context) if context else None,
                        "graph_status": graph_status,
                    }
                    if result.diagnosis
                    else None
                ),
                idempotency_key=idempotency_key,
            )
    if result.status is not LLMStatus.SUCCEEDED or result.diagnosis is None:
        raise LLMCallFailedError(result.status, run_id, "Diagnosis", result.retry_after_s)
    return DiagnosisResponse(
        run_id=run_id,
        incident_id=incident.id,
        diagnosis=result.diagnosis,
        retrieval=retrieval,
        service_context=context,
        graph_status=graph_status,
        run=result.metadata,
    )
