"""Idempotency-Key on the LLM endpoints: a retried request must not be billed twice.

A key is claimed before the model is called, so even two requests that arrive at the same moment
cost one call. Only a successful run is replayed; a failed one releases the key so the client's
retry really retries.
"""

import asyncio
import json
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from test_analysis_api import EXTRACTION, INCIDENT, app_client, runs

from app.main import create_app
from incidentops_ai.llm.fake_provider import FakeProvider
from incidentops_ai.llm.provider import LLMStatus, ToolCall

KEY = {"Idempotency-Key": "retry-me-0001"}
LOOKUP = ToolCall(
    call_id="c1",
    name="get_recent_incidents",
    arguments_json=json.dumps({"service": "checkout-api", "limit": 5}),
)


def script() -> FakeProvider:
    """Enough replies for two real calls on either endpoint."""
    return FakeProvider(
        [EXTRACTION, EXTRACTION],
        tool_turns=[[LOOKUP], "first answer", [LOOKUP], "second answer"],
    )


async def new_incident(client: AsyncClient) -> str:
    return (await client.post("/incidents", json=INCIDENT)).json()["id"]


@pytest.mark.parametrize("path", ["extraction", "related-incidents"])
async def test_same_key_replays_the_first_result_without_calling_the_model_again(
    settings, session_factory, path
):
    provider = script()
    async with app_client(settings, provider) as client:
        url = f"/incidents/{await new_incident(client)}/{path}"

        first = await client.post(url, headers=KEY)
        second = await client.post(url, headers=KEY)

    assert first.status_code == second.status_code == 200
    assert "idempotent-replayed" not in first.headers
    assert second.headers["idempotent-replayed"] == "true"
    assert second.json() == first.json()  # same run id, same answer, same recorded cost
    assert len(provider.calls) + len(provider.tool_calls) == (1 if path == "extraction" else 2)
    [run] = await runs(session_factory)
    assert str(run["id"]) == first.json()["run_id"]
    async with session_factory() as session:
        linked = (await session.execute(text("SELECT run_id FROM llm_run_keys"))).scalar_one()
    assert linked == run["id"]  # the key points at the run it produced


@pytest.mark.parametrize("path", ["extraction", "related-incidents"])
async def test_without_a_key_or_with_a_new_key_the_model_is_called_each_time(settings, path):
    provider = script()
    async with app_client(settings, provider) as client:
        url = f"/incidents/{await new_incident(client)}/{path}"

        first = await client.post(url)
        second = await client.post(url, headers={"Idempotency-Key": "another-key"})

    assert first.json()["run_id"] != second.json()["run_id"]
    assert "idempotent-replayed" not in second.headers


async def test_the_same_key_on_the_other_endpoint_is_a_different_request(settings):
    provider = script()
    async with app_client(settings, provider) as client:
        base = f"/incidents/{await new_incident(client)}"

        extraction = await client.post(f"{base}/extraction", headers=KEY)
        related = await client.post(f"{base}/related-incidents", headers=KEY)

    assert (extraction.status_code, related.status_code) == (200, 200)
    assert "idempotent-replayed" not in related.headers


async def test_a_key_cannot_be_reused_for_another_incident(settings, session_factory):
    provider = script()
    async with app_client(settings, provider) as client:
        one, two = await new_incident(client), await new_incident(client)
        await client.post(f"/incidents/{one}/extraction", headers=KEY)

        resp = await client.post(f"/incidents/{two}/extraction", headers=KEY)

    assert resp.status_code == 422 and resp.json()["code"] == "idempotency_key_reused"
    assert len(provider.calls) == 1 and len(await runs(session_factory)) == 1


async def test_a_failed_run_is_not_replayed_so_the_retry_really_retries(settings, session_factory):
    provider = FakeProvider([LLMStatus.TIMED_OUT, EXTRACTION])
    async with app_client(settings, provider) as client:
        url = f"/incidents/{await new_incident(client)}/extraction"

        first = await client.post(url, headers=KEY)
        second = await client.post(url, headers=KEY)

    assert first.status_code == 504
    assert second.status_code == 200 and "idempotent-replayed" not in second.headers
    assert len(provider.calls) == 2
    assert [r["status"] for r in await runs(session_factory)] == ["timed_out", "succeeded"]


