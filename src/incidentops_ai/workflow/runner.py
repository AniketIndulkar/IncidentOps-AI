"""Running, recording, gating and resuming workflow runs (AIENG-46, AIENG-49).

A run gets a row in `workflow_runs` before it starts. LangGraph writes a checkpoint after every step
(thread id = run id). If a step raises, the run is marked failed and its checkpoints stay; `resume`
continues from the last completed step, in this process or another, without repeating the steps
that finished (and so without paying for their model calls again).

Every run stops at the approval gate (`awaiting_approval`). Only `decide` moves it on, and only once:
the run row is locked while the decision is recorded. Every transition writes an event to the
append-only `workflow_events` table.

Budgets (AIENG-51): LangGraph's recursion limit caps the steps of one invocation (`max_steps`), and a
run that fails again after `max_resumes` resumes is `abandoned` instead of `failed`.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime

from langgraph.types import Command
from pydantic import BaseModel
from sqlalchemy import func, select

from incidentops_ai.errors import (
    WorkflowAbandonedError,
    WorkflowFailedError,
    WorkflowNotAwaitingDecisionError,
    WorkflowNotResumableError,
    WorkflowRunNotFoundError,
)
from incidentops_ai.incidents.repository import IncidentRepository
from incidentops_ai.memory.repository import MemoryRepository
from incidentops_ai.workflow.graph import Decision, WorkflowDeps, build_workflow, initial_state
from incidentops_ai.workflow.orm import WorkflowEventRow, WorkflowRunRow

MAX_STEPS = 25  # super-steps per invocation; the graph needs about ten
MAX_RESUMES = 3

__all__ = ["Decision", "WorkflowEventRead", "WorkflowRunRead", "WorkflowRunner", "checkpoint_url"]


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


class WorkflowEventRead(BaseModel):
    id: uuid.UUID
    kind: str
    actor: str
    data: dict
    created_at: datetime


class WorkflowRunner:
    def __init__(
        self,
        deps: WorkflowDeps,
        checkpointer,
        *,
        parallel: bool = True,
        prepare: Callable[[], Awaitable[None]] | None = None,
        max_steps: int = MAX_STEPS,
        max_resumes: int = MAX_RESUMES,
    ) -> None:
        """`prepare` runs once, before the first use: the app passes the saver's `setup`, so a
        database that is down at boot fails the first request (503), not the server."""
        self._deps = deps
        self._max_steps = max_steps
        self._max_resumes = max_resumes
        self._graph = build_workflow(deps, checkpointer=checkpointer, parallel=parallel)
        self._prepare = prepare
        self._prepared = asyncio.Lock()

    @property
    def graph(self):
        """The compiled graph (tests use it to try to get past the gate without the runner)."""
        return self._graph

    async def _ready(self) -> None:
        async with self._prepared:
            if self._prepare is not None:
                await self._prepare()
                self._prepare = None

    def _config(self, run_id: uuid.UUID) -> dict:
        return {"configurable": {"thread_id": str(run_id)}, "recursion_limit": self._max_steps}

    async def _resumes(self, run_id: uuid.UUID) -> int:
        async with self._deps.sessions() as session:
            return await session.scalar(
                select(func.count())
                .select_from(WorkflowEventRow)
                .where(WorkflowEventRow.run_id == run_id, WorkflowEventRow.kind == "resumed")
            )

    def _event(self, run_id: uuid.UUID, kind: str, data: dict | None = None, *, actor="system"):
        return WorkflowEventRow(
            tenant_id=self._deps.tenant_id, run_id=run_id, kind=kind, actor=actor, data=data or {}
        )

    async def start(self, incident_id: uuid.UUID) -> WorkflowRunRead:
        await self._ready()
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
            await session.flush()
            session.add(self._event(run.id, "started", {"incident_id": str(incident_id)}))
            await session.commit()
        state = initial_state(incident_id, str(run.id), self._deps.tenant_id)
        return await self._drive(run.id, state)

    async def resume(self, run_id: uuid.UUID) -> WorkflowRunRead:
        """Continue a failed run. A run paused at the gate is not failed: only `decide` moves it."""
        await self._ready()
        async with self._deps.sessions() as session:
            run = await session.get(WorkflowRunRow, run_id, with_for_update=True)
            if run is None:
                raise WorkflowRunNotFoundError(f"No workflow run {run_id}.")
            if run.status != "failed":
                raise WorkflowNotResumableError(f"Run {run_id} is {run.status}, not failed.")
            run.status, run.error = "running", None
            session.add(self._event(run_id, "resumed"))
            await session.commit()
        return await self._drive(run_id, None)  # None: continue from the last checkpoint

    async def decide(self, run_id: uuid.UUID, decision: Decision) -> WorkflowRunRead:
        """Apply a person's decision to a run waiting at the gate, exactly once."""
        await self._ready()
        async with self._deps.sessions() as session:
            # The row lock makes a concurrent second decision wait, then see the run is not waiting.
            run = await session.get(WorkflowRunRow, run_id, with_for_update=True)
            if run is None:
                raise WorkflowRunNotFoundError(f"No workflow run {run_id}.")
            if run.status != "awaiting_approval":
                raise WorkflowNotAwaitingDecisionError(
                    f"Run {run_id} is {run.status}; only a run awaiting approval takes a decision."
                )
            run.status = "running"
            data = {"decision": decision.decision, "comment": decision.comment}
            if decision.memory:  # the reviewer's note for the service, in the same transaction
                incident = await IncidentRepository(session).get(run.incident_id)
                note = await MemoryRepository(session).add(
                    tenant_id=run.tenant_id,
                    service=incident.service,
                    text=decision.memory,
                    author=decision.reviewer,
                    source_run_id=run_id,
                )
                data["memory_id"] = str(note.id)
            event = self._event(run_id, "decided", data, actor=decision.reviewer)
            session.add(event)
            await session.commit()
        resume = {**decision.model_dump(), "decision_id": str(event.id)}
        return await self._drive(run_id, Command(resume=resume))

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

    async def events(self, run_id: uuid.UUID) -> list[WorkflowEventRead]:
        async with self._deps.sessions() as session:
            rows = await session.execute(
                select(WorkflowEventRow)
                .where(WorkflowEventRow.run_id == run_id)
                .order_by(WorkflowEventRow.created_at)
            )
            return [
                WorkflowEventRead.model_validate(r, from_attributes=True) for r in rows.scalars()
            ]

    async def state(self, run_id: uuid.UUID) -> dict:
        await self._ready()
        snapshot = await self._graph.aget_state(self._config(run_id))
        return dict(snapshot.values)

    async def _drive(self, run_id: uuid.UUID, payload) -> WorkflowRunRead:
        try:
            final = await self._graph.ainvoke(payload, self._config(run_id))
        except Exception as error:  # any step that raises stops the run; its checkpoints stay
            name = type(error).__name__
            if await self._resumes(run_id) >= self._max_resumes:
                await self._finish(
                    run_id, status="abandoned", error=name, event=("abandoned", {"error": name})
                )
                raise WorkflowAbandonedError(run_id) from error
            await self._finish(
                run_id, status="failed", error=name, event=("failed", {"error": name})
            )
            raise WorkflowFailedError(run_id) from error
        route, reasons = final.get("route"), final.get("reasons", [])
        if final.get("__interrupt__"):
            return await self._finish(
                run_id,
                status="awaiting_approval",
                route=route,
                reasons=reasons,
                event=("awaiting_approval", {"route": route, "reasons": reasons}),
            )
        return await self._finish(
            run_id,
            status=final["status"],
            route=route,
            reasons=reasons,
            event=("completed", {"status": final["status"]}),
        )

    async def _finish(
        self,
        run_id: uuid.UUID,
        *,
        status: str,
        event: tuple[str, dict],
        route: str | None = None,
        reasons: list[str] | None = None,
        error: str | None = None,
    ) -> WorkflowRunRead:
        async with self._deps.sessions() as session:
            run = await session.get(WorkflowRunRow, run_id)
            if status in {"failed", "abandoned"}:  # a failure keeps what the run had decided
                route, reasons = run.route, run.reasons
            run.status, run.route, run.reasons, run.error = status, route, reasons or [], error
            session.add(self._event(run_id, *event))
            await session.commit()
            return WorkflowRunRead.model_validate(run, from_attributes=True)
