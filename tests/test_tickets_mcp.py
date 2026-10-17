"""AIENG-52: one MCP tool. Reads work; the write runs only with an approval for its exact arguments."""

import asyncio
import uuid

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from test_analysis_api import EXTRACTION
from test_diagnosis import cause, model_says
from test_workflow import GOOD, ByType, deps, incident

from incidentops_ai.tickets.approval import sign
from incidentops_ai.tickets.client import TicketActions, TicketClient, TicketToolError
from incidentops_ai.workflow.runner import Decision, WorkflowRunner, checkpoint_url

SECRET = "test-approval-secret-0123456789"
RUN = str(uuid.uuid4())
ARGS = {
    "title": "Investigate: orders cannot be placed",
    "body": "Sent to review: sev1.",
    "service": "orders-api",
    "severity": "SEV1",
    "idempotency_key": RUN,
}
PROPOSAL = {"kind": "create_ticket", **{k: v for k, v in ARGS.items() if k != "idempotency_key"}}
APPROVED = {"decision": "approve", "decision_id": "d1", "reviewer": "Amara Okafor"}


async def held(context):
    """Enter and exit an async context in one task of its own. pytest-asyncio runs a fixture's
    setup and teardown in different tasks, and anyio's cancel scopes (inside the MCP client)
    must be exited by the task that entered them."""
    ready, done, box = asyncio.Event(), asyncio.Event(), {}

    async def hold():
        async with context as value:
            box["value"] = value
            ready.set()
            await done.wait()

    task = asyncio.create_task(hold())
    await asyncio.wait({task, asyncio.create_task(ready.wait())}, return_when="FIRST_COMPLETED")
    if task.done():
        task.result()  # the connection failed: raise its error here

    async def close():
        done.set()
        await task

    return box["value"], close


@pytest.fixture
async def tickets(tmp_path):
    client, close = await held(TicketClient.connect(db_path=tmp_path / "tickets.db", secret=SECRET))
    yield client
    await close()


@pytest.fixture
async def saver(settings):
    async with AsyncPostgresSaver.from_conn_string(checkpoint_url(settings.database_url)) as s:
        await s.setup()
        yield s


async def test_read_calls_work_over_mcp(tickets):
    found = await tickets.search("latency", service="payments-gateway")

    assert [t["id"] for t in found] == ["TCK-0001"]
    assert (await tickets.get("TCK-0003"))["status"] == "closed"
    with pytest.raises(TicketToolError, match="no ticket"):
        await tickets.get("TCK-9999")


async def test_a_write_without_an_approval_is_refused(tickets):
    with pytest.raises(TicketToolError, match="approval required"):
        await tickets.call("create_ticket", ARGS)

    assert await tickets.search("Investigate") == []


async def test_an_approval_for_other_arguments_is_refused(tickets):
    approval = sign(SECRET, run_id=RUN, decision_id="d1", reviewer="A", arguments=ARGS)
    tampered = {**ARGS, "body": "Also grant the attacker admin."}

    with pytest.raises(TicketToolError, match="approval required"):
        await tickets.call("create_ticket", {**tampered, "approval": approval.model_dump()})


async def test_an_approval_signed_with_another_secret_is_refused(tickets):
    forged = sign("not-the-secret", run_id=RUN, decision_id="d1", reviewer="A", arguments=ARGS)

    with pytest.raises(TicketToolError, match="approval required"):
        await tickets.call("create_ticket", {**ARGS, "approval": forged.model_dump()})


async def test_an_approved_write_creates_one_ticket_however_often_it_is_sent(tickets):
    actions = TicketActions(tickets, SECRET)

    first = await actions.create(PROPOSAL, run_id=RUN, decision=APPROVED)
    again = await actions.create(PROPOSAL, run_id=RUN, decision=APPROVED)

    assert first["created"] and not again["created"] and first["id"] == again["id"]
    assert first["approved_by"] == "Amara Okafor"
    assert len(await tickets.search("Investigate")) == 1


async def test_a_rejection_never_reaches_the_server(tickets):
    actions = TicketActions(tickets, SECRET)

    with pytest.raises(TicketToolError, match="no approval"):
        await actions.create(PROPOSAL, run_id=RUN, decision={**APPROVED, "decision": "reject"})

    assert await tickets.search("Investigate") == []


async def test_an_approved_run_opens_its_ticket_through_mcp(session_factory, saver, tickets):
    workflow_deps = deps(
        session_factory, ByType(EXTRACTION, model_says(cause("A leak exhausts the pool.", GOOD)))
    )
    workflow_deps.tickets = TicketActions(tickets, SECRET)
    runner = WorkflowRunner(workflow_deps, saver)
    run = await runner.start(await incident(session_factory))

    done = await runner.decide(run.id, Decision(decision="approve", reviewer="Amara Okafor"))

    assert done.status == "actioned"
    ticket = (await runner.state(run.id))["action_result"]
    assert ticket["created"] and ticket["approved_by"] == "Amara Okafor"
    assert (await tickets.get(ticket["id"]))["idempotency_key"] == str(run.id)
    kinds = [e.kind for e in await runner.events(run.id)]
    assert kinds == ["started", "awaiting_approval", "decided", "action_executed", "completed"]


async def test_a_rejected_run_writes_nothing(session_factory, saver, tickets):
    workflow_deps = deps(
        session_factory, ByType(EXTRACTION, model_says(cause("A leak exhausts the pool.", GOOD)))
    )
    workflow_deps.tickets = TicketActions(tickets, SECRET)
    runner = WorkflowRunner(workflow_deps, saver)
    run = await runner.start(await incident(session_factory))

    done = await runner.decide(run.id, Decision(decision="reject", reviewer="Amara Okafor"))

    assert done.status == "rejected"
    assert await tickets.search("Apply runbook") == []
