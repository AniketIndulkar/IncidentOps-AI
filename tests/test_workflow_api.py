"""AIENG-46: the triage API runs the workflow, shows its state, and resumes failed runs."""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from test_analysis_api import EXTRACTION, INCIDENT
from test_diagnosis import POOL, StaticRetriever, cause, chunks_of, model_says, retrieved
from test_workflow import GOOD, ByType

from app.main import create_app
from incidentops_ai.errors import WorkflowFailedError
from incidentops_ai.llm.provider import ModelConfig
from incidentops_ai.workflow.graph import WorkflowDeps
from incidentops_ai.workflow.runner import WorkflowRunner, checkpoint_url


def corpus():
    return StaticRetriever(retrieved(*chunks_of("rb-orders-db-pool", POOL)))


@asynccontextmanager
async def client(settings, provider, *, token=True) -> AsyncIterator[AsyncClient]:
    app = create_app(settings, llm_provider=provider, retriever=corpus())
    headers = {"Authorization": f"Bearer {settings.api_token.get_secret_value()}"} if token else {}
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://t", headers=headers) as c,
    ):
        yield c


async def test_triage_runs_the_workflow_and_shows_its_steps(settings):
    provider = ByType(EXTRACTION, model_says(cause("A leak exhausts the pool.", GOOD)))
    async with client(settings, provider) as c:
        incident_id = (await c.post("/incidents", json=INCIDENT)).json()["id"]

        started = await c.post(f"/incidents/{incident_id}/triage")
        run_id = started.json()["run"]["id"]
        shown = await c.get(f"/incidents/{incident_id}/triage/{run_id}")
        listed = await c.get(f"/incidents/{incident_id}/triage")

    assert started.status_code == 200
    body = started.json()
    assert body["run"]["status"] == "awaiting_approval" and body["route"] == "recommend"
    assert body["steps"][0]["node"] == "intake" and len(body["steps"]) == 8
    assert body["recommendation"]["runbooks"][0]["doc_id"] == "rb-orders-db-pool"
    assert shown.json()["diagnosis"]["status"] == "answered"
    assert [r["id"] for r in listed.json()] == [run_id]


async def test_a_failed_run_is_resumed_through_the_api(settings, session_factory):
    provider = ByType(EXTRACTION, model_says(cause("A leak exhausts the pool.", GOOD)))
    async with client(settings, provider) as c:
        incident_id = (await c.post("/incidents", json=INCIDENT)).json()["id"]

        def crash(node):
            if node == "assess":
                raise RuntimeError("killed")

        async with AsyncPostgresSaver.from_conn_string(
            checkpoint_url(settings.database_url)
        ) as saver:
            deps = WorkflowDeps(
                session_factory, provider, ModelConfig(model="m"), corpus(), None, fault=crash
            )
            with pytest.raises(WorkflowFailedError) as failed:
                await WorkflowRunner(deps, saver).start(uuid.UUID(incident_id))
        run_id = failed.value.run_id

        resumed = await c.post(f"/incidents/{incident_id}/triage/{run_id}/resume")
        again = await c.post(f"/incidents/{incident_id}/triage/{run_id}/resume")

    assert resumed.status_code == 200 and resumed.json()["run"]["status"] == "awaiting_approval"
    assert again.status_code == 409 and again.json()["code"] == "workflow_not_resumable"
    assert sorted(provider.calls) == ["IncidentExtraction", "ModelDiagnosis"]


async def test_unknown_runs_and_missing_tokens_are_refused(settings):
    provider = ByType(EXTRACTION, model_says())
    async with client(settings, provider) as c:
        incident_id = (await c.post("/incidents", json=INCIDENT)).json()["id"]
        missing = await c.get(f"/incidents/{incident_id}/triage/{uuid.uuid4()}")
    async with client(settings, provider, token=False) as c:
        anonymous = await c.post(f"/incidents/{uuid.uuid4()}/triage")

    assert missing.status_code == 404 and missing.json()["code"] == "workflow_run_not_found"
    assert anonymous.status_code == 401


async def test_without_a_model_triage_is_off(settings):
    app = create_app(settings, llm_provider=None, retriever=corpus())
    headers = {"Authorization": f"Bearer {settings.api_token.get_secret_value()}"}
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://t", headers=headers) as c,
    ):
        incident_id = (await c.post("/incidents", json=INCIDENT)).json()["id"]
        resp = await c.post(f"/incidents/{incident_id}/triage")

    assert resp.status_code == 503 and resp.json()["code"] == "llm_not_configured"


async def test_the_api_starts_and_answers_503_when_the_database_is_down(settings):
    # Port 1 refuses connections. The checkpointer must not stop the server at boot.
    down = settings.model_copy(update={"database_url": "postgresql+asyncpg://localhost:1/x"})
    provider = ByType(EXTRACTION, model_says(cause("A leak exhausts the pool.", GOOD)))
    async with client(down, provider) as c:
        resp = await c.post(f"/incidents/{uuid.uuid4()}/triage")

    assert resp.status_code == 503 and resp.json()["code"] == "database_unavailable"
    assert provider.calls == []


async def test_a_decision_through_the_api_completes_the_run_and_is_audited(settings):
    provider = ByType(EXTRACTION, model_says(cause("A leak exhausts the pool.", GOOD)))
    async with client(settings, provider) as c:
        incident_id = (await c.post("/incidents", json=INCIDENT)).json()["id"]
        started = (await c.post(f"/incidents/{incident_id}/triage")).json()
        run_id = started["run"]["id"]
        base = f"/incidents/{incident_id}/triage/{run_id}"

        malformed = await c.post(f"{base}/decision", json={"decision": "maybe", "reviewer": "A"})
        decided = await c.post(
            f"{base}/decision", json={"decision": "approve", "reviewer": "Amara Okafor"}
        )
        again = await c.post(f"{base}/decision", json={"decision": "reject", "reviewer": "B"})
        events = (await c.get(f"{base}/events")).json()

    assert started["proposed_action"]["kind"] == "create_ticket"
    assert malformed.status_code == 422
    assert decided.status_code == 200 and decided.json()["run"]["status"] == "approved"
    assert decided.json()["decision"]["reviewer"] == "Amara Okafor"
    assert again.status_code == 409 and again.json()["code"] == "workflow_not_awaiting_decision"
    assert [e["kind"] for e in events] == ["started", "awaiting_approval", "decided", "completed"]
    assert events[2]["actor"] == "Amara Okafor"


async def test_with_a_ticket_system_an_approval_opens_the_ticket(settings, tmp_path):
    from pydantic import SecretStr

    with_tickets = settings.model_copy(
        update={
            "tickets_db": tmp_path / "tickets.db",
            "approval_secret": SecretStr("test-approval-secret-0123456789"),
        }
    )
    provider = ByType(EXTRACTION, model_says(cause("A leak exhausts the pool.", GOOD)))
    async with client(with_tickets, provider) as c:
        incident_id = (await c.post("/incidents", json=INCIDENT)).json()["id"]
        run_id = (await c.post(f"/incidents/{incident_id}/triage")).json()["run"]["id"]
        decided = await c.post(
            f"/incidents/{incident_id}/triage/{run_id}/decision",
            json={"decision": "approve", "reviewer": "Amara Okafor"},
        )

    body = decided.json()
    assert body["run"]["status"] == "actioned"
    assert body["action_result"]["id"].startswith("TCK-") and body["action_result"]["created"]
