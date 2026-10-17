"""Evidence files attached to an incident (AIENG-42): upload and list.

Uploading can call the vision model, so these routes sit behind the same token and rate limit as
the other routes that spend model budget.
"""

import uuid
from pathlib import PurePath
from typing import Annotated

from fastapi import APIRouter, Depends, File, Response, UploadFile, status

from app.deps import (
    AttachmentProcessorDep,
    CanReadIncidents,
    CanWriteIncidents,
    SessionFactory,
    authenticate,
    enforce_llm_rate_limit,
)
from app.errors import ERROR_RESPONSES, PROBLEM_JSON, Problem
from incidentops_ai.errors import (
    AttachmentTooLargeError,
    ExtractionUnavailableError,
    InvalidAttachmentError,
    UnsupportedMediaError,
)
from incidentops_ai.incidents.repository import IncidentRepository
from incidentops_ai.multimodal.attachments import (
    MAX_BYTES,
    AttachmentError,
    AttachmentRead,
    AttachmentRepository,
)
from incidentops_ai.multimodal.extract import ExtractionError, detect_media_type

_PROBLEM = {"model": Problem, "content": {PROBLEM_JSON: {}}}
router = APIRouter(
    prefix="/incidents/{incident_id}/attachments",
    tags=["attachments"],
    # Login first; only the upload (which may call a vision model) takes the rate limit.
    dependencies=[Depends(authenticate)],
    responses={
        **ERROR_RESPONSES,
        401: _PROBLEM,
        404: _PROBLEM,
        413: _PROBLEM,
        415: _PROBLEM,
        429: _PROBLEM,
        503: _PROBLEM,
    },
)

_ERRORS = {
    "unsupported_media": UnsupportedMediaError,
    "file_too_large": AttachmentTooLargeError,
    "too_many_pages": InvalidAttachmentError,
    "unreadable_file": InvalidAttachmentError,
}


def _label(filename: str | None) -> str:
    """The uploaded name, reduced to a label: no directories, never used as a path."""
    name = PurePath((filename or "").replace("\\", "/")).name.strip()
    return name[:255] or "attachment"


@router.post(
    "",
    response_model=AttachmentRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(enforce_llm_rate_limit)],
    responses={
        200: {"description": "The same file was already attached; the stored one is returned."}
    },
)
async def upload_attachment(
    incident_id: uuid.UUID,
    principal: CanWriteIncidents,
    sessions: SessionFactory,
    processor: AttachmentProcessorDep,
    response: Response,
    file: Annotated[UploadFile, File(description="A PNG, JPEG or PDF file, at most 5 MB.")],
):
    """Attach a screenshot, chart, diagram or PDF to the incident. Its text is extracted now (PDF
    text layer, OCR, or a vision-model transcript) so the diagnosis can cite it."""
    async with sessions() as session:
        # 404 before reading the file, also for another tenant's incident.
        await IncidentRepository(session).get(incident_id, tenant_id=principal.tenant_id)
    data = await file.read(MAX_BYTES + 1)  # never hold more than one byte past the limit
    try:
        extraction = await processor.process(data)
    except AttachmentError as error:
        raise _ERRORS[error.code](error.code, error.message) from error
    except ExtractionError as error:
        raise ExtractionUnavailableError(
            "The text of this file could not be extracted right now."
        ) from error

    async with sessions() as session:
        stored, created = await AttachmentRepository(session).add(
            tenant_id=principal.tenant_id,
            incident_id=incident_id,
            filename=_label(file.filename),
            media_type=detect_media_type(data),
            data=data,
            extraction=extraction,
        )
        await session.commit()
    if not created:
        response.status_code = status.HTTP_200_OK
    return stored


@router.get("", response_model=list[AttachmentRead])
async def list_attachments(
    incident_id: uuid.UUID, principal: CanReadIncidents, sessions: SessionFactory
):
    """The files attached to the incident, oldest first (without their contents)."""
    async with sessions() as session:
        await IncidentRepository(session).get(incident_id, tenant_id=principal.tenant_id)
        return await AttachmentRepository(session).list_for_incident(incident_id)
