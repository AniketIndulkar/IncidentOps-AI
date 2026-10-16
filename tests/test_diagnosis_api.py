"""POST /incidents/{id}/diagnosis, with a scripted model and a fixed retriever: no real calls."""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from test_analysis_api import INCIDENT, runs
from test_diagnosis import POOL, StaticRetriever, cause, chunks_of, model_says, retrieved

from app.main import create_app
from incidentops_ai.config import Settings
from incidentops_ai.llm.fake_provider import FakeProvider
from incidentops_ai.llm.provider import LLMStatus
from incidentops_ai.retrieval.embeddings import EmbeddingError

KEY = {"Idempotency-Key": "diagnose-me-0001"}
GOOD = ("E1", "exhausts it within minutes")


def pool_retriever() -> StaticRetriever:
    return StaticRetriever(retrieved(*chunks_of("orders-db-pool", POOL)))


@asynccontextmanager
async def client_for(
    settings: Settings,
    provider: FakeProvider | None,
    retriever: StaticRetriever | None,
    *,
    token: bool = True,
) -> AsyncIterator[AsyncClient]:
    app = create_app(settings, llm_provider=provider, retriever=retriever)
    headers = {"Authorization": f"Bearer {settings.api_token.get_secret_value()}"} if token else {}
    async with (
        app.router.lifespan_context(app),
        AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t", headers=headers
        ) as client,
    ):
        yield client


async def new_incident(client: AsyncClient) -> str:
    return (await client.post("/incidents", json=INCIDENT)).json()["id"]


async def test_a_diagnosis_comes_back_with_checked_citations_and_provenance(
    settings, session_factory
):
    retriever = pool_retriever()
    provider = FakeProvider([model_says(cause("A connection leak exhausts the pool.", GOOD))])
    async with client_for(settings, provider, retriever) as client:
        incident_id = await new_incident(client)

        resp = await client.post(f"/incidents/{incident_id}/diagnosis")

    assert resp.status_code == 200
    body = resp.json()
    assert body["incident_id"] == incident_id
    assert body["diagnosis"]["status"] == "answered"
    [shown] = body["diagnosis"]["causes"]
    [citation] = shown["citations"]
    assert citation["quote"] == "exhausts it within minutes"
    assert citation["doc_id"] == "orders-db-pool" and citation["source_path"] == "orders-db-pool.md"
    assert citation["quote_end"] - citation["quote_start"] == len(citation["quote"])
    assert body["retrieval"]["stack"] == "static" and body["retrieval"]["latency_ms"] >= 0
    assert body["run"]["prompt_name"] == "diagnose_incident"
    # Retrieval used the incident's own words, for the default tenant, with no service filter.
    [(query, _, filters)] = retriever.asked
    assert query == f"{INCIDENT['title']}\n\n{INCIDENT['description']}"
    assert (filters.tenant_id, filters.services) == ("default", None)

    [run] = await runs(session_factory)
    assert str(run["id"]) == body["run_id"]
    assert (run["operation"], run["status"]) == ("diagnose_incident", "succeeded")
    assert run["output"]["diagnosis"]["evidence"][0]["evidence_id"] == "E1"
    assert run["output"]["retrieval"]["stack"] == "static"


async def test_a_fabricated_citation_is_dropped_and_the_answer_is_an_explicit_no_answer(
    settings, session_factory
):
    provider = FakeProvider([model_says(cause("Invented.", ("E1", "the database was deleted")))])
    async with client_for(settings, provider, pool_retriever()) as client:
        incident_id = await new_incident(client)

        resp = await client.post(f"/incidents/{incident_id}/diagnosis")

    assert resp.status_code == 200
    diagnosis = resp.json()["diagnosis"]
    assert (diagnosis["status"], diagnosis["no_answer_reason"]) == (
        "no_answer",
        "no_supported_cause",
    )
    assert diagnosis["causes"] == [] and diagnosis["dropped_causes"] == 1
    [run] = await runs(session_factory)
    assert run["output"]["diagnosis"]["status"] == "no_answer"


async def test_an_empty_corpus_is_a_no_evidence_answer_and_the_model_is_not_called(
    settings, session_factory
):
    provider = FakeProvider([])
    async with client_for(settings, provider, StaticRetriever([])) as client:
        incident_id = await new_incident(client)

        resp = await client.post(f"/incidents/{incident_id}/diagnosis")

    assert resp.status_code == 200
    assert resp.json()["diagnosis"]["no_answer_reason"] == "no_evidence"
    assert provider.calls == []
    [run] = await runs(session_factory)
    assert (run["provider"], run["attempts"], run["status"]) == ("none", 0, "succeeded")


