"""AIENG-30: reciprocal rank fusion of dense and BM25 results."""

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from incidentops_ai.retrieval.bm25 import Bm25Retriever
from incidentops_ai.retrieval.chunking import Chunk, FixedChunker, SectionChunker
from incidentops_ai.retrieval.documents import load_corpus, parse_document
from incidentops_ai.retrieval.embeddings import FakeEmbedder
from incidentops_ai.retrieval.evalset import load_questions
from incidentops_ai.retrieval.hybrid import (
    CANDIDATE_DEPTH,
    RRF_K,
    HybridRetriever,
    reciprocal_rank_fusion,
)
from incidentops_ai.retrieval.ingest import ingest_documents
from incidentops_ai.retrieval.memory import InMemoryDenseRetriever
from incidentops_ai.retrieval.retriever import RetrievalFilter, RetrievedChunk
from incidentops_ai.retrieval.store import PgVectorRetriever, index_chunks, load_chunks

ROOT = Path(__file__).resolve().parent.parent
DOCUMENTS = load_corpus(ROOT / "data" / "corpus")
QUESTIONS = load_questions(ROOT / "evals" / "retrieval" / "questions.jsonl")
CHUNKER = SectionChunker(max_size=1500)
DEFAULT = RetrievalFilter(tenant_id="default")

POOL = "database connection pool exhausted, writes time out, kill idle sessions"
KAFKA = "kafka brokers unavailable, consumer cannot connect, check the broker certificate"
CACHE = "cdn cache serves a stale index page after release, purge the html entry points"


def chunks_of(doc_id: str, body: str, *, tenant: str = "default") -> list[Chunk]:
    raw = f"---\nid: {doc_id}\ntype: runbook\ntitle: {doc_id}\nservice: orders-api\n---\n{body}"
    document = parse_document(raw, source_path=f"{doc_id}.md", tenant_id=tenant)
    return FixedChunker(size=2000, overlap=0).chunk(document)


def ranked(*chunks: Chunk) -> list[RetrievedChunk]:
    return [RetrievedChunk(chunk=c, score=1.0 / i, rank=i) for i, c in enumerate(chunks, start=1)]


def test_the_standard_constants_are_used():
    assert (RRF_K, CANDIDATE_DEPTH) == (60, 50)


def test_rrf_scores_match_the_formula():
    [a], [b], [c] = chunks_of("a", POOL), chunks_of("b", KAFKA), chunks_of("c", CACHE)

    fused = reciprocal_rank_fusion([ranked(a, b, c), ranked(a, c)])

    assert [r.chunk.doc_id for r in fused] == ["a", "c", "b"]
    assert [r.rank for r in fused] == [1, 2, 3]
    assert fused[0].score == pytest.approx(1 / 61 + 1 / 61)
    assert fused[1].score == pytest.approx(1 / 63 + 1 / 62)
    assert fused[2].score == pytest.approx(1 / 62)


def test_a_chunk_in_only_one_list_still_counts():
    [a], [b] = chunks_of("a", POOL), chunks_of("b", KAFKA)

    fused = reciprocal_rank_fusion([ranked(a), ranked(b)])

    assert {r.chunk.doc_id for r in fused} == {"a", "b"}
    assert fused[0].score == pytest.approx(1 / 61)


def test_equal_scores_keep_the_order_they_were_first_seen_in():
    [a], [b], [c], [d] = (chunks_of(name, POOL) for name in "abcd")

    fused = reciprocal_rank_fusion([ranked(a, b), ranked(c, d)])

    assert [r.chunk.doc_id for r in fused] == ["a", "c", "b", "d"]


class Recording:
    """A retriever that returns fixed results and remembers the `k` it was asked for."""

    def __init__(self, name: str, results: list[RetrievedChunk]) -> None:
        self.name = name
        self._results = results
        self.asked_k: int | None = None

    async def retrieve(self, query: str, *, k: int, filters: RetrievalFilter):
        self.asked_k = k
        return self._results[:k]


