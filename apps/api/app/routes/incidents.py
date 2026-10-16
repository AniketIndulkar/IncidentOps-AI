import uuid

from fastapi import APIRouter, Response, status

from app.deps import IdempotencyKey, IncidentRepo
from app.errors import ERROR_RESPONSES, PROBLEM_JSON, Problem
from incidentops_ai.incidents.models import IncidentCreate, IncidentRead

router = APIRouter(prefix="/incidents", tags=["incidents"], responses=ERROR_RESPONSES)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=IncidentRead)
async def create_incident(
    payload: IncidentCreate,
    repo: IncidentRepo,
    response: Response,
    idempotency_key: IdempotencyKey = None,
):
    result = await repo.create(payload, idempotency_key)
    response.headers["Location"] = f"/incidents/{result.incident.id}"
    if result.replayed:
        response.headers["Idempotent-Replayed"] = "true"
    return result.incident


@router.get(
    "/{incident_id}",
    response_model=IncidentRead,
    responses={404: {"model": Problem, "content": {PROBLEM_JSON: {}}}},
)
async def get_incident(incident_id: uuid.UUID, repo: IncidentRepo):
    return await repo.get(incident_id)
