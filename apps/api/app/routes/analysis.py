"""LLM-backed analysis of a stored incident. Every call is recorded in `llm_runs`."""

import uuid

from fastapi import APIRouter
from pydantic import BaseModel

from app.deps import LLM, LLMConfig, SessionFactory
from app.errors import ERROR_RESPONSES, PROBLEM_JSON, Problem
from incidentops_ai.errors import LLMCallFailedError
from incidentops_ai.extraction.models import IncidentExtraction
from incidentops_ai.extraction.service import extract_incident
from incidentops_ai.incidents.models import IncidentRead
from incidentops_ai.incidents.repository import IncidentRepository
from incidentops_ai.llm.provider import LLMStatus, RunMetadata
from incidentops_ai.llm.runs import LLMRunRepository
from incidentops_ai.related import find_related_incidents
from incidentops_ai.tools.recent_incidents import make_recent_incidents_tool
from incidentops_ai.tools.registry import ToolExecution, ToolRegistry

_PROBLEM = {"model": Problem, "content": {PROBLEM_JSON: {}}}
router = APIRouter(
    prefix="/incidents/{incident_id}",
    tags=["analysis"],
    responses={**ERROR_RESPONSES, 404: _PROBLEM, 502: _PROBLEM, 504: _PROBLEM},
)


class ExtractionResponse(BaseModel):
    run_id: uuid.UUID
    incident_id: uuid.UUID
    extraction: IncidentExtraction
    run: RunMetadata


class RelatedIncidentsResponse(BaseModel):
    run_id: uuid.UUID
    incident_id: uuid.UUID
    summary: str
    tool_calls: list[ToolExecution]
    run: RunMetadata


async def _load(sessions: SessionFactory, incident_id: uuid.UUID) -> IncidentRead:
    # Short session, closed before the slow LLM call so no DB connection is held meanwhile.
    async with sessions() as session:
        return await IncidentRepository(session).get(incident_id)


def _report_text(incident: IncidentRead) -> str:
    return f"{incident.title}\n\n{incident.description}"


@router.post("/extraction", response_model=ExtractionResponse)
async def extract(incident_id: uuid.UUID, sessions: SessionFactory, llm: LLM, config: LLMConfig):
    """Extract structured data from the incident's text. Takes several seconds."""
    incident = await _load(sessions, incident_id)
    result = await extract_incident(llm, _report_text(incident), config)

    async with sessions() as session:
        run_id = await LLMRunRepository(session).record(
            operation="extract_incident",
            incident_id=incident.id,
            status=result.status,
            metadata=result.metadata,
            error_detail=result.error_detail,
            output=result.value.model_dump(mode="json") if result.value else None,
        )
    if result.status is not LLMStatus.SUCCEEDED or result.value is None:
        raise LLMCallFailedError(result.status, run_id, "Extraction", result.retry_after_s)
    return ExtractionResponse(
        run_id=run_id, incident_id=incident.id, extraction=result.value, run=result.metadata
    )


@router.post("/related-incidents", response_model=RelatedIncidentsResponse)
async def related_incidents(
    incident_id: uuid.UUID, sessions: SessionFactory, llm: LLM, config: LLMConfig
):
    """Ask the model which earlier incidents look related. It looks them up with a read-only
    tool; every tool call it requested, run or rejected, is returned and recorded."""
    incident = await _load(sessions, incident_id)
    registry = ToolRegistry([make_recent_incidents_tool(sessions)])
    result = await find_related_incidents(llm, registry, _report_text(incident), config)

    async with sessions() as session:
        run_id = await LLMRunRepository(session).record(
            operation="related_incidents",
            incident_id=incident.id,
            status=result.status,
            metadata=result.metadata,
            error_detail=result.error_detail,
            tool_executions=result.executions,
            output={"summary": result.final_text} if result.final_text else None,
        )
    if result.status is not LLMStatus.SUCCEEDED or not result.final_text:
        raise LLMCallFailedError(result.status, run_id, "Related-incident lookup")
    return RelatedIncidentsResponse(
        run_id=run_id,
        incident_id=incident.id,
        summary=result.final_text,
        tool_calls=result.executions,
        run=result.metadata,
    )
