"""pgvector indexing and retrieval against real Postgres, with the offline FakeEmbedder."""

from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from incidentops_ai.retrieval.chunking import FixedChunker, SectionChunker
from incidentops_ai.retrieval.documents import DocType, load_corpus, parse_document
from incidentops_ai.retrieval.embeddings import FakeEmbedder
from incidentops_ai.retrieval.evalset import load_questions
from incidentops_ai.retrieval.ingest import ingest_documents
from incidentops_ai.retrieval.memory import InMemoryDenseRetriever
from incidentops_ai.retrieval.orm import ChunkRow
from incidentops_ai.retrieval.retriever import RetrievalFilter
from incidentops_ai.retrieval.store import PgVectorRetriever, index_chunks, load_chunks

ROOT = Path(__file__).resolve().parent.parent
DOCUMENTS = load_corpus(ROOT / "data" / "corpus")
QUESTIONS = load_questions(ROOT / "evals" / "retrieval" / "questions.jsonl")
CHUNKER = SectionChunker(max_size=1500)
DEFAULT = RetrievalFilter(tenant_id="default")

Factory = async_sessionmaker[AsyncSession]


async def ingest(factory: Factory, documents=DOCUMENTS, chunker=CHUNKER, tenant_id="default"):
    async with factory() as session, session.begin():
        await ingest_documents(session, documents, chunker, tenant_id=tenant_id)


async def index(factory: Factory, embedder=None, **kwargs) -> int:
    async with factory() as session, session.begin():
        return await index_chunks(session, embedder or FakeEmbedder(), **kwargs)


async def count(factory: Factory, *where) -> int:
    async with factory() as session:
        stmt = select(func.count()).select_from(ChunkRow).where(*where)
        return (await session.execute(stmt)).scalar_one()


async def test_indexing_embeds_every_chunk_and_records_how(session_factory: Factory):
    await ingest(session_factory)
    total = await count(session_factory)

    embedded = await index(session_factory)

    assert embedded == total > 50
    assert await count(session_factory, ChunkRow.embedding.is_not(None)) == total
    async with session_factory() as session:
        row = (await session.execute(select(ChunkRow).limit(1))).scalar_one()
    assert (row.embedding_model, row.embedding_context) == ("fake-hash-1536", False)
    assert len(row.embedding) == 1536


async def test_indexing_twice_embeds_nothing_the_second_time(session_factory: Factory):
    await ingest(session_factory)
    await index(session_factory)

    assert await index(session_factory) == 0


async def test_a_different_model_or_context_setting_re_embeds_everything(session_factory: Factory):
    await ingest(session_factory)
    total = await index(session_factory)
    other = FakeEmbedder()
    other.model = "fake-other"

    assert await index(session_factory, other) == total
    assert await index(session_factory, other, with_context=True) == total
    assert await count(session_factory, ChunkRow.embedding_model == "fake-other") == total


async def test_only_changed_documents_are_embedded_again(session_factory: Factory):
    await ingest(session_factory)
    await index(session_factory)
    changed = parse_document(
        "---\nid: rb-general-triage\ntype: runbook\ntitle: Triage\n---\n# Triage\n\nBrand new text.\n",
        source_path="runbooks/general-incident-triage.md",
    )
    others = [d for d in DOCUMENTS if d.id != "rb-general-triage"]
    await ingest(session_factory, [*others, changed])

    assert await index(session_factory) == 1


async def test_an_embedder_of_the_wrong_size_is_refused_before_anything_is_written(
    session_factory: Factory,
):
    await ingest(session_factory)

    with pytest.raises(ValueError, match="1536"):
        await index(session_factory, FakeEmbedder(dimensions=64))

    assert await count(session_factory, ChunkRow.embedding.is_not(None)) == 0


async def test_pgvector_ranks_exactly_like_the_in_memory_reference(session_factory: Factory):
    await ingest(session_factory)
    await index(session_factory)
    embedder = FakeEmbedder()
    chunks = [c for d in DOCUMENTS for c in CHUNKER.chunk(d)]
    reference = await InMemoryDenseRetriever.build(chunks, embedder, with_context=False)
    store = PgVectorRetriever(session_factory, embedder)

    for question in QUESTIONS:
        for services in (None, (question.service,) if question.service else None):
            filters = RetrievalFilter("default", services=services)
            expected = await reference.retrieve(question.text, k=10, filters=filters)
            actual = await store.retrieve(question.text, k=10, filters=filters)

            assert len(actual) == len(expected), question.id
            for want, got in zip(expected, actual, strict=True):
                assert got.rank == want.rank
                assert got.score == pytest.approx(want.score, abs=1e-4), question.id
                # Float32 storage may swap two chunks whose scores are equal to 5 decimals.
                assert got.chunk.id == want.chunk.id or abs(got.score - want.score) < 1e-5


