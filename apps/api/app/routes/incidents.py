import uuid
from typing import Annotated

from fastapi import APIRouter, Header, Response, status

from app.deps import IncidentRepo
from app.errors import ERROR_RESPONSES, PROBLEM_JSON, Problem
from incidentops_ai.incidents.models import IncidentCreate, IncidentRead

router = APIRouter(prefix="/incidents", tags=["incidents"], responses=ERROR_RESPONSES)

# Printable ASCII, no spaces: safe to log and to store; UUIDv4 is the recommended client choice.
IdempotencyKey = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=255,
        pattern=r"^[\x21-\x7e]+$",
        description="Client-generated key (UUIDv4 recommended). Retrying with the same key and "
        "body returns the original incident instead of creating a duplicate.",
    ),
]


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
