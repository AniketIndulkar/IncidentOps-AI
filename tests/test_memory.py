"""AIENG-50: scoped memory that is explicit, queryable, cited, and never rewrites history."""

import uuid

import pytest
from httpx import AsyncClient
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_analysis_api import EXTRACTION
from test_diagnosis import cause, model_says
from test_workflow import GOOD, ByType, deps, incident

from incidentops_ai.errors import MemoryAlreadySupersededError, MemoryNotFoundError
from incidentops_ai.memory.repository import MemoryRepository
from incidentops_ai.workflow.runner import Decision, WorkflowRunner, checkpoint_url

NOTE = "Pool alarms on orders-api during the 02:00 batch are expected; check the batch first."
QUOTE = "check the batch first"


@pytest.fixture
async def saver(settings):
    async with AsyncPostgresSaver.from_conn_string(checkpoint_url(settings.database_url)) as s:
        await s.setup()
        yield s


async def note(session_factory, service="orders-api", body=NOTE, *, tenant="default"):
    async with session_factory() as session:
        created = await MemoryRepository(session).add(
            tenant_id=tenant, service=service, text=body, author="Amara Okafor"
        )
        await session.commit()
        return created


async def current(session_factory, service="orders-api", *, tenant="default"):
    async with session_factory() as session:
        return await MemoryRepository(session).current(tenant_id=tenant, service=service)


async def test_a_correction_supersedes_the_note_and_keeps_its_history(session_factory):
    first = await note(session_factory)
    async with session_factory() as session:
        repo = MemoryRepository(session)
        second = await repo.supersede(
            first.id, text="Check the 02:00 batch, then the pool.", author="B"
        )
        await session.commit()
        history = await repo.history(second.id)

    assert [n.text for n in await current(session_factory)] == [second.text]
    assert [n.id for n in history] == [first.id, second.id]
    assert history[1].supersedes == first.id


async def test_a_withdrawn_note_is_no_longer_current(session_factory):
    first = await note(session_factory)
    async with session_factory() as session:
        await MemoryRepository(session).supersede(first.id, text=None, author="B")
        await session.commit()

    assert await current(session_factory) == []


async def test_a_note_can_be_superseded_only_once(session_factory):
    first = await note(session_factory)
    async with session_factory() as session:
        repo = MemoryRepository(session)
        await repo.supersede(first.id, text="v2", author="B")
        await session.commit()
        with pytest.raises(MemoryAlreadySupersededError):
            await repo.supersede(first.id, text="a fork", author="C")


async def test_an_unknown_note_cannot_be_superseded(session_factory):
    async with session_factory() as session:
        with pytest.raises(MemoryNotFoundError):
            await MemoryRepository(session).supersede(uuid.uuid4(), text="x", author="B")


async def test_notes_are_scoped_to_tenant_and_service(session_factory):
    await note(session_factory)

    assert await current(session_factory, "checkout-api") == []
    assert await current(session_factory, tenant="acme") == []
    assert len(await current(session_factory)) == 1


@pytest.mark.parametrize(
    "statement", ["UPDATE memories SET text = 'rewritten'", "DELETE FROM memories"]
)
async def test_memories_are_append_only(session_factory, statement):
    await note(session_factory)

    async with session_factory() as session:
        with pytest.raises(DBAPIError, match="append-only"):
            await session.execute(text(statement))


async def test_a_recalled_note_is_evidence_the_diagnosis_can_cite(session_factory, saver):
    remembered = await note(session_factory)
    model = ByType(
        EXTRACTION, model_says(cause("The nightly batch holds the pool.", ("M1", QUOTE)))
    )
    runner = WorkflowRunner(deps(session_factory, model), saver)

    run = await runner.start(await incident(session_factory))
    state = await runner.state(run.id)

    assert [m["id"] for m in state["memories"]] == [str(remembered.id)]
    citation = state["diagnosis"]["causes"][0]["citations"][0]
    assert (citation["evidence_id"], citation["doc_id"]) == ("M1", f"mem-{remembered.id}")
    assert "recall" in {s["node"] for s in state["steps"]}


async def test_a_reviewer_can_leave_a_note_with_their_decision(session_factory, saver):
    model = ByType(EXTRACTION, model_says(cause("A leak exhausts the pool.", GOOD)))
    runner = WorkflowRunner(deps(session_factory, model), saver)
    run = await runner.start(await incident(session_factory))

    await runner.decide(run.id, Decision(decision="approve", reviewer="Amara Okafor", memory=NOTE))

    notes = await current(session_factory)
    assert [(n.text, n.author, n.source_run_id) for n in notes] == [(NOTE, "Amara Okafor", run.id)]
    decided = next(e for e in await runner.events(run.id) if e.kind == "decided")
    assert decided.data["memory_id"] == str(notes[0].id)
    later = await runner.start(await incident(session_factory))
    assert [m["id"] for m in (await runner.state(later.id))["memories"]] == [str(notes[0].id)]


async def test_a_resumed_run_keeps_the_notes_it_recalled(session_factory, saver):
    old = await note(session_factory)

    def crash_before_assess(node: str) -> None:
        if node == "assess":
            raise RuntimeError("process killed")

    model = ByType(EXTRACTION, model_says(cause("A leak exhausts the pool.", GOOD)))
    from incidentops_ai.errors import WorkflowFailedError

    with pytest.raises(WorkflowFailedError) as failed:
        await WorkflowRunner(deps(session_factory, model, fault=crash_before_assess), saver).start(
            await incident(session_factory)
        )
    async with session_factory() as session:
        new = await MemoryRepository(session).supersede(old.id, text="Rewritten note.", author="B")
        await session.commit()

    runner = WorkflowRunner(deps(session_factory, model), saver)
    resumed = await runner.resume(failed.value.run_id)

    assert resumed.status == "awaiting_approval"
    state = await runner.state(resumed.id)
    assert [(m["id"], m["text"]) for m in state["memories"]] == [(str(old.id), NOTE)]
    fresh = await runner.start(await incident(session_factory))
    assert [m["id"] for m in (await runner.state(fresh.id))["memories"]] == [str(new.id)]


async def test_the_memory_api(client: AsyncClient, settings):
    headers = {"Authorization": f"Bearer {settings.api_token.get_secret_value()}"}
    body = {"service": "orders-api", "text": NOTE}  # the author is the caller (W10)

    unauthorised = await client.post("/memory", json=body, headers={"Authorization": "Bearer x"})
    created = await client.post("/memory", json=body, headers=headers)
    note_id = created.json()["id"]
    corrected = await client.post(
        f"/memory/{note_id}/supersede", json={"text": "v2"}, headers=headers
    )
    fork = await client.post(f"/memory/{note_id}/supersede", json={"text": "v3"}, headers=headers)
    listed = await client.get("/memory", params={"service": "orders-api"}, headers=headers)
    history = await client.get(f"/memory/{corrected.json()['id']}/history", headers=headers)
    missing = await client.get(f"/memory/{uuid.uuid4()}/history", headers=headers)

    assert unauthorised.status_code == 401
    assert created.status_code == 201 and corrected.status_code == 201
    assert fork.status_code == 409 and fork.json()["code"] == "memory_already_superseded"
    assert [n["text"] for n in listed.json()] == ["v2"]
    assert [n["text"] for n in history.json()] == [NOTE, "v2"]
    assert missing.status_code == 404
