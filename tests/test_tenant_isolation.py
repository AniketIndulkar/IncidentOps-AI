"""AIENG-61 / H42: one tenant's data never reaches another tenant, and never its model's context.

The tenant comes from the token. Isolation is enforced where the data is read (repository
lookups, the retrieval filter, the reader login under row-level security), never by a prompt.
"""

from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient
from ops_support import reader_url
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import create_async_engine
from test_analysis_api import INCIDENT

from app.main import create_app
from incidentops_ai.auth.tokens import mint
from incidentops_ai.diagnosis.models import ModelDiagnosis
from incidentops_ai.llm.fake_provider import FakeProvider
from incidentops_ai.ops.database import OpsDatabase, OpsQueryError
from incidentops_ai.retrieval.bm25 import Bm25Retriever
from incidentops_ai.retrieval.chunking import FixedChunker
from incidentops_ai.retrieval.documents import parse_document

pytestmark = pytest.mark.redteam

SECRET = "isolation-jwt-secret-0123456789abcd"
ISSUER = "https://idp.test"


def bearer(tenant: str, role: str = "approver") -> dict[str, str]:
    token = mint(
        SECRET, sub=f"u-{tenant}", name=f"{tenant} user", tenant=tenant, roles=[role], issuer=ISSUER
    )
    return {"Authorization": f"Bearer {token}"}


def document(doc_id: str, tenant: str, body: str):
    raw = f"---\nid: {doc_id}\ntype: runbook\ntitle: {doc_id}\nservice: orders-api\n---\n{body}"
    return FixedChunker(size=2000, overlap=0).chunk(
        parse_document(raw, source_path=f"{doc_id}.md", tenant_id=tenant)
    )


SECRET_RUNBOOK = "ACME ONLY: rotate the acme-vault master key with the break-glass procedure."
DEFAULT_RUNBOOK = "Orders pool exhaustion: restart the orders-api pods after raising the pool size."


@asynccontextmanager
async def serve(settings, provider=None, *, ops_urls=None):
    configured = settings.model_copy(
        update={
            "jwt_secret": SecretStr(SECRET),
            "jwt_issuer": ISSUER,
            "llm_rate_limit_per_minute": 100_000,
            "ops_database_urls": ops_urls or {},
        }
    )
    retriever = Bm25Retriever(
        document("rb-acme-vault", "acme", SECRET_RUNBOOK)
        + document("rb-orders-pool", "default", DEFAULT_RUNBOOK)
    )
    app = create_app(configured, llm_provider=provider or FakeProvider([]), retriever=retriever)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client,
    ):
        yield client


async def test_another_tenants_incident_runs_and_files_are_not_found(settings):
    async with serve(settings) as c:
        acme = (await c.post("/incidents", json=INCIDENT, headers=bearer("acme"))).json()
        own = await c.get(f"/incidents/{acme['id']}", headers=bearer("acme"))
        other = await c.get(f"/incidents/{acme['id']}", headers=bearer("default"))
        files = await c.get(f"/incidents/{acme['id']}/attachments", headers=bearer("default"))
        runs = await c.get(f"/incidents/{acme['id']}/triage", headers=bearer("default"))
        triage = await c.post(f"/incidents/{acme['id']}/triage", headers=bearer("default"))

    assert own.status_code == 200 and own.json()["tenant_id"] == "acme"
    assert (other.status_code, files.status_code, triage.status_code) == (404, 404, 404)
    assert runs.json() == []


async def test_the_same_idempotency_key_in_two_tenants_makes_two_incidents(settings):
    key = {"Idempotency-Key": "shared-client-key-0001"}
    async with serve(settings) as c:
        first = await c.post("/incidents", json=INCIDENT, headers=bearer("acme") | key)
        second = await c.post("/incidents", json=INCIDENT, headers=bearer("default") | key)

    assert (first.status_code, second.status_code) == (201, 201)  # no replay across tenants
    assert first.json()["id"] != second.json()["id"]
    assert second.json()["tenant_id"] == "default"


async def test_memory_notes_stay_in_their_tenant(settings):
    async with serve(settings) as c:
        note = await c.post(
            "/memory", json={"service": "orders-api", "text": "acme note"}, headers=bearer("acme")
        )
        listed = await c.get("/memory", params={"service": "orders-api"}, headers=bearer("default"))
        history = await c.get(f"/memory/{note.json()['id']}/history", headers=bearer("default"))
        fork = await c.post(
            f"/memory/{note.json()['id']}/supersede", json={"text": "x"}, headers=bearer("default")
        )

    assert listed.json() == [] and history.status_code == 404 and fork.status_code == 404


