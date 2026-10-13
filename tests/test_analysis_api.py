"""Extraction and related-incident endpoints, with a scripted provider: no real model calls."""

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.main import create_app
from incidentops_ai.config import Settings
from incidentops_ai.extraction.models import IncidentExtraction
from incidentops_ai.llm.fake_provider import Fail, FakeProvider
from incidentops_ai.llm.provider import LLMStatus, ToolCall

INCIDENT = {
    "title": "Checkout API returning 502s",
    "description": "Since 14:05 UTC roughly 30% of POST /checkout requests fail with 502.",
    "service": "checkout-api",
    "severity": "SEV2",
    "environment": "production",
}
EXTRACTION = IncidentExtraction.model_validate(
    {
        "title": "Checkout API returning 502s",
        "service": {"name": "checkout-api", "stated_explicitly": True},
        "symptoms": [{"description": "30% of POST /checkout fail", "started_at": "14:05 UTC"}],
        "error_codes": [{"code": "502", "kind": "http_status"}],
        "environment": "production",
        "severity_candidate": {"level": "SEV2", "rationale": "Partial outage."},
        "entities": [],
        "missing_information": [],
    }
)


@asynccontextmanager
async def app_client(
    settings: Settings, provider: FakeProvider | None
) -> AsyncIterator[AsyncClient]:
    app = create_app(settings, llm_provider=provider)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client,
    ):
        yield client


async def runs(session_factory) -> list[dict]:
    async with session_factory() as session:
        rows = await session.execute(text("SELECT * FROM llm_runs ORDER BY created_at"))
        return [dict(row._mapping) for row in rows]


async def test_extraction_returns_typed_result_and_persists_run_metadata(settings, session_factory):
    provider = FakeProvider(
        [
            Fail(
                LLMStatus.SUCCEEDED,
                value=EXTRACTION,
                input_tokens=900,
                output_tokens=300,
                latency_ms=4200,
            )
        ]
    )
    async with app_client(settings, provider) as client:
        incident = (await client.post("/incidents", json=INCIDENT)).json()

        resp = await client.post(f"/incidents/{incident['id']}/extraction")

    assert resp.status_code == 200
    body = resp.json()
    assert body["incident_id"] == incident["id"]
    assert body["extraction"]["service"] == {"name": "checkout-api", "stated_explicitly": True}
    assert body["run"]["prompt_name"] == "extract_incident"
    assert body["run"]["prompt_version"] == "v1"
    # The model saw the stored title and description.
    assert provider.calls[0].prompt.input == f"{INCIDENT['title']}\n\n{INCIDENT['description']}"

    [run] = await runs(session_factory)
    assert str(run["id"]) == body["run_id"]
    assert str(run["incident_id"]) == incident["id"]
    assert (run["operation"], run["status"]) == ("extract_incident", "succeeded")
    assert (run["provider"], run["model"]) == ("fake", "fake-model")
    assert (run["prompt_name"], run["prompt_version"]) == ("extract_incident", "v1")
    assert (run["input_tokens"], run["output_tokens"], run["latency_ms"]) == (900, 300, 4200)
    assert (run["attempts"], run["attempt_statuses"]) == (1, ["succeeded"])
    assert run["output"]["extraction"]["severity_candidate"]["level"] == "SEV2"
    assert run["output"]["review"] == {"required": False, "reasons": []}
    assert body["review"] == {"required": False, "reasons": []}
    assert run["tool_calls"] is None


async def test_failed_extraction_is_recorded_and_returns_a_problem_not_partial_data(
    settings, session_factory
):
    expected = {
        LLMStatus.TIMED_OUT: 504,
        LLMStatus.RATE_LIMITED: 503,
        LLMStatus.PROVIDER_ERROR: 502,
        LLMStatus.INVALID_OUTPUT: 502,
        LLMStatus.TRUNCATED: 502,
        LLMStatus.REFUSED: 422,
        LLMStatus.REJECTED: 500,
    }
    for status, http_status in expected.items():
        async with app_client(settings, FakeProvider([status])) as client:
            incident = (await client.post("/incidents", json=INCIDENT)).json()

            resp = await client.post(f"/incidents/{incident['id']}/extraction")

        assert resp.status_code == http_status, status
        assert resp.headers["content-type"] == "application/problem+json"
        body = resp.json()
        assert body["code"] == f"llm_{status}"
        assert "extraction" not in body
        run = (await runs(session_factory))[-1]
        assert run["status"] == status and run["output"] is None
        assert str(run["id"]) in body["detail"]  # the client can quote the recorded run


async def test_rate_limited_extraction_tells_the_client_when_to_retry(settings):
    provider = FakeProvider([Fail(LLMStatus.RATE_LIMITED, retry_after_s=12)])
    async with app_client(settings, provider) as client:
        incident = (await client.post("/incidents", json=INCIDENT)).json()

        resp = await client.post(f"/incidents/{incident['id']}/extraction")

    assert resp.status_code == 503 and resp.headers["retry-after"] == "12"


async def test_extraction_for_unknown_incident_is_404_and_calls_no_model(settings, session_factory):
    provider = FakeProvider([EXTRACTION])
    async with app_client(settings, provider) as client:
        resp = await client.post(f"/incidents/{uuid.uuid4()}/extraction")

    assert resp.status_code == 404 and resp.json()["code"] == "incident_not_found"
    assert provider.calls == [] and await runs(session_factory) == []


