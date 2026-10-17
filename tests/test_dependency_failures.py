"""AIENG-70 / H51: every dependency failure ends in a named state; no silent wrong answer.

Rows already covered elsewhere: the database down at request time (test_errors, test_workflow_api),
the graph unavailable (test_workflow), the CMDB (test_cmdb_adapter), the ticket system timing out
after a write (test_workflow_faults). This file adds the model, retrieval and a database
interruption mid-run.
"""

import asyncio

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import text
from test_analysis_api import EXTRACTION
from test_diagnosis import POOL, StaticRetriever, cause, chunks_of, model_says, retrieved
from test_gateway import ByModel
from test_workflow import GOOD, ByType, deps, incident

from incidentops_ai.errors import WorkflowFailedError
from incidentops_ai.llm.gateway import Gateway
from incidentops_ai.llm.provider import LLMStatus
from incidentops_ai.workflow.runner import WorkflowRunner, checkpoint_url


@pytest.fixture
async def saver(settings):
    async with AsyncPostgresSaver.from_conn_string(checkpoint_url(settings.database_url)) as s:
        await s.setup()
        yield s


class Routed:
    """The gateway's per-model behaviour, with structured replies by schema like ByType."""

    def __init__(self, failing: set[str]) -> None:
        self._failing = failing
        self._ok = ByType(EXTRACTION, model_says(cause("A leak exhausts the pool.", GOOD)))

    async def generate_structured(self, *, prompt, schema, config):
        if config.model in self._failing:
            return await ByModel({config.model: [LLMStatus.TIMED_OUT]}).generate_structured(
                prompt=prompt, schema=schema, config=config
            )
        return await self._ok.generate_structured(prompt=prompt, schema=schema, config=config)

    async def aclose(self):
        return None


ROUTES = {"extraction": ["primary", "secondary"], "diagnosis": ["primary", "secondary"]}


async def test_every_model_route_failing_sends_the_run_to_review(session_factory, saver):
    gateway = Gateway(Routed({"primary", "secondary"}), ROUTES)
    runner = WorkflowRunner(deps(session_factory, gateway), saver)

    run = await runner.start(await incident(session_factory))
    state = await runner.state(run.id)

    assert run.route == "review" and "model_failed" in run.reasons
    assert state["diagnosis"] is None and state["proposed_action"]["title"].startswith(
        "Investigate"
    )


async def test_a_failing_primary_is_answered_by_the_fallback_and_recorded(session_factory, saver):
    gateway = Gateway(Routed({"primary"}), ROUTES)
    runner = WorkflowRunner(deps(session_factory, gateway), saver)

    run = await runner.start(await incident(session_factory))

    assert run.route == "recommend"
    async with session_factory() as session:
        models = (await session.execute(text("SELECT model FROM llm_runs"))).scalars().all()
    assert sorted(models) == ["secondary", "secondary"]


class Slow(StaticRetriever):
    async def retrieve(self, query, *, k, filters, hints=None):
        await asyncio.sleep(10)
        return await super().retrieve(query, k=k, filters=filters, hints=hints)


async def test_slow_retrieval_times_out_and_the_run_goes_to_review(session_factory, saver):
    model = ByType(EXTRACTION, model_says(cause("A leak exhausts the pool.", GOOD)))
    workflow_deps = deps(
        session_factory, model, retriever=Slow(retrieved(*chunks_of("rb-orders-db-pool", POOL)))
    )
    workflow_deps.retrieval_timeout_s = 0.2
    runner = WorkflowRunner(workflow_deps, saver)

    run = await runner.start(await incident(session_factory))
    state = await runner.state(run.id)

    assert state["retrieval_error"] == "RetrievalTimeout"
    assert run.route == "review" and "no_answer" in run.reasons
    assert model.calls == ["IncidentExtraction"]  # no evidence: no diagnosis call, nothing invented


class Broken(StaticRetriever):
    async def retrieve(self, query, *, k, filters, hints=None):
        raise ConnectionResetError("vector store connection reset")


async def test_an_unexpected_retrieval_error_fails_the_run_loudly(session_factory, saver):
    model = ByType(EXTRACTION, model_says(cause("A leak exhausts the pool.", GOOD)))
    runner = WorkflowRunner(deps(session_factory, model, retriever=Broken([])), saver)

    with pytest.raises(WorkflowFailedError) as failed:
        await runner.start(await incident(session_factory))

    run = await runner.get(failed.value.run_id)
    assert (run.status, run.error) == ("failed", "ConnectionResetError")


async def test_a_run_whose_process_died_is_recovered_and_resumed(session_factory, saver):
    # The process dies before it can record the end of the run: the row says "running" forever.
    def crash(node: str) -> None:
        if node == "assess":
            raise RuntimeError("process died")

    model = ByType(EXTRACTION, model_says(cause("A leak exhausts the pool.", GOOD)))
    with pytest.raises(WorkflowFailedError) as failed:
        await WorkflowRunner(deps(session_factory, model, fault=crash), saver).start(
            await incident(session_factory)
        )
    run_id = failed.value.run_id
    async with session_factory() as session:
        await session.execute(
            text(
                "UPDATE workflow_runs SET status = 'running', updated_at = now() - interval '1 hour' "
                "WHERE id = :id"
            ),
            {"id": run_id},
        )
        await session.commit()
    runner = WorkflowRunner(deps(session_factory, model), saver)

    recovered = await runner.recover_stale(older_than_s=600)
    resumed = await runner.resume(run_id)

    assert recovered == [run_id]
    assert resumed.status == "awaiting_approval"
    kinds = [e.kind for e in await runner.events(run_id)]
    assert kinds[-3:] == ["failed", "resumed", "awaiting_approval"]
    assert (await runner.events(run_id))[-3].data == {"error": "StaleRun"}
    assert sorted(model.calls) == ["IncidentExtraction", "ModelDiagnosis"]  # never repeated


async def test_a_recent_running_run_is_left_alone(session_factory, saver):
    model = ByType(EXTRACTION, model_says(cause("A leak exhausts the pool.", GOOD)))
    runner = WorkflowRunner(deps(session_factory, model), saver)
    run = await runner.start(await incident(session_factory))
    async with session_factory() as session:
        await session.execute(
            text("UPDATE workflow_runs SET status = 'running' WHERE id = :id"), {"id": run.id}
        )
        await session.commit()

    assert await runner.recover_stale(older_than_s=600) == []  # slow, not dead