async def test_another_tenants_documents_never_reach_the_prompt(settings):
    provider = FakeProvider([ModelDiagnosis(answer_status="insufficient_evidence", causes=[])] * 2)
    incident = {**INCIDENT, "description": "Orders pool exhausted; also rotate the vault key."}
    async with serve(settings, provider) as c:
        mine = (await c.post("/incidents", json=incident, headers=bearer("default"))).json()
        theirs = (await c.post("/incidents", json=incident, headers=bearer("acme"))).json()
        await c.post(f"/incidents/{mine['id']}/diagnosis", headers=bearer("default"))
        await c.post(f"/incidents/{theirs['id']}/diagnosis", headers=bearer("acme"))

    default_prompt, acme_prompt = (call.prompt.input for call in provider.calls)
    assert "acme-vault" not in default_prompt and "restart the orders-api" in default_prompt
    assert "acme-vault" in acme_prompt and "restart the orders-api" not in acme_prompt


async def test_ops_rows_are_isolated_by_the_reader_login(settings):
    engines = {
        tenant: create_async_engine(await reader_url(settings.database_url, tenant))
        for tenant in ("default", "acme")
    }
    try:
        default = await OpsDatabase(engines["default"]).run("SELECT name FROM ops.services")
        acme = await OpsDatabase(engines["acme"]).run("SELECT name FROM ops.services")
        # Validator bypassed: a query that tries to switch tenants still sees its own rows only.
        switched = await OpsDatabase(engines["default"]).run(
            "SELECT set_config('app.tenant_id', 'acme', false), name FROM ops.services"
        )
        with pytest.raises(OpsQueryError):
            await OpsDatabase(engines["default"]).run("SET SESSION AUTHORIZATION ops_reader_acme")
    finally:
        for engine in engines.values():
            await engine.dispose()

    assert {r[0] for r in acme.rows} == {"acme-storefront", "acme-payments"}
    assert "acme-storefront" not in {r[0] for r in default.rows} and len(default.rows) == 9
    assert "acme-storefront" not in {r[1] for r in switched.rows}


async def test_the_ops_api_answers_from_the_callers_tenant(settings):
    from incidentops_ai.ops.text_to_sql import SqlAnswer

    reply = SqlAnswer(answerable=True, sql="SELECT name FROM ops.services", explanation="All.")
    urls = {t: await reader_url(settings.database_url, t) for t in ("default", "acme")}
    async with serve(settings, FakeProvider([reply, reply]), ops_urls=urls) as c:
        acme = await c.post("/ops/query", json={"question": "Services?"}, headers=bearer("acme"))
        default = await c.post(
            "/ops/query", json={"question": "Services?"}, headers=bearer("default")
        )

    assert {r[0] for r in acme.json()["rows"]} == {"acme-storefront", "acme-payments"}
    assert len(default.json()["rows"]) == 9


async def test_a_tenant_without_an_ops_reader_gets_none(settings):
    urls = {"default": await reader_url(settings.database_url, "default")}
    async with serve(settings, ops_urls=urls) as c:
        response = await c.post(
            "/ops/query", json={"question": "Services?"}, headers=bearer("globex")
        )

    assert response.status_code == 503 and response.json()["code"] == "ops_not_configured"


async def test_the_related_incidents_tool_reads_only_the_callers_tenant(session_factory):
    from incidentops_ai.incidents.models import IncidentCreate
    from incidentops_ai.incidents.repository import IncidentRepository
    from incidentops_ai.tools.recent_incidents import (
        GetRecentIncidentsArgs,
        make_recent_incidents_tool,
    )

    async with session_factory() as session:
        for tenant in ("acme", "default"):
            await IncidentRepository(session).create(
                IncidentCreate(**{**INCIDENT, "title": f"{tenant} outage"}), tenant_id=tenant
            )
    tool = make_recent_incidents_tool(session_factory, tenant_id="default")

    found = await tool.handler(GetRecentIncidentsArgs(service=INCIDENT["service"], limit=10))

    assert [i["title"] for i in found] == ["default outage"]
