"""AIENG-32: the retrieval stack the API serves, and a BM25 index that follows re-ingestion."""

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import incidentops_ai.retrieval.stack as stack_module
from incidentops_ai.retrieval.chunking import SectionChunker
from incidentops_ai.retrieval.documents import Document, load_corpus, parse_document
from incidentops_ai.retrieval.embeddings import FakeEmbedder
from incidentops_ai.retrieval.ingest import ingest_documents
from incidentops_ai.retrieval.rerank import FakeReranker
from incidentops_ai.retrieval.retriever import RetrievalFilter
from incidentops_ai.retrieval.stack import DbBm25Retriever, build_retriever
from incidentops_ai.retrieval.store import index_chunks

ROOT = Path(__file__).resolve().parent.parent
CHUNKER = SectionChunker(max_size=1500)
DEFAULT = RetrievalFilter(tenant_id="default")
Factory = async_sessionmaker[AsyncSession]


def document(doc_id: str, body: str, *, tenant: str = "default") -> Document:
    raw = f"---\nid: {doc_id}\ntype: runbook\ntitle: {doc_id}\nservice: checkout-api\n---\n# {doc_id}\n\n{body}\n"
    return parse_document(raw, source_path=f"{doc_id}.md", tenant_id=tenant)


async def ingest(factory: Factory, *documents: Document, tenant: str = "default") -> None:
    async with factory() as session, session.begin():
        await ingest_documents(session, list(documents), CHUNKER, tenant_id=tenant)


def test_each_named_stack_can_be_built(session_factory: Factory):
    embedder = FakeEmbedder()

    assert build_retriever("dense", session_factory, embedder).name == "dense-pgvector"
    assert build_retriever("hybrid", session_factory, embedder).name == "hybrid-rrf"
    reranked = build_retriever("hybrid-rerank", session_factory, embedder, FakeReranker())
    assert reranked.name == "hybrid-rrf+rerank"


def test_an_unknown_stack_and_a_missing_reranker_are_refused(session_factory: Factory):
    embedder = FakeEmbedder()

    with pytest.raises(ValueError, match="unknown stack 'magic'.*dense, hybrid, hybrid-rerank"):
        build_retriever("magic", session_factory, embedder)
    with pytest.raises(ValueError, match="hybrid-rerank needs a reranker"):
        build_retriever("hybrid-rerank", session_factory, embedder)


async def test_the_bm25_index_is_built_from_the_stored_chunks(session_factory: Factory):
    await ingest(
        session_factory,
        document("keepalive", "The proxy keepalive timeout is shorter than upstream."),
        document("cache", "The cdn cache serves a stale index page."),
    )

    found = await DbBm25Retriever(session_factory).retrieve("keepalive", k=5, filters=DEFAULT)

    assert [r.chunk.doc_id for r in found] == ["keepalive"]
    assert found[0].chunk.title == "keepalive" and found[0].chunk.source_path == "keepalive.md"


async def test_the_bm25_index_follows_a_re_ingested_document_without_a_restart(
    session_factory: Factory,
):
    retriever = DbBm25Retriever(session_factory)
    await ingest(session_factory, document("a", "The proxy keepalive timeout is too short."))
    assert [
        r.chunk.doc_id for r in await retriever.retrieve("keepalive", k=5, filters=DEFAULT)
    ] == ["a"]

    await ingest(session_factory, document("a", "The rollback command is kubectl rollout undo."))

    assert await retriever.retrieve("keepalive", k=5, filters=DEFAULT) == []
    assert [r.chunk.doc_id for r in await retriever.retrieve("rollback", k=5, filters=DEFAULT)] == [
        "a"
    ]


async def test_a_new_document_is_found_and_a_pruned_one_is_gone(session_factory: Factory):
    retriever = DbBm25Retriever(session_factory)
    assert await retriever.retrieve("keepalive", k=5, filters=DEFAULT) == []  # empty corpus

    await ingest(session_factory, document("a", "The proxy keepalive timeout is too short."))
    assert len(await retriever.retrieve("keepalive", k=5, filters=DEFAULT)) == 1

    async with session_factory() as session, session.begin():
        await ingest_documents(
            session, [document("b", "Unrelated cache note.")], CHUNKER, prune=True
        )
    assert await retriever.retrieve("keepalive", k=5, filters=DEFAULT) == []


async def test_the_index_is_not_rebuilt_while_nothing_changes(
    session_factory: Factory, monkeypatch: pytest.MonkeyPatch
):
    loads = []
    real = stack_module.load_chunks

    async def counting(session, tenant_id):
        loads.append(tenant_id)
        return await real(session, tenant_id)

    monkeypatch.setattr(stack_module, "load_chunks", counting)
    await ingest(session_factory, document("a", "The proxy keepalive timeout is too short."))
    retriever = DbBm25Retriever(session_factory)

    for _ in range(3):
        await retriever.retrieve("keepalive", k=5, filters=DEFAULT)

    assert loads == ["default"]


async def test_each_tenant_gets_its_own_index(session_factory: Factory):
    await ingest(session_factory, document("mine", "keepalive timeout"))
    await ingest(
        session_factory, document("theirs", "keepalive timeout", tenant="acme"), tenant="acme"
    )
    retriever = DbBm25Retriever(session_factory)

    mine = await retriever.retrieve("keepalive", k=5, filters=DEFAULT)
    theirs = await retriever.retrieve("keepalive", k=5, filters=RetrievalFilter("acme"))
    nobody = await retriever.retrieve("keepalive", k=5, filters=RetrievalFilter("nobody"))

    assert [r.chunk.doc_id for r in mine] == ["mine"]
    assert [r.chunk.doc_id for r in theirs] == ["theirs"]
    assert nobody == []


async def test_a_stack_built_here_answers_over_the_real_corpus(session_factory: Factory):
    corpus = load_corpus(ROOT / "data" / "corpus")
    async with session_factory() as session, session.begin():
        await ingest_documents(session, corpus, CHUNKER)
        await index_chunks(session, FakeEmbedder())
    hybrid = build_retriever("hybrid", session_factory, FakeEmbedder())

    results = await hybrid.retrieve("INDEXER_BATCH_SIZE safe default", k=5, filters=DEFAULT)

    assert len(results) == 5 and results[0].chunk.tenant_id == "default"
    assert any("search-indexer" in r.chunk.doc_id for r in results)
