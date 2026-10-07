import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from httpx import ASGITransport, AsyncClient

from app.main import create_app
from incidentops_ai.config import Settings

VALID_INCIDENT = {
    "title": "Checkout API returning 502s",
    "description": "Since 14:05 UTC roughly 30% of POST /checkout requests fail with 502.",
    "service": "checkout-api",
    "severity": "SEV2",
    "environment": "production",
}


async def test_create_incident_returns_201_with_server_fields(client: AsyncClient):
    resp = await client.post("/incidents", json=VALID_INCIDENT)

    assert resp.status_code == 201
    body = resp.json()
    assert uuid.UUID(body["id"])
    assert body["status"] == "open"
    assert body["created_at"] and body["updated_at"]
    for key, value in VALID_INCIDENT.items():
        assert body[key] == value
    assert resp.headers["location"] == f"/incidents/{body['id']}"


async def test_get_incident_returns_created_record(client: AsyncClient):
    created = (await client.post("/incidents", json=VALID_INCIDENT)).json()

    resp = await client.get(f"/incidents/{created['id']}")

    assert resp.status_code == 200
    assert resp.json() == created


async def test_get_unknown_incident_returns_404(client: AsyncClient):
    resp = await client.get(f"/incidents/{uuid.uuid4()}")

    assert resp.status_code == 404


async def test_get_with_malformed_id_returns_422(client: AsyncClient):
    resp = await client.get("/incidents/not-a-uuid")

    assert resp.status_code == 422


async def test_create_rejects_invalid_severity(client: AsyncClient):
    resp = await client.post("/incidents", json={**VALID_INCIDENT, "severity": "CRITICAL"})

    assert resp.status_code == 422


async def test_create_rejects_missing_required_field(client: AsyncClient):
    payload = {k: v for k, v in VALID_INCIDENT.items() if k != "title"}

    resp = await client.post("/incidents", json=payload)

    assert resp.status_code == 422


async def test_create_rejects_unknown_fields(client: AsyncClient):
    # Clients must not be able to set server-owned fields like status or id.
    resp = await client.post("/incidents", json={**VALID_INCIDENT, "status": "resolved"})

    assert resp.status_code == 422


async def test_create_strips_whitespace_and_rejects_blank_title(client: AsyncClient):
    resp = await client.post("/incidents", json={**VALID_INCIDENT, "title": "   "})

    assert resp.status_code == 422


async def test_create_rejects_invalid_service_name(client: AsyncClient):
    resp = await client.post("/incidents", json={**VALID_INCIDENT, "service": "Checkout API!"})

    assert resp.status_code == 422


async def test_incident_survives_app_restart(settings: Settings):
    """Records live in Postgres, not process memory: a fresh app instance can read them."""
    async with _fresh_client(settings) as c:
        created = (await c.post("/incidents", json=VALID_INCIDENT)).json()

    async with _fresh_client(settings) as c:
        resp = await c.get(f"/incidents/{created['id']}")

    assert resp.status_code == 200
    assert resp.json() == created


@asynccontextmanager
async def _fresh_client(settings: Settings) -> AsyncIterator[AsyncClient]:
    """New app instance with its own engine/pool, simulating a process restart."""
    app = create_app(settings)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c,
    ):
        yield c