async def test_llm_endpoints_answer_503_when_no_provider_is_configured(settings):
    async with app_client(settings, None) as client:
        incident = (await client.post("/incidents", json=INCIDENT)).json()

        for path in ("extraction", "related-incidents"):
            resp = await client.post(f"/incidents/{incident['id']}/{path}")

            assert resp.status_code == 503, path
            assert resp.json()["code"] == "llm_not_configured"
        # The rest of the API keeps working without an LLM.
        assert (await client.get(f"/incidents/{incident['id']}")).status_code == 200


async def test_related_incidents_runs_parallel_tool_calls_and_records_them(
    settings, session_factory
):
    lookups = [
        ToolCall(
            call_id="c1",
            name="get_recent_incidents",
            arguments_json=json.dumps({"service": "checkout-api", "limit": 5}),
        ),
        ToolCall(
            call_id="c2",
            name="get_recent_incidents",
            arguments_json=json.dumps({"service": "payments-gateway", "limit": 500}),
        ),
    ]
    provider = FakeProvider(
        tool_turns=[lookups, "One earlier checkout-api incident looks related."]
    )
    async with app_client(settings, provider) as client:
        earlier = (
            await client.post("/incidents", json={**INCIDENT, "title": "Checkout 502s last week"})
        ).json()
        incident = (await client.post("/incidents", json=INCIDENT)).json()

        resp = await client.post(f"/incidents/{incident['id']}/related-incidents")

    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"] == "One earlier checkout-api incident looks related."
    assert [(c["call_id"], c["executed"], c["ok"]) for c in body["tool_calls"]] == [
        ("c1", True, True),
        ("c2", False, False),  # limit 500 is out of range: rejected, never run
    ]
    assert body["run"]["prompt_name"] == "related_incidents"
    # The valid lookup really read the database and its result went back to the model.
    sent_back = provider.tool_calls[1].outputs
    assert earlier["id"] in sent_back[0].output
    assert incident["id"] not in sent_back[0].output  # never "related" to itself
    assert "invalid arguments" in sent_back[1].output

    [run] = await runs(session_factory)
    assert (run["operation"], run["status"]) == ("related_incidents", "succeeded")
    assert (run["prompt_name"], run["prompt_version"], run["model"]) == (
        "related_incidents",
        "v1",
        "fake-model",
    )
    assert [(c["name"], c["executed"], c["error"]) for c in run["tool_calls"]] == [
        ("get_recent_incidents", True, None),
        ("get_recent_incidents", False, "invalid arguments"),
    ]
    assert run["tool_calls"][0]["arguments"] == {"service": "checkout-api", "limit": 5}
    assert run["output"] == {"summary": "One earlier checkout-api incident looks related."}
    assert run["input_tokens"] == 20  # summed over both model turns


async def test_failed_tool_conversation_is_recorded_with_the_calls_that_ran(
    settings, session_factory
):
    lookup = [
        ToolCall(
            call_id="c1",
            name="get_recent_incidents",
            arguments_json=json.dumps({"service": "checkout-api", "limit": 5}),
        )
    ]
    provider = FakeProvider(tool_turns=[lookup, LLMStatus.TIMED_OUT])
    async with app_client(settings, provider) as client:
        incident = (await client.post("/incidents", json=INCIDENT)).json()

        resp = await client.post(f"/incidents/{incident['id']}/related-incidents")

    assert resp.status_code == 504 and resp.json()["code"] == "llm_timed_out"
    [run] = await runs(session_factory)
    assert run["status"] == "timed_out" and run["output"] is None
    assert len(run["tool_calls"]) == 1


async def test_missing_json_values_are_sql_null_not_json_null(settings, session_factory):
    async with app_client(settings, FakeProvider([LLMStatus.TIMED_OUT])) as client:
        incident = (await client.post("/incidents", json=INCIDENT)).json()
        await client.post(f"/incidents/{incident['id']}/extraction")

    async with session_factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT tool_calls IS NULL AS no_tools, output IS NULL AS no_output FROM llm_runs"
                )
            )
        ).one()

    assert row.no_tools and row.no_output


async def test_deleting_an_incident_keeps_its_run_history(settings, session_factory):
    async with app_client(settings, FakeProvider([EXTRACTION])) as client:
        incident = (await client.post("/incidents", json=INCIDENT)).json()
        await client.post(f"/incidents/{incident['id']}/extraction")

    async with session_factory() as session:
        await session.execute(text("DELETE FROM incidents"))
        await session.commit()

    [run] = await runs(session_factory)
    assert run["incident_id"] is None and run["status"] == "succeeded"


async def test_blank_key_and_model_count_as_not_configured(settings):
    blank = Settings(
        _env_file=None, database_url=settings.database_url, OPENAI_API_KEY="", llm_model="  "
    )

    assert blank.openai_api_key is None and blank.llm_model is None
    async with app_client(blank, None) as client:
        resp = await client.post(f"/incidents/{uuid.uuid4()}/extraction")

    assert resp.status_code == 503 and resp.json()["code"] == "llm_not_configured"
