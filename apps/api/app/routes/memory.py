"""Memory notes over HTTP (AIENG-50): list, add, correct or withdraw, and see a note's history.

Notes reach the diagnosis prompt, so the routes need the same bearer token as the model routes.
"""

import uuid

from fastapi import APIRouter, Depends, status

from app.deps import SessionFactory, require_llm_access
from app.errors import ERROR_RESPONSES, PROBLEM_JSON, Problem
from incidentops_ai.memory.repository import (
    MemoryCreate,
    MemoryRead,
    MemoryRepository,
    MemorySupersede,
)
from incidentops_ai.retrieval.documents import DEFAULT_TENANT

_PROBLEM = {"model": Problem, "content": {PROBLEM_JSON: {}}}
router = APIRouter(
    prefix="/memory",
    tags=["memory"],
    dependencies=[Depends(require_llm_access)],
    responses={**ERROR_RESPONSES, 401: _PROBLEM, 404: _PROBLEM, 409: _PROBLEM},
)


@router.get("", response_model=list[MemoryRead])
async def list_memory(service: str, sessions: SessionFactory):
    """The current notes for a service, newest first."""
    async with sessions() as session:
        return await MemoryRepository(session).current(tenant_id=DEFAULT_TENANT, service=service)


@router.post("", response_model=MemoryRead, status_code=status.HTTP_201_CREATED)
async def add_memory(body: MemoryCreate, sessions: SessionFactory):
    async with sessions() as session:
        note = await MemoryRepository(session).add(tenant_id=DEFAULT_TENANT, **body.model_dump())
        await session.commit()
        return note


@router.post("/{note_id}/supersede", response_model=MemoryRead, status_code=status.HTTP_201_CREATED)
async def supersede_memory(note_id: uuid.UUID, body: MemorySupersede, sessions: SessionFactory):
    """Correct a note (new text) or withdraw it (`text: null`). The old version stays."""
    async with sessions() as session:
        note = await MemoryRepository(session).supersede(note_id, **body.model_dump())
        await session.commit()
        return note


@router.get("/{note_id}/history", response_model=list[MemoryRead])
async def memory_history(note_id: uuid.UUID, sessions: SessionFactory):
    async with sessions() as session:
        return await MemoryRepository(session).history(note_id)
