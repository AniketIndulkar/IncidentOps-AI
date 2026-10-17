"""The incident workflow over HTTP (AIENG-46, AIENG-49): run it, inspect a run and its audit
trail, decide at the approval gate, resume a failed run."""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app.deps import (
    CanDecide,
    CanReadTriage,
    CanResume,
    CanRunTriage,
    WorkflowRunnerDep,
    authenticate,
    enforce_llm_rate_limit,
)
from app.errors import ERROR_RESPONSES, PROBLEM_JSON, Problem
from incidentops_ai.auth.principal import Principal
from incidentops_ai.errors import WorkflowRunNotFoundError
from incidentops_ai.workflow.runner import (
    Decision,
    WorkflowEventRead,
    WorkflowRunner,
    WorkflowRunRead,
)

_PROBLEM = {"model": Problem, "content": {PROBLEM_JSON: {}}}
router = APIRouter(
    prefix="/incidents/{incident_id}/triage",
    tags=["workflow"],
    # Login first. Only the routes that call a model take the rate limit (after login, before
    # their own permission check): reading a run or deciding must never wait on model budget.
    dependencies=[Depends(authenticate)],
    responses={
        **ERROR_RESPONSES,
        401: _PROBLEM,
        403: _PROBLEM,
        404: _PROBLEM,
        409: _PROBLEM,
        429: _PROBLEM,
        503: _PROBLEM,
    },
)


class DecisionRequest(BaseModel):
    """The reviewer is the authenticated caller (W10): there is no field to say otherwise."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    decision: Literal["approve", "reject"]
    comment: str | None = Field(default=None, max_length=2000)
    memory: str | None = Field(default=None, max_length=2000)


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
    proposed_action: dict | None  # the ticket awaiting (or given) approval
    decision: dict | None
    action_result: dict | None
    steps: list[dict]  # one entry per step: node, kind (model, deterministic, human), ms, outcome


async def _response(runner: WorkflowRunner, run: WorkflowRunRead) -> TriageResponse:
    state = await runner.state(run.id)
    return TriageResponse(
        run=run,
        status=run.status,  # the state says "running" at the gate; the run row knows it waits
        route=state.get("route"),
        reasons=state.get("reasons", []),
        extraction_status=state.get("extraction_status"),
        retrieval_error=state.get("retrieval_error"),
        graph_status=state.get("graph_status"),
        diagnosis=state.get("diagnosis"),
        recommendation=state.get("recommendation"),
        service_context=state.get("service_context"),
        proposed_action=state.get("proposed_action"),
        decision=state.get("decision"),
        action_result=state.get("action_result"),
        steps=state.get("steps", []),
    )


async def _owned(
    runner: WorkflowRunner, principal: Principal, incident_id: uuid.UUID, run_id: uuid.UUID
) -> WorkflowRunRead:
    """The run, if it is this incident's and in the caller's tenant; not found otherwise."""
    run = await runner.get(run_id, tenant_id=principal.tenant_id)
    if run.incident_id != incident_id:
        raise WorkflowRunNotFoundError(f"No workflow run {run_id} for this incident.")
    return run


@router.post("", response_model=TriageResponse, dependencies=[Depends(enforce_llm_rate_limit)])
async def start_triage(incident_id: uuid.UUID, principal: CanRunTriage, runner: WorkflowRunnerDep):
    """Run the workflow: retrieve and look up the graph, extract and diagnose, then recommend or
    send to review, and stop at the approval gate (`awaiting_approval`) with a proposed ticket.
    Takes several seconds. A run that stops part way answers 500 with its id and can be resumed."""
    run = await runner.start(incident_id, tenant_id=principal.tenant_id)
    return await _response(runner, run)


@router.get("", response_model=list[WorkflowRunRead])
async def list_triage_runs(
    incident_id: uuid.UUID, principal: CanReadTriage, runner: WorkflowRunnerDep
):
    return await runner.list_for_incident(incident_id, tenant_id=principal.tenant_id)


@router.get("/{run_id}", response_model=TriageResponse)
async def get_triage_run(
    incident_id: uuid.UUID, run_id: uuid.UUID, principal: CanReadTriage, runner: WorkflowRunnerDep
):
    return await _response(runner, await _owned(runner, principal, incident_id, run_id))


@router.post(
    "/{run_id}/resume",
    response_model=TriageResponse,
    dependencies=[Depends(enforce_llm_rate_limit)],
)
async def resume_triage_run(
    incident_id: uuid.UUID, run_id: uuid.UUID, principal: CanResume, runner: WorkflowRunnerDep
):
    """Continue a failed run from its last completed step. Finished steps are not repeated."""
    await _owned(runner, principal, incident_id, run_id)
    return await _response(runner, await runner.resume(run_id, tenant_id=principal.tenant_id))


@router.post("/{run_id}/decision", response_model=TriageResponse)
async def decide_triage_run(
    incident_id: uuid.UUID,
    run_id: uuid.UUID,
    body: DecisionRequest,
    principal: CanDecide,
    runner: WorkflowRunnerDep,
):
    """Approve or reject the proposed action of a run waiting at the gate. You are the reviewer:
    the audit trail records your name and id from your token. One decision per run (409 after)."""
    await _owned(runner, principal, incident_id, run_id)
    decision = Decision(
        decision=body.decision,
        reviewer=principal.name,
        user_id=principal.user_id,
        comment=body.comment,
        memory=body.memory,
    )
    run = await runner.decide(run_id, decision, tenant_id=principal.tenant_id)
    return await _response(runner, run)


@router.get("/{run_id}/events", response_model=list[WorkflowEventRead])
async def list_triage_events(
    incident_id: uuid.UUID, run_id: uuid.UUID, principal: CanReadTriage, runner: WorkflowRunnerDep
):
    """The run's audit trail, oldest first. Append-only: the database refuses changes."""
    await _owned(runner, principal, incident_id, run_id)
    return await runner.events(run_id)