async def test_no_login_means_401_and_nothing_is_retrieved_or_asked(settings):
    retriever, provider = pool_retriever(), FakeProvider([])
    async with client_for(settings, provider, retriever, token=False) as client:
        resp = await client.post(f"/incidents/{uuid.uuid4()}/diagnosis")

    assert resp.status_code == 401 and resp.json()["code"] == "unauthorized"
    assert retriever.asked == [] and provider.calls == []


async def test_an_unknown_incident_is_404_with_no_retrieval_and_no_model_call(
    settings, session_factory
):
    retriever, provider = pool_retriever(), FakeProvider([])
    async with client_for(settings, provider, retriever) as client:
        resp = await client.post(f"/incidents/{uuid.uuid4()}/diagnosis")

    assert resp.status_code == 404 and resp.json()["code"] == "incident_not_found"
    assert retriever.asked == [] and provider.calls == [] and await runs(session_factory) == []


async def test_without_a_retriever_the_endpoint_answers_503(settings):
    async with client_for(settings, FakeProvider([]), None) as client:
        incident_id = await new_incident(client)

        resp = await client.post(f"/incidents/{incident_id}/diagnosis")

        assert resp.status_code == 503 and resp.json()["code"] == "llm_not_configured"
        # The rest of the API keeps working without retrieval.
        assert (await client.get(f"/incidents/{incident_id}")).status_code == 200


async def test_the_same_idempotency_key_replays_the_first_diagnosis_without_new_calls(
    settings, session_factory
):
    retriever = pool_retriever()
    provider = FakeProvider([model_says(cause("Leak.", GOOD)), model_says(cause("Leak.", GOOD))])
    async with client_for(settings, provider, retriever) as client:
        url = f"/incidents/{await new_incident(client)}/diagnosis"

        first = await client.post(url, headers=KEY)
        second = await client.post(url, headers=KEY)

    assert first.status_code == second.status_code == 200
    assert "idempotent-replayed" not in first.headers
    assert second.headers["idempotent-replayed"] == "true"
    assert second.json()["run_id"] == first.json()["run_id"]
    assert second.json()["diagnosis"] == first.json()["diagnosis"]
    assert len(provider.calls) == 1 and len(retriever.asked) == 1
    assert len(await runs(session_factory)) == 1


async def test_a_failed_model_call_is_a_problem_and_frees_the_key_for_a_real_retry(
    settings, session_factory
):
    provider = FakeProvider([LLMStatus.TIMED_OUT, model_says(cause("Leak.", GOOD))])
    async with client_for(settings, provider, pool_retriever()) as client:
        url = f"/incidents/{await new_incident(client)}/diagnosis"

        failed = await client.post(url, headers=KEY)
        retried = await client.post(url, headers=KEY)

    assert failed.status_code == 504 and failed.json()["code"] == "llm_timed_out"
    assert failed.headers["content-type"] == "application/problem+json"
    assert "diagnosis" not in failed.json()
    assert retried.status_code == 200 and "idempotent-replayed" not in retried.headers
    first_run, second_run = await runs(session_factory)
    assert (first_run["status"], first_run["output"]) == ("timed_out", None)
    assert second_run["status"] == "succeeded"


async def test_a_failing_retrieval_is_a_502_problem_with_no_model_call_and_a_freed_key(
    settings, session_factory
):
    class Broken(StaticRetriever):
        async def retrieve(self, query, *, k, filters):
            raise EmbeddingError("embedding request timed out")

    provider = FakeProvider([model_says(cause("Leak.", GOOD))])
    async with client_for(settings, provider, Broken([])) as client:
        url = f"/incidents/{await new_incident(client)}/diagnosis"

        failed = await client.post(url, headers=KEY)

    assert failed.status_code == 502 and failed.json()["code"] == "retrieval_failed"
    assert "timed out" not in failed.text  # no internals in the problem body
    assert provider.calls == []
    async with session_factory() as session:
        keys = (await session.execute(text("SELECT count(*) FROM llm_run_keys"))).scalar_one()
    assert keys == 0
