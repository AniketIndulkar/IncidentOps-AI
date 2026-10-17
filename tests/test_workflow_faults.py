"""AIENG-53 / H33: every injected fault ends in an explicit, audited state, with no duplicate ticket
and no unrecorded model call. The scenarios are the ones the fault-injection report runs."""

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from incidentops_ai.workflow.faults import SCENARIOS, FaultEnv
from incidentops_ai.workflow.runner import checkpoint_url


@pytest.fixture
async def env(settings, session_factory, tmp_path):
    async with AsyncPostgresSaver.from_conn_string(checkpoint_url(settings.database_url)) as s:
        await s.setup()
        yield FaultEnv(session_factory, s, tmp_path)


@pytest.mark.parametrize("name", list(SCENARIOS))
async def test_each_fault_ends_safely_and_observably(env, name):
    outcome = await SCENARIOS[name](env)

    assert outcome.ok, outcome
    assert outcome.calls_made == outcome.calls_recorded


async def test_the_timeout_retry_and_the_duplicate_write_leave_one_ticket(env):
    timeout = await SCENARIOS["tool_timeout"](env)
    duplicate = await SCENARIOS["duplicate_call"](env)

    assert timeout.tickets == 1 and "created=False" in timeout.detail
    assert (
        duplicate.tickets == 1 and "loop_detected after 2 rounds, tool ran 1x" in duplicate.detail
    )
