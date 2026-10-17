import pytest

from incidentops_ai.retrieval.chunking import Chunk, FixedChunker
from incidentops_ai.retrieval.documents import DocType, parse_document
from incidentops_ai.retrieval.embeddings import FakeEmbedder
from incidentops_ai.retrieval.memory import InMemoryDenseRetriever
from incidentops_ai.retrieval.retriever import QueryHints, RetrievalFilter


def chunks_of(
    doc_id: str,
    body: str,
    *,
    tenant: str = "default",
    service: str | None = "orders-api",
    doc_type: str = "runbook",
) -> list[Chunk]:
    raw = f"---\nid: {doc_id}\ntype: {doc_type}\ntitle: {doc_id}\nservice: {service or ''}\n---\n{body}"
    document = parse_document(raw, source_path=f"{doc_id}.md", tenant_id=tenant)
    return FixedChunker(size=2000, overlap=0).chunk(document)


POOL = "database connection pool exhausted, writes time out, raise nothing, kill idle sessions"
KAFKA = "kafka brokers unavailable, consumer cannot connect, check the broker certificate"
CACHE = "cdn cache serves a stale index page after release, purge the html entry points"


async def retriever(*chunk_lists: list[Chunk]) -> InMemoryDenseRetriever:
    chunks = [c for chunk_list in chunk_lists for c in chunk_list]
    return await InMemoryDenseRetriever.build(chunks, FakeEmbedder(), with_context=False)


DEFAULT = RetrievalFilter(tenant_id="default")


async def test_the_most_similar_chunk_comes_first_with_ranks_and_scores():
    r = await retriever(chunks_of("pool", POOL), chunks_of("kafka", KAFKA), chunks_of("cdn", CACHE))

    results = await r.retrieve("connection pool exhausted", k=3, filters=DEFAULT)

    assert results[0].chunk.doc_id == "pool"
    assert [x.rank for x in results] == [1, 2, 3]
    assert [x.score for x in results] == sorted((x.score for x in results), reverse=True)


async def test_k_limits_the_number_of_results():
    r = await retriever(chunks_of("pool", POOL), chunks_of("kafka", KAFKA), chunks_of("cdn", CACHE))

    assert len(await r.retrieve("pool", k=2, filters=DEFAULT)) == 2
    assert len(await r.retrieve("pool", k=10, filters=DEFAULT)) == 3


async def test_other_tenants_are_never_returned():
    r = await retriever(
        chunks_of("mine", KAFKA),
        chunks_of("theirs", POOL, tenant="acme"),
    )

    results = await r.retrieve("connection pool exhausted", k=5, filters=DEFAULT)

    assert [x.chunk.doc_id for x in results] == ["mine"]
    acme = await r.retrieve("connection pool exhausted", k=5, filters=RetrievalFilter("acme"))
    assert [x.chunk.doc_id for x in acme] == ["theirs"]


async def test_service_and_type_filters_narrow_the_search_and_combine():
    r = await retriever(
        chunks_of("orders-rb", POOL, service="orders-api", doc_type="runbook"),
        chunks_of("orders-pm", POOL, service="orders-api", doc_type="postmortem"),
        chunks_of("billing-rb", POOL, service="billing-service", doc_type="runbook"),
    )

    by_service = await r.retrieve(
        "pool", k=5, filters=RetrievalFilter("default", services=("billing-service",))
    )
    by_type = await r.retrieve(
        "pool", k=5, filters=RetrievalFilter("default", doc_types=(DocType.POSTMORTEM,))
    )
    both = await r.retrieve(
        "pool",
        k=5,
        filters=RetrievalFilter("default", services=("orders-api",), doc_types=(DocType.RUNBOOK,)),
    )

    assert [x.chunk.doc_id for x in by_service] == ["billing-rb"]
    assert [x.chunk.doc_id for x in by_type] == ["orders-pm"]
    assert [x.chunk.doc_id for x in both] == ["orders-rb"]


async def test_a_filter_that_matches_nothing_returns_nothing():
    r = await retriever(chunks_of("pool", POOL))

    assert (
        await r.retrieve("pool", k=5, filters=RetrievalFilter("default", services=("nope",))) == []
    )


async def test_equal_scores_keep_a_stable_order():
    r = await retriever(chunks_of("a", POOL), chunks_of("b", POOL), chunks_of("c", POOL))

    results = await r.retrieve("pool", k=3, filters=DEFAULT)

    assert [x.chunk.doc_id for x in results] == ["a", "b", "c"]


def test_a_tenant_is_required():
    with pytest.raises(ValueError, match="tenant"):
        RetrievalFilter(tenant_id="  ")


async def test_k_must_be_positive():
    r = await retriever(chunks_of("pool", POOL))

    with pytest.raises(ValueError, match="k must"):
        await r.retrieve("pool", k=0, filters=DEFAULT)


async def test_results_point_at_their_source_span():
    r = await retriever(chunks_of("pool", POOL))

    [hit] = await r.retrieve("pool", k=1, filters=DEFAULT)

    assert (hit.doc_id, hit.start, hit.end) == ("pool", hit.chunk.start, hit.chunk.end)


def test_a_document_filter_keeps_only_the_listed_documents():
    [pool] = chunks_of("pool", POOL)
    [kafka] = chunks_of("kafka", KAFKA)
    only_pool = RetrievalFilter("default", doc_ids=("pool",))

    assert only_pool.allows(pool) and not only_pool.allows(kafka)


async def test_the_dense_retriever_applies_the_document_filter_and_accepts_hints():
    r = await retriever(chunks_of("pool", POOL), chunks_of("kafka", KAFKA), chunks_of("cdn", CACHE))

    results = await r.retrieve(
        "connection pool exhausted",
        k=5,
        filters=RetrievalFilter("default", doc_ids=("kafka", "cdn")),
        hints=QueryHints(services=("orders-api",)),
    )

    assert {x.chunk.doc_id for x in results} == {"kafka", "cdn"}
