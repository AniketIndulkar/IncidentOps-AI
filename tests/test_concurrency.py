"""AIENG-71 / H52: duplicates and concurrency make exactly one of everything irreversible, and a
server killed mid-run resumes it after restart without repeating a finished model step."""

import asyncio
import os
import signal
import socket
import subprocess
import sys
import time
import uuid

import httpx
import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import text
from test_analysis_api import EXTRACTION, INCIDENT
from test_diagnosis import cause, model_says
from test_workflow import GOOD, ByType, deps, incident

from incidentops_ai.errors import WorkflowNotAwaitingDecisionError
from incidentops_ai.integrations.fake_jira import FakeJira
from incidentops_ai.integrations.jira import JiraClient, JiraTicketSink
from incidentops_ai.retrieval.chunking import chunker_from_name
from incidentops_ai.retrieval.documents import load_corpus
from incidentops_ai.retrieval.ingest import DEFAULT_CHUNKER, ingest_documents
from incidentops_ai.workflow.graph import nodes
from incidentops_ai.workflow.runner import Decision, WorkflowRunner, checkpoint_url


@pytest.fixture
async def saver(settings):
    async with AsyncPostgresSaver.from_conn_string(checkpoint_url(settings.database_url)) as s:
        await s.setup()
        yield s


async def test_fifty_concurrent_submissions_with_one_key_make_one_incident(client):
    key = {"Idempotency-Key": f"burst-{uuid.uuid4()}"}

    responses = await asyncio.gather(
        *(client.post("/incidents", json=INCIDENT, headers=key) for _ in range(50))
    )

    assert {r.status_code for r in responses} <= {200, 201}
    assert len({r.json()["id"] for r in responses}) == 1


async def test_twenty_concurrent_decisions_make_one(session_factory, saver):
    model = ByType(EXTRACTION, model_says(cause("A leak exhausts the pool.", GOOD)))
    runner = WorkflowRunner(deps(session_factory, model), saver)
    run = await runner.start(await incident(session_factory))

    results = await asyncio.gather(
        *(runner.decide(run.id, Decision(decision="approve", reviewer=f"r{i}")) for i in range(20)),
        return_exceptions=True,
    )

    assert sum(isinstance(r, WorkflowNotAwaitingDecisionError) for r in results) == 19
    assert sum(e.kind == "decided" for e in await runner.events(run.id)) == 1


async def test_ten_concurrent_ticket_writes_for_one_run_make_one_ticket(session_factory, saver):
    jira = FakeJira(token="t" * 24)
    sink = JiraTicketSink(
        JiraClient("http://jira.test", "t" * 24, transport=httpx.ASGITransport(app=jira.app))
    )
    model = ByType(EXTRACTION, model_says(cause("A leak exhausts the pool.", GOOD)))
    workflow_deps = deps(session_factory, model)
    workflow_deps.tickets = sink
    runner = WorkflowRunner(workflow_deps, saver)
    run = await runner.start(await incident(session_factory))
    state = await runner.state(run.id)
    state["decision"] = {"decision": "approve", "decision_id": "d1", "reviewer": "Amara Okafor"}
    act = nodes(workflow_deps)["act"]

    results = await asyncio.gather(*(act(state) for _ in range(10)))

    assert len(jira.issues) == 1
    assert sum(r["action_result"]["created"] for r in results) == 1


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _server(settings, port: int, stale_after: int) -> subprocess.Popen:
    env = {
        **os.environ,
        "INCIDENTOPS_DATABASE_URL": settings.database_url,
        "INCIDENTOPS_API_TOKEN": settings.api_token.get_secret_value(),
        "INCIDENTOPS_LLM_PROVIDER": "simulated",
        "INCIDENTOPS_LLM_MODEL": "simulated",
        "INCIDENTOPS_RETRIEVAL_STACK": "bm25",
        "INCIDENTOPS_STALE_RUN_AFTER_S": str(stale_after),
        "PYTHONPATH": "apps/api",
    }
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--port",
            str(port),
            "--log-level",
            "error",
        ],
        env=env,
    )


async def _up(base: str) -> None:
    deadline = time.monotonic() + 30
    async with httpx.AsyncClient() as c:
        while time.monotonic() < deadline:
            try:
                if (await c.get(f"{base}/health")).status_code == 200:
                    return
            except httpx.TransportError:
                await asyncio.sleep(0.2)
    raise RuntimeError("server did not start")


async def test_a_server_killed_mid_run_resumes_it_after_restart(settings, session_factory):
    async with session_factory() as session, session.begin():
        await ingest_documents(
            session,
            load_corpus(__import__("pathlib").Path("data/corpus")),
            chunker_from_name(DEFAULT_CHUNKER),
            tenant_id="default",
        )
    port = _free_port()
    base, headers = (
        f"http://127.0.0.1:{port}",
        {"Authorization": f"Bearer {settings.api_token.get_secret_value()}"},
    )
    server = _server(settings, port, stale_after=3600)
    try:
        await _up(base)
        async with httpx.AsyncClient(base_url=base, headers=headers, timeout=60) as c:
            incident_id = (await c.post("/incidents", json=INCIDENT)).json()["id"]
            triage = asyncio.create_task(c.post(f"/incidents/{incident_id}/triage"))
            # Wait until a model step has finished and been recorded, then kill the server.
            async with session_factory() as session:
                for _ in range(200):
                    done = (
                        (
                            await session.execute(
                                text("SELECT operation FROM llm_runs WHERE incident_id = :i"),
                                {"i": incident_id},
                            )
                        )
                        .scalars()
                        .all()
                    )
                    if done:
                        break
                    await asyncio.sleep(0.05)
            server.send_signal(signal.SIGKILL)
            server.wait(timeout=10)
            with pytest.raises(httpx.HTTPError):
                await triage
    finally:
        if server.poll() is None:
            server.kill()
    async with session_factory() as session:
        before = (
            (
                await session.execute(
                    text("SELECT operation FROM llm_runs WHERE incident_id = :i"),
                    {"i": incident_id},
                )
            )
            .scalars()
            .all()
        )
        status = (
            await session.execute(
                text("SELECT id, status FROM workflow_runs WHERE incident_id = :i"),
                {"i": incident_id},
            )
        ).one()
    assert status.status == "running"  # the dead process could not say otherwise

    restarted = _server(settings, port, stale_after=0)  # recovery at startup: the run is stale
    try:
        await _up(base)
        async with httpx.AsyncClient(base_url=base, headers=headers, timeout=60) as c:
            resumed = await c.post(f"/incidents/{incident_id}/triage/{status.id}/resume")
    finally:
        restarted.terminate()
        restarted.wait(timeout=10)

    assert resumed.status_code == 200 and resumed.json()["run"]["status"] == "awaiting_approval"
    async with session_factory() as session:
        after = (
            (
                await session.execute(
                    text("SELECT operation FROM llm_runs WHERE incident_id = :i"),
                    {"i": incident_id},
                )
            )
            .scalars()
            .all()
        )
    assert before and set(before) <= set(after)
    assert sorted(after) == ["workflow_diagnose", "workflow_extract"]  # each model step once