async def test_a_crash_before_the_run_is_recorded_releases_the_key(settings):
    class Crashes(FakeProvider):
        async def generate_structured(self, **kwargs):
            raise RuntimeError("provider blew up")

    app = create_app(settings, llm_provider=Crashes())
    token = settings.api_token.get_secret_value()
    async with (
        app.router.lifespan_context(app),
        AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://t",
            headers={"Authorization": f"Bearer {token}"},
        ) as client,
    ):
        url = f"/incidents/{await new_incident(client)}/extraction"
        crashed = await client.post(url, headers=KEY)
        app.state.llm_provider = FakeProvider([EXTRACTION])

        retried = await client.post(url, headers=KEY)

    assert crashed.status_code == 500
    assert retried.status_code == 200


class Gated(FakeProvider):
    """Holds the model call open until the test lets it finish."""

    def __init__(self, replies) -> None:
        super().__init__(replies)
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def generate_structured(self, **kwargs):
        self.started.set()
        # Bounded, so a regression fails this test instead of hanging the whole run.
        await asyncio.wait_for(self.release.wait(), timeout=3)
        return await super().generate_structured(**kwargs)


async def test_a_duplicate_that_arrives_while_the_first_is_running_is_told_to_wait(
    settings, session_factory
):
    provider = Gated([EXTRACTION])
    async with app_client(settings, provider) as client:
        url = f"/incidents/{await new_incident(client)}/extraction"
        first = asyncio.create_task(client.post(url, headers=KEY))
        await asyncio.wait_for(provider.started.wait(), timeout=3)

        duplicate = await client.post(url, headers=KEY)
        provider.release.set()
        finished = await first
        replay = await client.post(url, headers=KEY)

    assert duplicate.status_code == 409 and duplicate.json()["code"] == "request_in_progress"
    assert int(duplicate.headers["retry-after"]) >= 1
    assert finished.status_code == 200
    assert replay.status_code == 200 and replay.json() == finished.json()
    assert len(provider.calls) == 1  # one call was billed
    assert len(await runs(session_factory)) == 1


async def claim_directly(session_factory, *, age: str, incident_id: str) -> None:
    async with session_factory() as session:
        await session.execute(
            text(
                "INSERT INTO llm_run_keys (operation, key, incident_id, claimed_at) "
                f"VALUES ('extract_incident', 'retry-me-0001', :incident, now() - interval '{age}')"
            ),
            {"incident": uuid.UUID(incident_id)},
        )
        await session.commit()


async def test_a_claim_left_behind_by_a_dead_request_expires(settings, session_factory):
    provider = FakeProvider([EXTRACTION])
    async with app_client(settings, provider) as client:
        incident_id = await new_incident(client)
        await claim_directly(session_factory, age="10 minutes", incident_id=incident_id)

        resp = await client.post(f"/incidents/{incident_id}/extraction", headers=KEY)

    assert resp.status_code == 200 and len(provider.calls) == 1


async def test_a_recent_claim_blocks_the_duplicate(settings, session_factory):
    provider = FakeProvider([EXTRACTION])
    async with app_client(settings, provider) as client:
        incident_id = await new_incident(client)
        await claim_directly(session_factory, age="10 seconds", incident_id=incident_id)

        resp = await client.post(f"/incidents/{incident_id}/extraction", headers=KEY)

    assert resp.status_code == 409 and provider.calls == []


async def test_a_replay_still_needs_the_incident_to_exist(settings):
    provider = FakeProvider([EXTRACTION])
    async with app_client(settings, provider) as client:
        resp = await client.post(f"/incidents/{uuid.uuid4()}/extraction", headers=KEY)

    assert resp.status_code == 404 and provider.calls == []


@pytest.mark.parametrize("bad", ["", "has space", "x" * 256])
async def test_a_malformed_key_is_a_validation_error(settings, bad):
    provider = FakeProvider([EXTRACTION])
    async with app_client(settings, provider) as client:
        url = f"/incidents/{await new_incident(client)}/extraction"

        resp = await client.post(url, headers={"Idempotency-Key": bad})

    assert resp.status_code == 422 and resp.json()["code"] == "validation_error"
    assert provider.calls == []
