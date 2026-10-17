"""Who may spend the OpenAI budget: a bearer token for the LLM endpoints and a rate limit on them.

Both checks run before anything else (incident lookup, model call), so a refused request costs
nothing and reveals nothing about which incidents exist.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient
from test_analysis_api import EXTRACTION, INCIDENT, runs

from app.main import create_app
from app.rate_limit import SlidingWindowLimiter
from incidentops_ai.config import Settings
from incidentops_ai.llm.fake_provider import FakeProvider

PATHS = ("extraction", "related-incidents")


def variant(settings: Settings, **changes) -> Settings:
    return Settings(
        _env_file=None,
        database_url=settings.database_url,
        llm_model="fake-model",
        api_token=settings.api_token,
        **changes,
    )


@asynccontextmanager
async def serve(settings: Settings, provider: FakeProvider | None) -> AsyncIterator[AsyncClient]:
    app = create_app(settings, llm_provider=provider)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client,
    ):
        yield client


def bearer(settings: Settings, token: str | None = None) -> dict[str, str]:
    return {"Authorization": f"Bearer {token or settings.api_token.get_secret_value()}"}


# --- login ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer not-the-token"},
        {"Authorization": "Bearer "},
        {"Authorization": "Basic dXNlcjpwYXNz"},
        {"Authorization": "test-token-0123456789"},  # right value, no scheme
    ],
    ids=["no header", "wrong token", "empty token", "wrong scheme", "no scheme"],
)
async def test_llm_endpoints_refuse_callers_without_the_token_and_call_no_model(
    settings, session_factory, path, headers
):
    provider = FakeProvider([EXTRACTION], tool_turns=["answer"])
    async with serve(settings, provider) as client:
        incident = (await client.post("/incidents", json=INCIDENT, headers=bearer(settings))).json()

        resp = await client.post(f"/incidents/{incident['id']}/{path}", headers=headers)

    assert resp.status_code == 401
    assert resp.headers["content-type"] == "application/problem+json"
    assert resp.headers["www-authenticate"] == "Bearer"
    assert resp.json()["code"] == "unauthorized"
    assert provider.calls == [] and provider.tool_calls == []
    assert await runs(session_factory) == []


@pytest.mark.parametrize("path", PATHS)
async def test_the_right_token_is_accepted(settings, path):
    provider = FakeProvider([EXTRACTION], tool_turns=["answer"])
    async with serve(settings, provider) as client:
        incident = (await client.post("/incidents", json=INCIDENT, headers=bearer(settings))).json()

        resp = await client.post(f"/incidents/{incident['id']}/{path}", headers=bearer(settings))

    assert resp.status_code == 200


async def test_login_is_checked_before_the_incident_so_ids_cannot_be_probed(settings):
    async with serve(settings, FakeProvider()) as client:
        resp = await client.post("/incidents/00000000-0000-0000-0000-000000000000/extraction")

    assert resp.status_code == 401  # not 404


async def test_login_is_checked_before_configuration_is_revealed(settings):
    async with serve(settings, None) as client:
        resp = await client.post("/incidents/00000000-0000-0000-0000-000000000000/extraction")

    assert resp.status_code == 401  # not 503


@pytest.mark.parametrize("path", PATHS)
async def test_without_any_configured_credential_every_route_stays_off(settings, path):
    """Fail closed: an OpenAI key on its own must never open the endpoints to everyone. With no
    API token and no JWT key (W10), nobody can be authenticated, so nothing is served."""
    no_token = Settings(_env_file=None, database_url=settings.database_url, llm_model="fake-model")
    provider = FakeProvider([EXTRACTION], tool_turns=["answer"])
    async with serve(no_token, provider) as client:
        for headers in ({}, bearer(settings), {"Authorization": "Bearer "}):
            created = await client.post("/incidents", json=INCIDENT, headers=headers)
            resp = await client.post(
                f"/incidents/00000000-0000-0000-0000-000000000000/{path}", headers=headers
            )

            assert created.status_code == 503 and created.json()["code"] == "auth_not_configured"
            assert resp.status_code == 503 and resp.json()["code"] == "auth_not_configured"
    assert provider.calls == [] and provider.tool_calls == []


async def test_every_route_but_health_needs_a_caller(settings):
    async with serve(settings, FakeProvider()) as client:
        anonymous = await client.post("/incidents", json=INCIDENT)
        incident = await client.post("/incidents", json=INCIDENT, headers=bearer(settings))
        read_anonymously = await client.get(f"/incidents/{incident.json()['id']}")

        assert anonymous.status_code == 401 and read_anonymously.status_code == 401
        assert incident.status_code == 201
        assert (await client.get("/health")).status_code == 200


def test_a_token_too_short_to_resist_guessing_is_refused_at_startup(settings):
    with pytest.raises(ValueError, match="at least 16") as refused:
        Settings(_env_file=None, database_url=settings.database_url, api_token="hunter2hunter")

    assert "hunter2hunter" not in str(refused.value)  # the startup error must not print it


def test_blank_token_counts_as_not_set(settings):
    blank = Settings(_env_file=None, database_url=settings.database_url, api_token="  ")

    assert blank.api_token is None


# --- rate limit ----------------------------------------------------------------------------------


async def test_both_endpoints_share_one_budget_and_the_extra_request_is_refused(settings):
    limited = variant(settings, llm_rate_limit_per_minute=2)
    provider = FakeProvider([EXTRACTION, EXTRACTION], tool_turns=["answer"])
    async with serve(limited, provider) as client:
        incident = (await client.post("/incidents", json=INCIDENT, headers=bearer(settings))).json()
        url = f"/incidents/{incident['id']}"
        headers = bearer(settings)

        first = await client.post(f"{url}/extraction", headers=headers)
        second = await client.post(f"{url}/related-incidents", headers=headers)
        third = await client.post(f"{url}/extraction", headers=headers)

    assert (first.status_code, second.status_code) == (200, 200)
    assert third.status_code == 429
    assert third.headers["content-type"] == "application/problem+json"
    assert third.json()["code"] == "rate_limit_exceeded"
    assert 1 <= int(third.headers["retry-after"]) <= 60
    assert len(provider.calls) == 1 and len(provider.tool_calls) == 1  # the third reached no model


async def test_requests_without_the_token_do_not_use_up_the_budget(settings):
    limited = variant(settings, llm_rate_limit_per_minute=1)
    provider = FakeProvider([EXTRACTION])
    async with serve(limited, provider) as client:
        incident = (await client.post("/incidents", json=INCIDENT, headers=bearer(settings))).json()
        url = f"/incidents/{incident['id']}/extraction"

        for _ in range(5):
            assert (await client.post(url)).status_code == 401
        resp = await client.post(url, headers=bearer(settings))

    assert resp.status_code == 200


def test_window_slides_so_old_requests_stop_counting():
    now = [0.0]
    limiter = SlidingWindowLimiter(limit=2, window_s=60, clock=lambda: now[0])

    assert limiter.hit() is None
    now[0] = 10
    assert limiter.hit() is None
    now[0] = 20
    assert limiter.hit() == pytest.approx(40)  # the first hit leaves the window at t=60
    now[0] = 59.9
    assert limiter.hit() == pytest.approx(0.1)
    now[0] = 60.1
    assert limiter.hit() is None  # the first hit has expired; the refused ones never counted
    assert limiter.hit() == pytest.approx(9.9)  # the hit at t=10 leaves the window at t=70


@pytest.mark.parametrize(
    "name",
    ["trace_file", "langfuse_host", "langfuse_public_key", "langfuse_secret_key", "tickets_db"],
)
def test_blank_optional_settings_count_as_not_set(settings, name):
    # .env.example lists these with empty values; a blank trace file used to become the path ".".
    blank = Settings(_env_file=None, database_url=settings.database_url, **{name: ""})

    assert getattr(blank, name) is None
