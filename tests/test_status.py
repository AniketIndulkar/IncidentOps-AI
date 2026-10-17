"""AIENG-72 / H53: GET /status names the tier and each dependency's state under each failure."""

from contextlib import asynccontextmanager

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from test_diagnosis import POOL, StaticRetriever, chunks_of, retrieved
from test_workflow import Graph

from app.main import create_app
from incidentops_ai.integrations.cmdb import CmdbClient
from incidentops_ai.integrations.fake_cmdb import FakeCmdb
from incidentops_ai.llm.fake_provider import FakeProvider
from incidentops_ai.llm.provider import LLMStatus


@asynccontextmanager
async def status_of(settings, *, graph=None, cmdb=None, open_models=(), database_url=None):
    configured = settings.model_copy(update={"database_url": database_url or settings.database_url})
    app = create_app(
        configured,
        llm_provider=FakeProvider([]),
        retriever=StaticRetriever(retrieved(*chunks_of("rb-orders-db-pool", POOL))),
        graph_store=graph,
    )
    headers = {"Authorization": f"Bearer {settings.api_token.get_secret_value()}"}
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://t", headers=headers) as c,
    ):
        if cmdb is not None:
            app.state.workflow_runner.deps.cmdb = cmdb
        for model in open_models:
            for _ in range(3):
                app.state.gateway._record(model, LLMStatus.TIMED_OUT)
        yield (await c.get("/status")), c


async def test_everything_healthy_is_full(settings):
    async with status_of(settings, graph=Graph()) as (response, _):
        body = response.json()

    assert response.status_code == 200 and body["tier"] == "FULL"
    assert body["dependencies"]["database"]["state"] == "ok"
    assert body["dependencies"]["graph"]["state"] == "ok"
    assert body["dependencies"]["cmdb"]["state"] == "off"  # optional and not configured


@pytest.mark.parametrize(
    ("case", "dependency", "state"),
    [("graph", "graph", "degraded"), ("cmdb", "cmdb", "degraded")],
)
async def test_an_optional_dependency_failing_is_reduced(settings, case, dependency, state):
    fake = FakeCmdb(api_key="k")
    fake.set_fault("down")
    kwargs = (
        {"graph": Graph(down=True)}
        if case == "graph"
        else {
            "graph": Graph(),
            "cmdb": CmdbClient(
                "http://cmdb.test", "k", transport=httpx.ASGITransport(app=fake.app)
            ),
        }
    )
    async with status_of(settings, **kwargs) as (response, _):
        body = response.json()

    assert body["tier"] == "REDUCED" and body["dependencies"][dependency]["state"] == state


async def test_every_model_breaker_open_is_manual(settings):
    async with status_of(settings, graph=Graph(), open_models=["fake-model"]) as (response, _):
        body = response.json()

    assert body["tier"] == "MANUAL" and body["dependencies"]["model"]["state"] == "down"


async def test_the_database_down_is_manual(settings):
    async with status_of(settings, database_url="postgresql+asyncpg://localhost:1/x") as (
        response,
        _,
    ):
        body = response.json()

    assert body["tier"] == "MANUAL" and body["dependencies"]["database"]["state"] == "down"
    assert "localhost:1" not in response.text  # no connection details


async def test_status_needs_a_caller(settings):
    async with status_of(settings) as (_, client):
        anonymous = await client.get("/status", headers={"Authorization": ""})

    assert anonymous.status_code == 401
