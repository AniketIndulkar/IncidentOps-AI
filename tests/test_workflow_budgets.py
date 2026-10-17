"""AIENG-51: a workflow run cannot retry or step without limit; each stop is explicit."""

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from test_analysis_api import EXTRACTION
from test_diagnosis import cause, model_says
from test_workflow import GOOD, ByType, deps, incident

from incidentops_ai.errors import (
    WorkflowAbandonedError,
    WorkflowFailedError,
    WorkflowNotResumableError,
)
from incidentops_ai.workflow.runner import WorkflowRunner, checkpoint_url


@pytest.fixture
async def saver(settings):
    async with AsyncPostgresSaver.from_conn_string(checkpoint_url(settings.database_url)) as s:
        await s.setup()
        yield s


def clear_model():
    return ByType(EXTRACTION, model_says(cause("A leak exhausts the pool.", GOOD)))


async def test_a_run_that_keeps_failing_is_abandoned_after_its_resume_budget(
    session_factory, saver
):
    def still_broken(node: str) -> None:
        if node == "assess":
            raise RuntimeError("still broken")

    model = clear_model()
    runner = WorkflowRunner(deps(session_factory, model, fault=still_broken), saver, max_resumes=2)
    with pytest.raises(WorkflowFailedError) as failed:
        await runner.start(await incident(session_factory))
    run_id = failed.value.run_id

    with pytest.raises(WorkflowFailedError):
        await runner.resume(run_id)
    with pytest.raises(WorkflowAbandonedError):
        await runner.resume(run_id)  # the last allowed resume fails too

    run = await runner.get(run_id)
    assert (run.status, run.error) == ("abandoned", "RuntimeError")
    kinds = [e.kind for e in await runner.events(run_id)]
    assert kinds == ["started", "failed", "resumed", "failed", "resumed", "abandoned"]
    with pytest.raises(WorkflowNotResumableError):
        await runner.resume(run_id)
    assert sorted(model.calls) == ["IncidentExtraction", "ModelDiagnosis"]  # never repeated


async def test_a_run_over_its_step_limit_fails_and_keeps_its_trace(session_factory, saver):
    runner = WorkflowRunner(deps(session_factory, clear_model()), saver, max_steps=3)

    with pytest.raises(WorkflowFailedError) as failed:
        await runner.start(await incident(session_factory))

    run = await runner.get(failed.value.run_id)
    assert (run.status, run.error) == ("failed", "GraphRecursionError")
    done = {s["node"] for s in (await runner.state(run.id))["steps"]}
    assert {"intake", "retrieve", "extract", "diagnose"} <= done and "assess" not in done
