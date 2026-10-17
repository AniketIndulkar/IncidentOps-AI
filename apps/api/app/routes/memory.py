"""Memory notes over HTTP (AIENG-50): list, add, correct or withdraw, and see a note's history.

Notes reach the diagnosis prompt, so the routes need the same bearer token as the model routes.
"""

import uuid

from fastapi import APIRouter, Depends, status

from app.deps import CanReadMemory, CanWriteMemory, SessionFactory, authenticate
from app.errors import ERROR_RESPONSES, PROBLEM_JSON, Problem
from incidentops_ai.memory.repository import (
    MemoryCreate,
    MemoryRead,
    MemoryRepository,
    MemorySupersede,
)

_PROBLEM = {"model": Problem, "content": {PROBLEM_JSON: {}}}
router = APIRouter(
    prefix="/memory",
    tags=["memory"],
    dependencies=[Depends(authenticate)],
    responses={**ERROR_RESPONSES, 401: _PROBLEM, 403: _PROBLEM, 404: _PROBLEM, 409: _PROBLEM},
)


@router.get("", response_model=list[MemoryRead])
async def list_memory(service: str, principal: CanReadMemory, sessions: SessionFactory):
    """The current notes for a service in your tenant, newest first."""
    async with sessions() as session:
        return await MemoryRepository(session).current(
            tenant_id=principal.tenant_id, service=service
        )


@router.post("", response_model=MemoryRead, status_code=status.HTTP_201_CREATED)
async def add_memory(body: MemoryCreate, principal: CanWriteMemory, sessions: SessionFactory):
    """Add a note; you are its author."""
    async with sessions() as session:
        note = await MemoryRepository(session).add(
            tenant_id=principal.tenant_id, author=principal.name, **body.model_dump()
        )
        await session.commit()
        return note


@router.post("/{note_id}/supersede", response_model=MemoryRead, status_code=status.HTTP_201_CREATED)
async def supersede_memory(
    note_id: uuid.UUID, body: MemorySupersede, principal: CanWriteMemory, sessions: SessionFactory
):
    """Correct a note (new text) or withdraw it (`text: null`). The old version stays."""
    async with sessions() as session:
        note = await MemoryRepository(session).supersede(
            note_id, text=body.text, author=principal.name, tenant_id=principal.tenant_id
        )
        await session.commit()
        return note


@router.get("/{note_id}/history", response_model=list[MemoryRead])
async def memory_history(note_id: uuid.UUID, principal: CanReadMemory, sessions: SessionFactory):
    async with sessions() as session:
        return await MemoryRepository(session).history(note_id, tenant_id=principal.tenant_id)
