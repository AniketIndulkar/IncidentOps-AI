"""The degraded mode IncidentOps is in, and each dependency's state (AIENG-72, docs/runbook.md)."""

from fastapi import APIRouter, Request

from app.deps import CanReadIncidents
from app.errors import ERROR_RESPONSES
from incidentops_ai.reliability.status import Status, check

router = APIRouter(tags=["status"], responses=ERROR_RESPONSES)


@router.get("/status", response_model=Status)
async def get_status(request: Request, principal: CanReadIncidents):
    """FULL, REDUCED or MANUAL, with the state of the database, the model routes, retrieval, the
    graph, the CMDB and the ticket system. Cheap checks, at most 2 s each, no model call."""
    return await check(request.app.state, tenant_id=principal.tenant_id)
