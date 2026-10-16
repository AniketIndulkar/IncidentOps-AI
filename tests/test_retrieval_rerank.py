"""AIENG-31: a reranker behind the Retriever interface."""

import sys
import types
from collections.abc import Sequence
from types import SimpleNamespace
from typing import ClassVar

import pytest

from incidentops_ai.retrieval.bm25 import Bm25Retriever
from incidentops_ai.retrieval.chunking import Chunk, FixedChunker
from incidentops_ai.retrieval.documents import parse_document
from incidentops_ai.retrieval.rerank import (
    CrossEncoderReranker,
    FakeReranker,
    RerankerError,
    RerankingRetriever,
)
from incidentops_ai.retrieval.retriever import RetrievalFilter, RetrievedChunk

DEFAULT = RetrievalFilter(tenant_id="default")


def chunks_of(doc_id: str, body: str, *, tenant: str = "default") -> list[Chunk]:
    raw = f"---\nid: {doc_id}\ntype: runbook\ntitle: {doc_id}\nservice: orders-api\n---\n{body}"
    document = parse_document(raw, source_path=f"{doc_id}.md", tenant_id=tenant)
    return FixedChunker(size=2000, overlap=0).chunk(document)


def ranked(*chunks: Chunk) -> list[RetrievedChunk]:
    return [RetrievedChunk(chunk=c, score=1.0 / i, rank=i) for i, c in enumerate(chunks, start=1)]


class Base:
    """A retriever that returns fixed results and remembers the `k` it was asked for."""

    name = "base"

    def __init__(self, results: list[RetrievedChunk]) -> None:
        self.results = results
        self.asked_k: int | None = None

    async def retrieve(self, query: str, *, k: int, filters: RetrievalFilter):
        self.asked_k = k
        return self.results[:k]


class Scripted:
    """A reranker that returns given scores and remembers what it was shown."""

    name = "scripted"

    def __init__(self, scores: Sequence[float]) -> None:
        self._scores = list(scores)
        self.calls: list[tuple[str, list[str]]] = []

    async def score(self, query: str, passages: Sequence[str]) -> list[float]:
        self.calls.append((query, list(passages)))
        return self._scores


async def test_candidates_are_reordered_by_the_reranker_scores():
    [a], [b], [c] = (chunks_of(name, "pool") for name in "abc")
    reranker = Scripted([0.1, 0.2, 0.9])

    results = await RerankingRetriever(Base(ranked(a, b, c)), reranker).retrieve(
        "pool", k=3, filters=DEFAULT
    )

    assert [r.chunk.doc_id for r in results] == ["c", "b", "a"]
    assert [r.rank for r in results] == [1, 2, 3]
    assert [r.score for r in results] == [0.9, 0.2, 0.1]


async def test_equal_reranker_scores_keep_the_order_of_the_base_retriever():
    [a], [b], [c] = (chunks_of(name, "pool") for name in "abc")

    results = await RerankingRetriever(Base(ranked(a, b, c)), Scripted([0.5, 0.5, 0.5])).retrieve(
        "pool", k=3, filters=DEFAULT
    )

    assert [r.chunk.doc_id for r in results] == ["a", "b", "c"]


async def test_only_the_top_depth_candidates_are_reranked_and_k_cuts_the_result():
    chunks = [c for name in "abcde" for c in chunks_of(name, "pool")]
    base, reranker = Base(ranked(*chunks)), Scripted([0.1, 0.9])

    results = await RerankingRetriever(base, reranker, depth=2).retrieve(
        "pool", k=1, filters=DEFAULT
    )

    assert base.asked_k == 2
    assert len(reranker.calls[0][1]) == 2
    assert [r.chunk.doc_id for r in results] == ["b"]  # "c", "d" and "e" were never candidates


async def test_the_reranker_sees_the_query_and_each_chunk_with_its_context_header():
    [chunk] = chunks_of("orders-db-pool", "connections are limited per pod")
    reranker = Scripted([1.0])

    await RerankingRetriever(Base(ranked(chunk)), reranker).retrieve(
        "how many connections", k=1, filters=DEFAULT
    )

    query, passages = reranker.calls[0]
    assert query == "how many connections"
    assert passages == [chunk.embedding_text(with_context=True)]
    assert passages[0].startswith("orders-db-pool")


async def test_a_wrong_number_of_scores_is_an_error_not_a_silent_misorder():
    [a], [b] = chunks_of("a", "pool"), chunks_of("b", "pool")

    with pytest.raises(RerankerError, match="returned 1 scores for 2 candidates"):
        await RerankingRetriever(Base(ranked(a, b)), Scripted([0.9])).retrieve(
            "pool", k=2, filters=DEFAULT
        )


