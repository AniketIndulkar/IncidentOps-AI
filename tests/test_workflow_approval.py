"""AIENG-49: a run stops at the approval gate and only an explicit, audited decision moves it on."""

import asyncio
import uuid

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_analysis_api import EXTRACTION
from test_diagnosis import cause, model_says
from test_workflow import GOOD, ByType, deps, incident

from incidentops_ai.errors import WorkflowNotAwaitingDecisionError, WorkflowNotResumableError
from incidentops_ai.workflow.runner import Decision, WorkflowRunner, checkpoint_url

APPROVE = Decision(decision="approve", reviewer="Amara Okafor", comment="Matches the runbook.")
REJECT = Decision(decision="reject", reviewer="Amara Okafor", comment="Not this, it's the batch.")


@pytest.fixture
async def saver(settings):
    async with AsyncPostgresSaver.from_conn_string(checkpoint_url(settings.database_url)) as s:
        await s.setup()
        yield s


def clear_model():
    return ByType(EXTRACTION, model_says(cause("A leak exhausts the pool.", GOOD)))


async def kinds(runner: WorkflowRunner, run_id: uuid.UUID) -> list[str]:
    return [e.kind for e in await runner.events(run_id)]


async def test_a_run_stops_at_the_gate_with_a_proposed_ticket(session_factory, saver):
    runner = WorkflowRunner(deps(session_factory, clear_model()), saver)

    run = await runner.start(await incident(session_factory))
    state = await runner.state(run.id)

    assert (run.status, run.route) == ("awaiting_approval", "recommend")
    assert state["proposed_action"]["kind"] == "create_ticket"
    assert state["proposed_action"]["service"] == "orders-api"
    assert "rb-orders-db-pool" in state["proposed_action"]["body"]
    assert await kinds(runner, run.id) == ["started", "awaiting_approval"]


async def test_an_approval_is_recorded_and_completes_the_run(session_factory, saver):
    runner = WorkflowRunner(deps(session_factory, clear_model()), saver)
    run = await runner.start(await incident(session_factory))

    done = await runner.decide(run.id, APPROVE)

    assert done.status == "approved"  # no ticket system configured: nothing written
    events = await runner.events(run.id)
    assert [e.kind for e in events] == ["started", "awaiting_approval", "decided", "completed"]
    decided = events[2]
    assert decided.actor == "Amara Okafor"
    assert decided.data == {"decision": "approve", "comment": "Matches the runbook."}
    state = await runner.state(run.id)
    assert state["decision"]["reviewer"] == "Amara Okafor"
    assert [s["node"] for s in state["steps"]][-2:] == ["approval", "act"]


async def test_a_rejection_ends_the_run_without_acting(session_factory, saver):
    runner = WorkflowRunner(deps(session_factory, clear_model()), saver)
    run = await runner.start(await incident(session_factory))

    done = await runner.decide(run.id, REJECT)
    state = await runner.state(run.id)

    assert done.status == "rejected"
    assert "act" not in {s["node"] for s in state["steps"]}
    assert state.get("action_result") is None


async def test_a_reviewed_run_is_gated_too(session_factory, saver):
    model = ByType(EXTRACTION, model_says(status="insufficient_evidence"))
    runner = WorkflowRunner(deps(session_factory, model), saver)

    run = await runner.start(await incident(session_factory))
    state = await runner.state(run.id)

    assert (run.status, run.route) == ("awaiting_approval", "review")
    assert state["proposed_action"]["title"].startswith("Investigate:")


# H29: every way past the gate without a decision is blocked.


async def test_a_decision_is_refused_unless_the_run_is_waiting(session_factory, saver):
    runner = WorkflowRunner(deps(session_factory, clear_model()), saver)
    run = await runner.start(await incident(session_factory))
    await runner.decide(run.id, APPROVE)

    with pytest.raises(WorkflowNotAwaitingDecisionError):
        await runner.decide(run.id, REJECT)  # a second decision

    assert (await runner.get(run.id)).status == "approved"
    assert (await kinds(runner, run.id)).count("decided") == 1


async def test_two_concurrent_decisions_cannot_both_pass(session_factory, saver):
    runner = WorkflowRunner(deps(session_factory, clear_model()), saver)
    run = await runner.start(await incident(session_factory))

    results = await asyncio.gather(
        runner.decide(run.id, APPROVE), runner.decide(run.id, REJECT), return_exceptions=True
    )

    refused = [r for r in results if isinstance(r, WorkflowNotAwaitingDecisionError)]
    assert len(refused) == 1
    assert (await kinds(runner, run.id)).count("decided") == 1


async def test_reinvoking_the_paused_thread_stays_at_the_gate(session_factory, saver):
    runner = WorkflowRunner(deps(session_factory, clear_model()), saver)
    run = await runner.start(await incident(session_factory))

    graph = runner.graph  # the compiled graph itself, bypassing the runner
    again = await graph.ainvoke(None, {"configurable": {"thread_id": str(run.id)}})

    assert "__interrupt__" in again
    snapshot = await graph.aget_state({"configurable": {"thread_id": str(run.id)}})
    assert snapshot.next == ("approval",)
    assert "act" not in {s["node"] for s in snapshot.values["steps"]}


async def test_the_failed_run_resume_cannot_open_the_gate(session_factory, saver):
    runner = WorkflowRunner(deps(session_factory, clear_model()), saver)
    run = await runner.start(await incident(session_factory))

    with pytest.raises(WorkflowNotResumableError):
        await runner.resume(run.id)

    assert (await runner.get(run.id)).status == "awaiting_approval"


# H30: the audit trail cannot be rewritten.


@pytest.mark.parametrize(
    "statement",
    ["UPDATE workflow_events SET actor = 'someone else'", "DELETE FROM workflow_events"],
)
async def test_workflow_events_are_append_only(session_factory, saver, statement):
    runner = WorkflowRunner(deps(session_factory, clear_model()), saver)
    await runner.start(await incident(session_factory))

    async with session_factory() as session:
        with pytest.raises(DBAPIError, match="append-only"):
            await session.execute(text(statement))


async def test_a_malformed_answer_at_the_gate_is_asked_again_not_stored(session_factory, saver):
    # A resume value that is not a decision used to be stored as the gate's answer: every later
    # decision replayed it, and the run failed. The gate now asks again instead.
    from langgraph.types import Command

    runner = WorkflowRunner(deps(session_factory, clear_model()), saver)
    run = await runner.start(await incident(session_factory))
    config = {"configurable": {"thread_id": str(run.id)}}

    again = await runner.graph.ainvoke(Command(resume={"decision": "maybe"}), config)
    done = await runner.decide(run.id, APPROVE)

    assert "__interrupt__" in again
    assert done.status == "approved"
