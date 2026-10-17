"""AIENG-46: runs are recorded, checkpointed in PostgreSQL, and resume in a new process."""

import uuid

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import text
from test_analysis_api import EXTRACTION
from test_diagnosis import cause, model_says
from test_workflow import GOOD, ByType, deps, incident

from incidentops_ai.errors import WorkflowFailedError, WorkflowNotResumableError
from incidentops_ai.workflow.runner import WorkflowRunner, checkpoint_url


def saver_url(settings) -> str:
    return checkpoint_url(settings.database_url)


def test_the_checkpoint_url_drops_the_asyncpg_driver():
    assert checkpoint_url("postgresql+asyncpg://u:p@h:5433/db") == "postgresql://u:p@h:5433/db"


async def test_a_completed_run_is_recorded_with_its_route(settings, session_factory):
    provider = ByType(EXTRACTION, model_says(cause("A leak exhausts the pool.", GOOD)))
    async with AsyncPostgresSaver.from_conn_string(saver_url(settings)) as saver:
        await saver.setup()
        runner = WorkflowRunner(deps(session_factory, provider), saver)

        run = await runner.start(await incident(session_factory))
        state = await runner.state(run.id)

    assert (run.status, run.route, run.reasons) == ("recommended", "recommend", [])
    assert state["route"] == "recommend" and len(state["steps"]) == 7
    async with session_factory() as session:
        row = (await session.execute(text("SELECT status, route FROM workflow_runs"))).one()
    assert tuple(row) == ("recommended", "recommend")


async def test_a_run_that_crashes_resumes_in_a_new_process_without_repeating_model_calls(
    settings, session_factory
):
    incident_id = await incident(session_factory)
    first_model = ByType(EXTRACTION, model_says(cause("A leak exhausts the pool.", GOOD)))

    def crash_before_assess(node: str) -> None:
        if node == "assess":
            raise RuntimeError("process killed")

    async with AsyncPostgresSaver.from_conn_string(saver_url(settings)) as saver:
        await saver.setup()
        runner = WorkflowRunner(
            deps(session_factory, first_model, fault=crash_before_assess), saver
        )
        with pytest.raises(WorkflowFailedError) as failed:
            await runner.start(incident_id)
    run_id = failed.value.run_id

    second_model = ByType(EXTRACTION, model_says())  # would give a different answer if asked
    async with AsyncPostgresSaver.from_conn_string(saver_url(settings)) as fresh:  # a new process
        resumed = await WorkflowRunner(deps(session_factory, second_model), fresh).resume(run_id)

    assert sorted(first_model.calls) == ["IncidentExtraction", "ModelDiagnosis"]
    assert second_model.calls == []
    assert (resumed.status, resumed.route) == ("recommended", "recommend")
    async with session_factory() as session:
        model_runs = (
            await session.execute(
                text("SELECT count(*) FROM llm_runs WHERE operation LIKE 'workflow_%'")
            )
        ).scalar_one()
    assert model_runs == 2  # one extraction, one diagnosis, never repeated


async def test_only_a_failed_run_can_be_resumed(settings, session_factory):
    provider = ByType(EXTRACTION, model_says(cause("Leak.", GOOD)))
    async with AsyncPostgresSaver.from_conn_string(saver_url(settings)) as saver:
        await saver.setup()
        runner = WorkflowRunner(deps(session_factory, provider), saver)
        run = await runner.start(await incident(session_factory))

        with pytest.raises(WorkflowNotResumableError):
            await runner.resume(run.id)


async def test_an_unknown_run_is_not_found(settings, session_factory):
    from incidentops_ai.errors import WorkflowRunNotFoundError

    async with AsyncPostgresSaver.from_conn_string(saver_url(settings)) as saver:
        runner = WorkflowRunner(deps(session_factory, ByType(EXTRACTION, model_says())), saver)
        with pytest.raises(WorkflowRunNotFoundError):
            await runner.resume(uuid.uuid4())
