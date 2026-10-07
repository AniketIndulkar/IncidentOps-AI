import asyncio
import uuid

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from incidentops_ai.config import Settings

INCIDENT = {
    "title": "Payments webhook backlog",
    "description": "Webhook queue depth above 50k and growing since 09:12 UTC.",
    "service": "payments-webhooks",
    "severity": "SEV2",
    "environment": "production",
}


async def _incident_count(settings: Settings) -> int:
    engine = create_async_engine(settings.database_url)
    async with engine.connect() as conn:
        count = (await conn.execute(text("SELECT count(*) FROM incidents"))).scalar_one()
    await engine.dispose()
    return count


async def test_same_key_same_body_replays_original_incident(client: AsyncClient, settings):
    headers = {"Idempotency-Key": "create-abc-123"}

    first = await client.post("/incidents", json=INCIDENT, headers=headers)
    second = await client.post("/incidents", json=INCIDENT, headers=headers)

    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json() == first.json()
    assert "idempotent-replayed" not in first.headers
    assert second.headers["idempotent-replayed"] == "true"
    assert second.headers["location"] == first.headers["location"]
    assert await _incident_count(settings) == 1


async def test_replay_ignores_insignificant_whitespace_differences(client: AsyncClient, settings):
    # Validation strips whitespace, so these are the same logical request.
    headers = {"Idempotency-Key": "create-ws"}

    first = await client.post("/incidents", json=INCIDENT, headers=headers)
    padded = {**INCIDENT, "title": f"  {INCIDENT['title']}  "}
    second = await client.post("/incidents", json=padded, headers=headers)

    assert second.status_code == 201
    assert second.json()["id"] == first.json()["id"]
    assert await _incident_count(settings) == 1


async def test_same_key_different_body_is_rejected(client: AsyncClient, settings):
    headers = {"Idempotency-Key": "create-xyz"}
    await client.post("/incidents", json=INCIDENT, headers=headers)

    resp = await client.post("/incidents", json={**INCIDENT, "severity": "SEV1"}, headers=headers)

    assert resp.status_code == 422
    assert resp.headers["content-type"] == "application/problem+json"
    assert resp.json()["code"] == "idempotency_key_reused"
    assert await _incident_count(settings) == 1


async def test_concurrent_requests_with_same_key_create_one_incident(client: AsyncClient, settings):
    headers = {"Idempotency-Key": f"race-{uuid.uuid4()}"}

    responses = await asyncio.gather(
        *(client.post("/incidents", json=INCIDENT, headers=headers) for _ in range(10))
    )

    assert {r.status_code for r in responses} == {201}
    assert len({r.json()["id"] for r in responses}) == 1
    assert await _incident_count(settings) == 1


async def test_different_keys_create_different_incidents(client: AsyncClient, settings):
    a = await client.post("/incidents", json=INCIDENT, headers={"Idempotency-Key": "k-a"})
    b = await client.post("/incidents", json=INCIDENT, headers={"Idempotency-Key": "k-b"})

    assert a.json()["id"] != b.json()["id"]
    assert await _incident_count(settings) == 2


async def test_without_key_each_request_creates_an_incident(client: AsyncClient, settings):
    # Documented behavior: dedupe is opt-in. Clients that retry must send a key.
    await client.post("/incidents", json=INCIDENT)
    await client.post("/incidents", json=INCIDENT)

    assert await _incident_count(settings) == 2


async def test_invalid_idempotency_key_is_rejected(client: AsyncClient):
    for bad_key in ["", "x" * 256, "has spaces", "naïve".encode()]:
        resp = await client.post("/incidents", json=INCIDENT, headers={"Idempotency-Key": bad_key})

        assert resp.status_code == 422, bad_key
        assert resp.json()["code"] == "validation_error"


async def test_invalid_body_with_key_does_not_consume_the_key(client: AsyncClient, settings):
    headers = {"Idempotency-Key": "fix-and-retry"}

    bad = await client.post("/incidents", json={**INCIDENT, "severity": "nope"}, headers=headers)
    good = await client.post("/incidents", json=INCIDENT, headers=headers)

    assert bad.status_code == 422
    assert good.status_code == 201
    assert "idempotent-replayed" not in good.headers
    assert await _incident_count(settings) == 1
