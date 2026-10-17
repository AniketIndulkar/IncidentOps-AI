"""AIENG-58 / H39: the legacy CMDB adapter normalises SOAP into a record, and every way the CMDB
can fail sends the run to a person with integration_degraded, never to an invented owner."""

import httpx
import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from test_analysis_api import EXTRACTION
from test_diagnosis import cause, model_says
from test_workflow import GOOD, ByType, deps, incident

from incidentops_ai.integrations.cmdb import CmdbClient, ServiceRecord
from incidentops_ai.integrations.fake_cmdb import FakeCmdb
from incidentops_ai.workflow.runner import WorkflowRunner, checkpoint_url

KEY = "cmdb-test-key-0123456789"


@pytest.fixture
def cmdb():
    return FakeCmdb(api_key=KEY)


def client(cmdb: FakeCmdb, *, url: str = "http://cmdb.test", timeout_s: float = 3.0, real=False):
    async def no_wait(_: float) -> None:
        return None

    transport = None if real else httpx.ASGITransport(app=cmdb.app)
    return CmdbClient(url, KEY, transport=transport, timeout_s=timeout_s, sleep=no_wait)


async def test_a_record_is_normalised_from_soap(cmdb):
    lookup = await client(cmdb).service_record("orders-api")

    assert lookup.status == "ok"
    assert lookup.record == ServiceRecord(
        service="orders-api",
        owner="Amara Okafor",
        tier=1,
        maintenance_window="Sun 02:00-04:00 UTC",
    )


async def test_an_unknown_service_is_an_answer_not_a_failure(cmdb):
    lookup = await client(cmdb).service_record("office-printing")

    assert (lookup.status, lookup.record) == ("unknown_service", None)


async def test_a_server_error_is_retried_once(cmdb):
    cmdb.fail_next(503)

    lookup = await client(cmdb).service_record("orders-api")

    assert lookup.status == "ok" and cmdb.calls == 2


@pytest.mark.parametrize(
    ("fault", "status"),
    [
        ("down", "unavailable"),  # 503 twice: retried once, then given up
        ("slow", "unavailable"),
        ("malformed", "invalid"),
        ("soap_fault", "invalid"),
        ("missing_owner", "invalid"),
        ("entity_attack", "invalid"),  # a DTD with an entity: defusedxml refuses it
    ],
)
async def test_each_failure_is_named(cmdb, fault, status):
    cmdb.set_fault(fault)

    lookup = await client(cmdb, timeout_s=0.2).service_record("orders-api")

    assert (lookup.status, lookup.record) == (status, None)
    assert KEY not in lookup.detail


async def test_a_refused_connection_is_unavailable(cmdb):
    lookup = await client(cmdb, url="http://127.0.0.1:1", real=True).service_record("orders-api")

    assert lookup.status == "unavailable"


@pytest.fixture
async def saver(settings):
    async with AsyncPostgresSaver.from_conn_string(checkpoint_url(settings.database_url)) as s:
        await s.setup()
        yield s


async def run_with(session_factory, saver, cmdb_client):
    workflow_deps = deps(
        session_factory, ByType(EXTRACTION, model_says(cause("A leak exhausts the pool.", GOOD)))
    )
    workflow_deps.cmdb = cmdb_client
    runner = WorkflowRunner(workflow_deps, saver)
    run = await runner.start(await incident(session_factory))
    return run, await runner.state(run.id)


async def test_a_known_owner_reaches_the_proposal(session_factory, saver, cmdb):
    run, state = await run_with(session_factory, saver, client(cmdb))

    assert (run.route, state["cmdb_status"]) == ("recommend", "ok")
    assert "Owner (CMDB): Amara Okafor" in state["proposed_action"]["body"]


@pytest.mark.parametrize("fault", ["down", "slow", "malformed", "soap_fault"])
async def test_a_degraded_cmdb_sends_the_run_to_review(session_factory, saver, cmdb, fault):
    cmdb.set_fault(fault)

    run, state = await run_with(session_factory, saver, client(cmdb, timeout_s=0.2))

    assert run.route == "review" and "integration_degraded" in run.reasons
    assert state["cmdb_record"] is None
    assert "Owner: unknown (CMDB" in state["proposed_action"]["body"]


async def test_the_app_uses_the_cmdb_when_it_is_configured(settings):
    from pydantic import SecretStr
    from test_diagnosis import POOL, StaticRetriever, chunks_of, retrieved

    from app.main import create_app
    from incidentops_ai.llm.fake_provider import FakeProvider

    configured = settings.model_copy(
        update={"cmdb_url": "http://cmdb.test", "cmdb_api_key": SecretStr(KEY)}
    )
    app = create_app(
        configured,
        llm_provider=FakeProvider([]),
        retriever=StaticRetriever(retrieved(*chunks_of("rb-orders-db-pool", POOL))),
    )
    async with app.router.lifespan_context(app):
        assert isinstance(app.state.workflow_runner.deps.cmdb, CmdbClient)
