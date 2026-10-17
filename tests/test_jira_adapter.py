"""AIENG-55 / H35: the Jira-style adapter retries only what is safe, keeps secrets out of errors,
and writes only with an approval, once per run."""

import httpx
import pytest

from incidentops_ai.integrations.fake_jira import FakeJira
from incidentops_ai.integrations.jira import JiraClient, JiraError, JiraTicketSink

TOKEN = "jira-test-token-0123456789"
RUN = "3f1c0e8a-0000-4000-8000-000000000001"
PROPOSAL = {
    "kind": "create_ticket",
    "title": "Apply runbook: Orders cannot be placed",
    "body": "Likely causes:\n- A leak exhausts the pool.",
    "service": "orders-api",
    "severity": "SEV2",
}
APPROVED = {"decision": "approve", "decision_id": "d1", "reviewer": "Amara Okafor"}


@pytest.fixture
def jira():
    return FakeJira(token=TOKEN)


def client(jira: FakeJira, *, token: str = TOKEN, timeout_s: float = 5.0):
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    return (
        JiraClient(
            "http://jira.test",
            token,
            transport=httpx.ASGITransport(app=jira.app),
            timeout_s=timeout_s,
            sleep=sleep,
        ),
        sleeps,
    )


async def test_search_and_get_work_with_the_token(jira):
    jira.add(summary="Orders DB pool exhausted", labels=["orders"])
    api, _ = client(jira)

    found = await api.search('text ~ "pool"')
    issue = await api.get(found[0].key)

    assert [i.summary for i in found] == ["Orders DB pool exhausted"]
    assert (issue.key, issue.status) == (found[0].key, "Open")


async def test_a_wrong_token_is_unauthorized_and_not_retried(jira):
    api, _ = client(jira, token="wrong-token-0123456789")

    with pytest.raises(JiraError) as error:
        await api.search('text ~ "pool"')

    assert (error.value.kind, error.value.status) == ("unauthorized", 401)
    assert len(jira.requests) == 1


async def test_server_errors_are_retried_with_backoff(jira):
    jira.fail_next(503, 502)
    api, sleeps = client(jira)

    assert await api.search('text ~ "pool"') == []
    assert len(jira.requests) == 3 and sleeps == [0.2, 0.4]


async def test_a_rate_limit_waits_for_retry_after(jira):
    jira.fail_next(429, retry_after="2")
    api, sleeps = client(jira)

    await api.search('text ~ "pool"')

    assert sleeps == [2.0]


async def test_three_failures_are_unavailable(jira):
    jira.fail_next(503, 503, 503)
    api, _ = client(jira)

    with pytest.raises(JiraError) as error:
        await api.search('text ~ "pool"')

    assert error.value.kind == "unavailable" and len(jira.requests) == 3


async def test_a_missing_issue_is_not_found_and_not_retried(jira):
    api, _ = client(jira)

    with pytest.raises(JiraError) as error:
        await api.get("OPS-999")

    assert error.value.kind == "not_found" and len(jira.requests) == 1


async def test_a_malformed_response_is_invalid_not_a_crash(jira):
    jira.malformed_next()
    api, _ = client(jira)

    with pytest.raises(JiraError) as error:
        await api.search('text ~ "pool"')

    assert error.value.kind == "invalid_response"


async def test_a_create_is_never_retried_blindly(jira):
    jira.fail_next(503)
    api, _ = client(jira)

    with pytest.raises(JiraError) as error:
        await api.create(summary="x", description="y", labels=[])

    assert error.value.kind == "unavailable"
    assert [r.method for r in jira.requests] == ["POST"]


async def test_errors_never_carry_the_token_or_the_response_body(jira):
    jira.fail_next(500, 500, 500, body="internal detail: db password is hunter2")
    api, _ = client(jira)

    with pytest.raises(JiraError) as error:
        await api.search('text ~ "pool"')

    text = f"{error.value} {error.value!r} {error.value.args}"
    assert TOKEN not in text and "hunter2" not in text


async def test_the_sink_refuses_without_an_approval(jira):
    sink = JiraTicketSink(client(jira)[0])

    with pytest.raises(JiraError) as error:
        await sink.create(PROPOSAL, run_id=RUN, decision={**APPROVED, "decision": "reject"})

    assert error.value.kind == "not_approved" and jira.requests == []


async def test_the_sink_creates_once_per_run(jira):
    sink = JiraTicketSink(client(jira)[0])

    first = await sink.create(PROPOSAL, run_id=RUN, decision=APPROVED)
    again = await sink.create(PROPOSAL, run_id=RUN, decision=APPROVED)

    assert first["created"] and not again["created"] and first["id"] == again["id"]
    assert len(jira.issues) == 1
    assert f"incidentops-run-{RUN}" in jira.issues[first["id"]]["labels"]
    assert "Approved by Amara Okafor" in jira.issues[first["id"]]["description"]


async def test_a_create_that_timed_out_after_writing_is_found_not_duplicated(jira):
    jira.delay_next_post(1.0)  # stores the issue, then answers too late
    sink = JiraTicketSink(client(jira, timeout_s=0.2)[0])

    with pytest.raises(JiraError) as error:
        await sink.create(PROPOSAL, run_id=RUN, decision=APPROVED)
    retried = await sink.create(PROPOSAL, run_id=RUN, decision=APPROVED)

    assert error.value.kind == "unavailable"
    assert not retried["created"] and len(jira.issues) == 1


async def test_an_approved_workflow_run_opens_its_jira_issue(jira, session_factory, settings):
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from test_analysis_api import EXTRACTION
    from test_diagnosis import cause, model_says
    from test_workflow import GOOD, ByType, deps, incident

    from incidentops_ai.workflow.runner import Decision, WorkflowRunner, checkpoint_url

    workflow_deps = deps(
        session_factory, ByType(EXTRACTION, model_says(cause("A leak exhausts the pool.", GOOD)))
    )
    workflow_deps.tickets = JiraTicketSink(client(jira)[0])
    async with AsyncPostgresSaver.from_conn_string(checkpoint_url(settings.database_url)) as saver:
        await saver.setup()
        runner = WorkflowRunner(workflow_deps, saver)
        run = await runner.start(await incident(session_factory))
        done = await runner.decide(run.id, Decision(decision="approve", reviewer="Amara Okafor"))
        ticket = (await runner.state(run.id))["action_result"]

    assert done.status == "actioned"
    assert (ticket["system"], ticket["id"], ticket["created"]) == ("jira", "OPS-1", True)


async def test_the_app_uses_jira_when_it_is_configured(settings):
    from pydantic import SecretStr
    from test_diagnosis import POOL, StaticRetriever, chunks_of, retrieved

    from app.main import create_app
    from incidentops_ai.llm.fake_provider import FakeProvider

    configured = settings.model_copy(
        update={"jira_url": "http://jira.test", "jira_token": SecretStr(TOKEN)}
    )
    app = create_app(
        configured,
        llm_provider=FakeProvider([]),
        retriever=StaticRetriever(retrieved(*chunks_of("rb-orders-db-pool", POOL))),
    )
    async with app.router.lifespan_context(app):
        sink = app.state.workflow_runner.deps.tickets

    assert isinstance(sink, JiraTicketSink)