async def test_no_candidates_means_the_reranker_is_not_called():
    reranker = Scripted([])

    results = await RerankingRetriever(Base([]), reranker).retrieve("pool", k=5, filters=DEFAULT)

    assert results == []
    assert reranker.calls == []


async def test_k_must_be_positive():
    with pytest.raises(ValueError, match="k must"):
        await RerankingRetriever(Base([]), Scripted([])).retrieve("pool", k=0, filters=DEFAULT)


async def test_filters_still_decide_which_tenant_is_searched():
    chunks = [
        *chunks_of("mine", "pool exhausted"),
        *chunks_of("theirs", "pool exhausted", tenant="acme"),
    ]
    stack = RerankingRetriever(Bm25Retriever(chunks), FakeReranker())

    mine = await stack.retrieve("pool", k=5, filters=DEFAULT)
    theirs = await stack.retrieve("pool", k=5, filters=RetrievalFilter("acme"))

    assert [r.chunk.doc_id for r in mine] == ["mine"]
    assert [r.chunk.doc_id for r in theirs] == ["theirs"]
    assert stack.name == "bm25+rerank"


async def test_the_fake_reranker_scores_the_share_of_query_words_a_passage_contains():
    scores = await FakeReranker().score(
        "connection pool exhausted",
        ["the connection pool is exhausted", "pool settings", "cache purge"],
    )

    assert scores == pytest.approx([1.0, 1 / 3, 0.0])
    assert await FakeReranker().score("what is the", ["anything"]) == [0.0]  # only stop words


def test_the_real_reranker_is_not_loaded_until_it_is_used():
    reranker = CrossEncoderReranker()

    assert reranker.load_seconds is None
    assert reranker.truncated == 0
    assert reranker.name.endswith("ms-marco-MiniLM-L-6-v2")


async def test_a_missing_runtime_says_how_to_install_it(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setitem(sys.modules, "fastembed", None)  # makes `import fastembed` fail

    with pytest.raises(RerankerError, match="uv sync --extra rerank"):
        await CrossEncoderReranker().score("q", ["a passage"])


class StubEncoding:
    def __init__(self, overflowing: list[int]) -> None:
        self.overflowing = overflowing


class StubTokenizer:
    """Pretends any passage longer than 10 characters does not fit the model's input."""

    def encode(self, query: str, passage: str) -> StubEncoding:
        return StubEncoding([1] if len(passage) > 10 else [])


class StubTextCrossEncoder:
    loaded: ClassVar[list[str]] = []

    def __init__(self, model_name: str) -> None:
        StubTextCrossEncoder.loaded.append(model_name)
        self.model = SimpleNamespace(tokenizer=StubTokenizer())

    def rerank(self, query: str, passages: Sequence[str]):
        return (len(passage) / 100 for passage in passages)


def install_stub_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stands in for fastembed, so the loading path runs without the extra or a download."""
    StubTextCrossEncoder.loaded = []
    module = types.ModuleType("fastembed.rerank.cross_encoder")
    module.TextCrossEncoder = StubTextCrossEncoder
    monkeypatch.setitem(sys.modules, "fastembed", types.ModuleType("fastembed"))
    monkeypatch.setitem(sys.modules, "fastembed.rerank", types.ModuleType("fastembed.rerank"))
    monkeypatch.setitem(sys.modules, "fastembed.rerank.cross_encoder", module)


async def test_the_model_loads_once_on_first_use_and_scores_come_back_as_floats(
    monkeypatch: pytest.MonkeyPatch,
):
    install_stub_runtime(monkeypatch)
    reranker = CrossEncoderReranker("some/model")

    first = await reranker.score("q", ["short", "a much longer passage"])
    second = await reranker.score("q", ["short"])

    assert StubTextCrossEncoder.loaded == ["some/model"]
    assert first == pytest.approx([0.05, 0.21]) and second == pytest.approx([0.05])
    assert all(isinstance(score, float) for score in first)
    assert reranker.load_seconds is not None and reranker.load_seconds >= 0
    assert await reranker.score("q", []) == []


async def test_truncated_passages_are_counted_exactly_from_the_models_own_tokenizer(
    monkeypatch: pytest.MonkeyPatch,
):
    install_stub_runtime(monkeypatch)
    reranker = CrossEncoderReranker()

    await reranker.score("q", ["short", "a much longer passage", "another long passage here"])
    await reranker.score("q", ["a much longer passage"])

    assert reranker.truncated == 3
