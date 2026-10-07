import uuid

from fastapi import APIRouter, HTTPException, Response, status

from app.deps import IncidentRepo
from incidentops_ai.incidents.models import IncidentCreate, IncidentRead

router = APIRouter(prefix="/incidents", tags=["incidents"])


@router.post("", status_code=status.HTTP_201_CREATED, response_model=IncidentRead)
async def create_incident(payload: IncidentCreate, repo: IncidentRepo, response: Response):
    incident = await repo.create(payload)
    response.headers["Location"] = f"/incidents/{incident.id}"
    return incident


@router.get("/{incident_id}", response_model=IncidentRead)
async def get_incident(incident_id: uuid.UUID, repo: IncidentRepo):
    incident = await repo.get(incident_id)
    if incident is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    return incident
