"""Every error response uses one shape: RFC 9457 problem details plus a stable `code`."""

import uuid

from httpx import ASGITransport, AsyncClient

from app.main import create_app
from incidentops_ai.config import Settings

PROBLEM_JSON = "application/problem+json"


def assert_problem(resp, status: int, code: str) -> dict:
    assert resp.status_code == status
    assert resp.headers["content-type"] == PROBLEM_JSON
    body = resp.json()
    assert body["status"] == status
    assert body["code"] == code
    assert body["title"]
    assert body["type"] == "about:blank"
    return body


async def test_missing_incident_returns_not_found_problem(client: AsyncClient):
    incident_id = uuid.uuid4()

    resp = await client.get(f"/incidents/{incident_id}")

    body = assert_problem(resp, 404, "incident_not_found")
    assert str(incident_id) in body["detail"]
    assert body["instance"] == f"/incidents/{incident_id}"


async def test_validation_error_lists_each_field_problem(client: AsyncClient):
    resp = await client.post(
        "/incidents",
        json={"title": "", "description": "d", "service": "BAD!", "severity": "X"},
    )

    body = assert_problem(resp, 422, "validation_error")
    fields = {tuple(e["loc"]) for e in body["errors"]}
    assert fields >= {
        ("body", "title"),
        ("body", "service"),
        ("body", "severity"),
        ("body", "environment"),
    }
    for err in body["errors"]:
        assert set(err) == {"loc", "message", "type"}


async def test_validation_error_does_not_echo_submitted_values(client: AsyncClient):
    # Incident text can contain secrets/PII; error bodies must not reflect it back into logs.
    secret = "password=hunter2"
    resp = await client.post(
        "/incidents",
        json={
            "title": "t",
            "description": secret,
            "service": "svc",
            "severity": secret,
            "environment": "production",
        },
    )

    assert resp.status_code == 422
    assert secret not in resp.text


async def test_malformed_json_returns_validation_problem(client: AsyncClient):
    resp = await client.post(
        "/incidents", content=b"{not json", headers={"content-type": "application/json"}
    )

    assert_problem(resp, 422, "validation_error")


async def test_unknown_route_returns_problem(client: AsyncClient):
    resp = await client.get("/nope")

    assert_problem(resp, 404, "not_found")


async def test_wrong_method_returns_problem(client: AsyncClient):
    resp = await client.delete("/incidents")

    assert_problem(resp, 405, "method_not_allowed")


async def test_database_unavailable_returns_503_without_internals():
    # Port 1 refuses connections: simulates Postgres being down.
    app = create_app(Settings(database_url="postgresql+asyncpg://localhost:1/incidentops"))
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c,
    ):
        resp = await c.get(f"/incidents/{uuid.uuid4()}")

    body = assert_problem(resp, 503, "database_unavailable")
    assert "localhost:1" not in resp.text
    assert "Traceback" not in resp.text
    assert resp.headers["retry-after"]
    assert body["detail"]


async def test_unexpected_error_returns_generic_500(settings: Settings):
    app = create_app(settings)

    @app.get("/boom")
    async def boom():
        raise RuntimeError("internal secret detail")

    async with (
        app.router.lifespan_context(app),
        AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t"
        ) as c,
    ):
        resp = await c.get("/boom")

    assert_problem(resp, 500, "internal_error")
    assert "internal secret detail" not in resp.text
