"""AIENG-46: runs are recorded, checkpointed in PostgreSQL, and resume in a new process."""

import asyncio
import sys
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


# Resumes a run in a separate Python process, with a model that fails the run if it is asked.
RESUME_IN_A_NEW_PROCESS = """
import asyncio, sys, uuid
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from incidentops_ai.config import Settings
from incidentops_ai.db import create_engine, create_session_factory
from incidentops_ai.llm.provider import ModelConfig
from incidentops_ai.workflow.graph import WorkflowDeps
from incidentops_ai.workflow.runner import WorkflowRunner, checkpoint_url

class NoModel:
    async def generate_structured(self, **_):
        raise AssertionError("a model was called on resume")

async def main(url, run_id):
    engine = create_engine(Settings(database_url=url))
    deps = WorkflowDeps(create_session_factory(engine), NoModel(), ModelConfig(model="m"), None, None)
    async with AsyncPostgresSaver.from_conn_string(checkpoint_url(url)) as saver:
        run = await WorkflowRunner(deps, saver).resume(uuid.UUID(run_id))
    await engine.dispose()
    print(run.status, run.route)

asyncio.run(main(sys.argv[1], sys.argv[2]))
"""


def test_the_checkpoint_url_drops_the_asyncpg_driver():
    assert checkpoint_url("postgresql+asyncpg://u:p@h:5433/db") == "postgresql://u:p@h:5433/db"


async def test_a_completed_run_is_recorded_with_its_route(settings, session_factory):
    provider = ByType(EXTRACTION, model_says(cause("A leak exhausts the pool.", GOOD)))
    async with AsyncPostgresSaver.from_conn_string(saver_url(settings)) as saver:
        await saver.setup()
        runner = WorkflowRunner(deps(session_factory, provider), saver)

        run = await runner.start(await incident(session_factory))
        state = await runner.state(run.id)

    assert (run.status, run.route, run.reasons) == ("awaiting_approval", "recommend", [])
    assert state["route"] == "recommend" and len(state["steps"]) == 7
    async with session_factory() as session:
        row = (await session.execute(text("SELECT status, route FROM workflow_runs"))).one()
    assert tuple(row) == ("awaiting_approval", "recommend")


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

    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        RESUME_IN_A_NEW_PROCESS,
        settings.database_url,
        str(run_id),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await process.communicate()

    assert process.returncode == 0, err.decode()[-2000:]
    assert out.decode().split() == ["awaiting_approval", "recommend"]
    assert sorted(first_model.calls) == ["IncidentExtraction", "ModelDiagnosis"]
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


async def test_a_branch_that_fails_beside_a_running_sibling_still_marks_the_run_failed(
    settings, session_factory
):
    # extract and diagnose share a step: when diagnose raises, LangGraph cancels extract, possibly
    # in the middle of a database write. The run must still be recorded as failed.
    def crash_diagnosis(node: str) -> None:
        if node == "diagnose":
            raise RuntimeError("diagnosis crashed")

    provider = ByType(EXTRACTION, model_says(cause("Leak.", GOOD)))
    async with AsyncPostgresSaver.from_conn_string(saver_url(settings)) as saver:
        await saver.setup()
        runner = WorkflowRunner(deps(session_factory, provider, fault=crash_diagnosis), saver)
        with pytest.raises(WorkflowFailedError) as failed:
            await runner.start(await incident(session_factory))
        run = await runner.get(failed.value.run_id)

    assert (run.status, run.error) == ("failed", "RuntimeError")