async def test_each_source_is_asked_for_its_top_fifty_and_the_result_is_cut_to_k():
    chunks = [c for name in "abcd" for c in chunks_of(name, POOL)]
    dense, sparse = Recording("d", ranked(*chunks)), Recording("s", ranked(*reversed(chunks)))

    results = await HybridRetriever(dense, sparse).retrieve("pool", k=3, filters=DEFAULT)

    assert (dense.asked_k, sparse.asked_k) == (50, 50)
    assert len(results) == 3
    assert [r.rank for r in results] == [1, 2, 3]


async def test_k_beyond_the_depth_and_the_candidates_returns_every_unique_chunk():
    chunks = [c for name in "abc" for c in chunks_of(name, POOL)]
    dense, sparse = Recording("d", ranked(*chunks)), Recording("s", ranked(*chunks[:2]))

    results = await HybridRetriever(dense, sparse, depth=1).retrieve("pool", k=10, filters=DEFAULT)

    assert sorted(r.chunk.doc_id for r in results) == ["a", "b", "c"]
    assert dense.asked_k == 10  # never fewer than k, whatever the depth


async def test_k_must_be_positive():
    source = Recording("d", [])

    with pytest.raises(ValueError, match="k must"):
        await HybridRetriever(source, source).retrieve("pool", k=0, filters=DEFAULT)


async def test_hybrid_never_returns_other_tenants_from_either_source():
    chunks = [
        *chunks_of("mine", KAFKA),
        *chunks_of("theirs", POOL, tenant="acme"),
        *chunks_of("noise", CACHE),
    ]
    embedder = FakeEmbedder()
    hybrid = HybridRetriever(
        await InMemoryDenseRetriever.build(chunks, embedder, with_context=False),
        Bm25Retriever(chunks),
    )

    results = await hybrid.retrieve("connection pool exhausted", k=10, filters=DEFAULT)
    acme = await hybrid.retrieve("connection pool exhausted", k=10, filters=RetrievalFilter("acme"))

    assert {r.chunk.tenant_id for r in results} == {"default"}
    assert [r.chunk.doc_id for r in acme] == ["theirs"]


async def test_hybrid_with_pgvector_ranks_like_hybrid_in_memory(
    session_factory: async_sessionmaker[AsyncSession],
):
    async with session_factory() as session, session.begin():
        await ingest_documents(session, DOCUMENTS, CHUNKER)
        await index_chunks(session, FakeEmbedder())
    async with session_factory() as session:
        stored = await load_chunks(session, "default")
    memory_chunks = [c for d in DOCUMENTS for c in CHUNKER.chunk(d)]
    embedder = FakeEmbedder()
    memory_dense = await InMemoryDenseRetriever.build(memory_chunks, embedder, with_context=False)
    database_dense = PgVectorRetriever(session_factory, embedder)
    in_memory = HybridRetriever(memory_dense, Bm25Retriever(memory_chunks))
    in_database = HybridRetriever(database_dense, Bm25Retriever(stored))

    compared = 0
    for question in QUESTIONS:
        for services in (None, (question.service,) if question.service else None):
            filters = RetrievalFilter("default", services=services)
            dense_memory = await memory_dense.retrieve(
                question.text, k=CANDIDATE_DEPTH, filters=filters
            )
            dense_database = await database_dense.retrieve(
                question.text, k=CANDIDATE_DEPTH, filters=filters
            )
            if [r.chunk.id for r in dense_memory] != [r.chunk.id for r in dense_database]:
                # The database stores float32, so two chunks tied to 5 decimals can swap places
                # (as in test_retrieval_store). Then only the scores per rank must still agree.
                assert [r.score for r in dense_database] == pytest.approx(
                    [r.score for r in dense_memory], abs=1e-5
                ), question.id
                continue

            want = await in_memory.retrieve(question.text, k=10, filters=filters)
            got = await in_database.retrieve(question.text, k=10, filters=filters)

            assert [r.chunk.id for r in got] == [r.chunk.id for r in want], question.id
            assert [r.score for r in got] == pytest.approx([r.score for r in want]), question.id
            compared += 1

    assert compared >= 40, (
        "most questions should have identical dense inputs, or this proves little"
    )
