"""Running, recording and resuming workflow runs (AIENG-46).

A run gets a row in `workflow_runs` before it starts. LangGraph writes a checkpoint after every step
(thread id = run id). If a step raises, the run is marked failed and its checkpoints stay; `resume`
continues from the last completed step, in this process or another, without repeating the steps
that finished (and so without paying for their model calls again).
"""

import uuid
from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import select

from incidentops_ai.errors import (
    WorkflowFailedError,
    WorkflowNotResumableError,
    WorkflowRunNotFoundError,
)
from incidentops_ai.incidents.repository import IncidentRepository
from incidentops_ai.workflow.graph import WorkflowDeps, build_workflow, initial_state
from incidentops_ai.workflow.orm import WorkflowRunRow


def checkpoint_url(database_url: str) -> str:
    """The checkpointer speaks psycopg; the app's URL names the asyncpg driver."""
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


class WorkflowRunRead(BaseModel):
    id: uuid.UUID
    incident_id: uuid.UUID
    status: str
    route: str | None
    reasons: list[str]
    error: str | None
    created_at: datetime
    updated_at: datetime


class WorkflowRunner:
    def __init__(self, deps: WorkflowDeps, checkpointer, *, parallel: bool = True) -> None:
        self._deps = deps
        self._graph = build_workflow(deps, checkpointer=checkpointer, parallel=parallel)

    @staticmethod
    def _config(run_id: uuid.UUID) -> dict:
        return {"configurable": {"thread_id": str(run_id)}}

    async def start(self, incident_id: uuid.UUID) -> WorkflowRunRead:
        async with self._deps.sessions() as session:
            await IncidentRepository(session).get(incident_id)  # 404 before anything is recorded
            run = WorkflowRunRow(
                id=uuid.uuid4(),
                tenant_id=self._deps.tenant_id,
                incident_id=incident_id,
                status="running",
                reasons=[],
            )
            session.add(run)
            await session.commit()
        state = initial_state(incident_id, str(run.id), self._deps.tenant_id)
        return await self._drive(run.id, state)

    async def resume(self, run_id: uuid.UUID) -> WorkflowRunRead:
        async with self._deps.sessions() as session:
            run = await session.get(WorkflowRunRow, run_id)
            if run is None:
                raise WorkflowRunNotFoundError(f"No workflow run {run_id}.")
            if run.status != "failed":
                raise WorkflowNotResumableError(f"Run {run_id} is {run.status}, not failed.")
            run.status, run.error = "running", None
            await session.commit()
        return await self._drive(run_id, None)  # None: continue from the last checkpoint

    async def get(self, run_id: uuid.UUID) -> WorkflowRunRead:
        async with self._deps.sessions() as session:
            run = await session.get(WorkflowRunRow, run_id)
            if run is None:
                raise WorkflowRunNotFoundError(f"No workflow run {run_id}.")
            return WorkflowRunRead.model_validate(run, from_attributes=True)

    async def list_for_incident(self, incident_id: uuid.UUID) -> list[WorkflowRunRead]:
        async with self._deps.sessions() as session:
            rows = await session.execute(
                select(WorkflowRunRow)
                .where(WorkflowRunRow.incident_id == incident_id)
                .order_by(WorkflowRunRow.created_at)
            )
            return [WorkflowRunRead.model_validate(r, from_attributes=True) for r in rows.scalars()]

    async def state(self, run_id: uuid.UUID) -> dict:
        snapshot = await self._graph.aget_state(self._config(run_id))
        return dict(snapshot.values)

    async def _drive(self, run_id: uuid.UUID, state: dict | None) -> WorkflowRunRead:
        try:
            final = await self._graph.ainvoke(state, self._config(run_id))
        except Exception as error:  # any step that raises stops the run; its checkpoints stay
            await self._finish(run_id, status="failed", error=type(error).__name__)
            raise WorkflowFailedError(run_id) from error
        return await self._finish(
            run_id,
            status=final["status"],
            route=final.get("route"),
            reasons=final.get("reasons", []),
        )

    async def _finish(
        self,
        run_id: uuid.UUID,
        *,
        status: str,
        route: str | None = None,
        reasons: list[str] | None = None,
        error: str | None = None,
    ) -> WorkflowRunRead:
        async with self._deps.sessions() as session:
            run = await session.get(WorkflowRunRow, run_id)
            run.status, run.route, run.reasons, run.error = status, route, reasons or [], error
            await session.commit()
            return WorkflowRunRead.model_validate(run, from_attributes=True)