async def test_results_carry_the_full_provenance(session_factory: Factory):
    await ingest(session_factory)
    await index(session_factory)
    store = PgVectorRetriever(session_factory, FakeEmbedder())

    [hit] = await store.retrieve(
        "orders database failover promote the standby", k=1, filters=DEFAULT
    )

    source = next(d for d in DOCUMENTS if d.id == hit.chunk.doc_id)
    assert hit.chunk.source_path == source.source_path
    assert hit.chunk.title == source.title
    assert hit.chunk.text == source.text[hit.chunk.start : hit.chunk.end]
    assert hit.chunk.tenant_id == "default"
    assert hit.chunk.id in {c.id for c in CHUNKER.chunk(source)}
    assert hit.rank == 1
    assert hit.chunk.doc_id == "rb-orders-db-failover"
    assert 1 <= hit.chunk.page <= hit.chunk.page_end <= 3


async def test_filters_are_applied_in_the_database(session_factory: Factory):
    await ingest(session_factory)
    await index(session_factory)
    store = PgVectorRetriever(session_factory, FakeEmbedder())

    by_service = await store.retrieve(
        "restart the pod", k=10, filters=RetrievalFilter("default", services=("billing-service",))
    )
    by_type = await store.retrieve(
        "restart the pod", k=10, filters=RetrievalFilter("default", doc_types=(DocType.POSTMORTEM,))
    )

    assert by_service and {r.chunk.service for r in by_service} == {"billing-service"}
    assert by_type and {r.chunk.doc_type for r in by_type} == {DocType.POSTMORTEM}


async def test_another_tenants_chunks_are_never_returned(session_factory: Factory):
    await ingest(session_factory)
    secret = parse_document(
        "---\nid: secret\ntype: runbook\ntitle: Secret\n---\n"
        "# Secret\n\norders database failover promote the standby with the secret password\n",
        source_path="secret.md",
        tenant_id="acme",
    )
    await ingest(session_factory, [secret], tenant_id="acme")
    await index(session_factory, tenant_id="default")
    await index(session_factory, tenant_id="acme")
    store = PgVectorRetriever(session_factory, FakeEmbedder())

    mine = await store.retrieve(
        "orders database failover promote the standby", k=20, filters=DEFAULT
    )
    theirs = await store.retrieve(
        "orders database failover promote the standby", k=20, filters=RetrievalFilter("acme")
    )

    assert mine and all(r.chunk.tenant_id == "default" for r in mine)
    assert [r.chunk.doc_id for r in theirs] == ["secret"]


async def test_chunks_that_are_not_embedded_or_embedded_by_another_model_are_not_searched(
    session_factory: Factory,
):
    await ingest(session_factory)
    store = PgVectorRetriever(session_factory, FakeEmbedder())
    assert await store.retrieve("restart the pod", k=5, filters=DEFAULT) == []  # nothing indexed

    other = FakeEmbedder()
    other.model = "fake-other"
    await index(session_factory, other)

    assert await store.retrieve("restart the pod", k=5, filters=DEFAULT) == []


async def test_k_must_be_positive(session_factory: Factory):
    store = PgVectorRetriever(session_factory, FakeEmbedder())

    with pytest.raises(ValueError, match="k must"):
        await store.retrieve("x", k=0, filters=DEFAULT)


async def test_the_fixed_chunker_stores_and_searches_too(session_factory: Factory):
    await ingest(session_factory, chunker=FixedChunker(size=500, overlap=50))
    await index(session_factory)
    store = PgVectorRetriever(session_factory, FakeEmbedder())

    results = await store.retrieve("kafka NoBrokersAvailable", k=3, filters=DEFAULT)

    assert len(results) == 3
    assert results[0].chunk.chunker == "fixed-500-50"


async def test_run_index_embeds_what_is_stored(settings, session_factory: Factory):
    from incidentops_ai.retrieval.index import run_index

    await ingest(session_factory)
    total = await count(session_factory)

    assert await run_index(settings, FakeEmbedder()) == total
    assert await run_index(settings, FakeEmbedder()) == 0


async def test_load_chunks_returns_one_tenants_stored_chunks_in_corpus_order(
    session_factory: Factory,
):
    await ingest(session_factory)
    other = parse_document(
        "---\nid: theirs\ntype: runbook\ntitle: Theirs\n---\n# Theirs\n\nnot for you\n",
        source_path="theirs.md",
        tenant_id="acme",
    )
    await ingest(session_factory, [other], tenant_id="acme")

    async with session_factory() as session:
        loaded = await load_chunks(session, "default")

    expected = [c for d in sorted(DOCUMENTS, key=lambda d: d.source_path) for c in CHUNKER.chunk(d)]
    assert [c.id for c in loaded] == [c.id for c in expected]
    assert [c.text for c in loaded] == [c.text for c in expected]
    assert {c.tenant_id for c in loaded} == {"default"}
    assert loaded[0].title and loaded[0].source_path  # provenance comes with the chunk
