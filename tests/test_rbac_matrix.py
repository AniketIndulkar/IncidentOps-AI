"""AIENG-60 / H40: every route enforces its permission on the server, for every role.

The matrix lists every route with the permission it needs. A route added to the app without an
entry here fails `test_the_matrix_covers_every_route`, so nothing ships without a decision.
"""

import uuid
from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from test_diagnosis import POOL, StaticRetriever, chunks_of, retrieved

from app.main import create_app
from incidentops_ai.auth.principal import ROLES
from incidentops_ai.auth.tokens import mint
from incidentops_ai.llm.fake_provider import FakeProvider

pytestmark = pytest.mark.redteam

JWT_SECRET = "matrix-jwt-secret-0123456789abcdef"
ISSUER = "https://idp.test"
I, R, N = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

MATRIX = [
    ("POST", "/incidents", "incidents:write"),
    ("GET", f"/incidents/{I}", "incidents:read"),
    ("POST", f"/incidents/{I}/extraction", "analysis:run"),
    ("POST", f"/incidents/{I}/related-incidents", "analysis:run"),
    ("POST", f"/incidents/{I}/diagnosis", "analysis:run"),
    ("POST", f"/incidents/{I}/attachments", "incidents:write"),
    ("GET", f"/incidents/{I}/attachments", "incidents:read"),
    ("POST", f"/incidents/{I}/triage", "triage:run"),
    ("GET", f"/incidents/{I}/triage", "triage:read"),
    ("GET", f"/incidents/{I}/triage/{R}", "triage:read"),
    ("POST", f"/incidents/{I}/triage/{R}/resume", "triage:resume"),
    ("POST", f"/incidents/{I}/triage/{R}/decision", "triage:decide"),
    ("GET", f"/incidents/{I}/triage/{R}/events", "triage:read"),
    ("GET", "/memory?service=orders-api", "memory:read"),
    ("POST", "/memory", "memory:write"),
    ("POST", f"/memory/{N}/supersede", "memory:write"),
    ("GET", f"/memory/{N}/history", "memory:read"),
    ("POST", "/ops/query", "ops:query"),
    ("GET", "/status", "incidents:read"),
]


def token(role: str) -> str:
    return mint(
        JWT_SECRET, sub=f"u-{role}", name=role, tenant="default", roles=[role], issuer=ISSUER
    )


@asynccontextmanager
async def app_client(settings):
    configured = settings.model_copy(
        update={
            "jwt_secret": SecretStr(JWT_SECRET),
            "jwt_issuer": ISSUER,
            "llm_rate_limit_per_minute": 100_000,
        }
    )
    app = create_app(
        configured,
        llm_provider=FakeProvider([]),
        retriever=StaticRetriever(retrieved(*chunks_of("rb-orders-db-pool", POOL))),
    )
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client,
    ):
        yield app, client


async def test_every_route_enforces_its_permission_for_every_role(settings):
    seen = []
    async with app_client(settings) as (_, client):
        for method, path, permission in MATRIX:
            anonymous = await client.request(method, path)
            seen.append((path, "anonymous", anonymous.status_code, 401))
            for role, permissions in ROLES.items():
                headers = {"Authorization": f"Bearer {token(role)}"}
                response = await client.request(method, path, headers=headers)
                if permission in permissions:
                    allowed = response.status_code not in (401, 403)
                    seen.append((path, role, response.status_code, "allowed" if allowed else "no"))
                else:
                    seen.append((path, role, response.status_code, 403))

    wrong = [s for s in seen if s[3] not in ("allowed", s[2])]
    assert wrong == [], wrong
    forbidden = sum(
        perm not in permissions for _, _, perm in MATRIX for permissions in ROLES.values()
    )
    assert forbidden > 0 and sum(s[3] == 403 for s in seen) == forbidden
    assert sum(s[3] == "allowed" for s in seen) == len(MATRIX) * len(ROLES) - forbidden


async def test_a_forbidden_call_is_a_problem_naming_the_permission(settings):
    async with app_client(settings) as (_, client):
        response = await client.post(
            f"/incidents/{I}/triage/{R}/decision",
            json={"decision": "approve"},
            headers={"Authorization": f"Bearer {token('viewer')}"},
        )

    assert response.status_code == 403
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "forbidden" and "triage:decide" in response.json()["detail"]


@pytest.mark.parametrize(
    "bad",
    [
        "not-a-jwt",
        mint(
            "another-secret-0123456789abcdef00",
            sub="u",
            name="u",
            tenant="default",
            roles=["admin"],
            issuer=ISSUER,
        ),
        mint(JWT_SECRET, sub="u", name="u", tenant="default", roles=["admin"], issuer="evil"),
        mint(
            JWT_SECRET,
            sub="u",
            name="u",
            tenant="default",
            roles=["admin"],
            issuer=ISSUER,
            ttl_s=-3600,
        ),
    ],
    ids=["garbage", "other-secret", "other-issuer", "expired"],
)
async def test_bad_tokens_get_401_on_the_api(settings, bad):
    async with app_client(settings) as (_, client):
        response = await client.get(f"/incidents/{I}", headers={"Authorization": f"Bearer {bad}"})

    assert response.status_code == 401 and response.json()["code"] == "unauthorized"


async def test_the_matrix_covers_every_route(settings):
    async with app_client(settings) as (app, _):
        # The OpenAPI schema lists every route, however the routers are nested.
        routes = {
            (method.upper(), path)
            for path, operations in app.openapi()["paths"].items()
            for method in operations
            if path != "/health"
        }

    def template(path: str) -> str:
        path = path.split("?")[0]
        for value, name in ((str(I), "{incident_id}"), (str(R), "{run_id}"), (str(N), "{note_id}")):
            path = path.replace(value, name)
        return path

    assert {(method, template(path)) for method, path, _ in MATRIX} == routes
