"""The incident workflow over HTTP (AIENG-46): run it, inspect a run, resume a failed one."""

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.deps import WorkflowRunnerDep, enforce_llm_rate_limit, require_llm_access
from app.errors import ERROR_RESPONSES, PROBLEM_JSON, Problem
from incidentops_ai.errors import WorkflowRunNotFoundError
from incidentops_ai.workflow.runner import WorkflowRunner, WorkflowRunRead

_PROBLEM = {"model": Problem, "content": {PROBLEM_JSON: {}}}
router = APIRouter(
    prefix="/incidents/{incident_id}/triage",
    tags=["workflow"],
    # A run makes two model calls: same login and rate limit as the other analysis routes.
    dependencies=[Depends(require_llm_access), Depends(enforce_llm_rate_limit)],
    responses={
        **ERROR_RESPONSES,
        401: _PROBLEM,
        404: _PROBLEM,
        409: _PROBLEM,
        429: _PROBLEM,
        503: _PROBLEM,
    },
)


class TriageResponse(BaseModel):
    run: WorkflowRunRead
    status: str | None
    route: str | None
    reasons: list[str]
    extraction_status: str | None
    retrieval_error: str | None
    graph_status: str | None
    diagnosis: dict | None
    recommendation: dict | None
    service_context: dict | None
    steps: list[dict]  # one entry per step: node, kind (model or deterministic), ms, outcome


async def _response(runner: WorkflowRunner, run: WorkflowRunRead) -> TriageResponse:
    state = await runner.state(run.id)
    return TriageResponse(
        run=run,
        status=state.get("status"),
        route=state.get("route"),
        reasons=state.get("reasons", []),
        extraction_status=state.get("extraction_status"),
        retrieval_error=state.get("retrieval_error"),
        graph_status=state.get("graph_status"),
        diagnosis=state.get("diagnosis"),
        recommendation=state.get("recommendation"),
        service_context=state.get("service_context"),
        steps=state.get("steps", []),
    )


async def _owned(
    runner: WorkflowRunner, incident_id: uuid.UUID, run_id: uuid.UUID
) -> WorkflowRunRead:
    run = await runner.get(run_id)
    if run.incident_id != incident_id:
        raise WorkflowRunNotFoundError(f"No workflow run {run_id} for this incident.")
    return run


@router.post("", response_model=TriageResponse)
async def start_triage(incident_id: uuid.UUID, runner: WorkflowRunnerDep):
    """Run the workflow: retrieve and look up the graph, extract and diagnose, then recommend or
    send to review. Takes several seconds. A run that stops part way answers 500 with its id and
    can be resumed."""
    return await _response(runner, await runner.start(incident_id))


@router.get("", response_model=list[WorkflowRunRead])
async def list_triage_runs(incident_id: uuid.UUID, runner: WorkflowRunnerDep):
    return await runner.list_for_incident(incident_id)


@router.get("/{run_id}", response_model=TriageResponse)
async def get_triage_run(incident_id: uuid.UUID, run_id: uuid.UUID, runner: WorkflowRunnerDep):
    return await _response(runner, await _owned(runner, incident_id, run_id))


@router.post("/{run_id}/resume", response_model=TriageResponse)
async def resume_triage_run(incident_id: uuid.UUID, run_id: uuid.UUID, runner: WorkflowRunnerDep):
    """Continue a failed run from its last completed step. Finished steps are not repeated."""
    await _owned(runner, incident_id, run_id)
    return await _response(runner, await runner.resume(run_id))
