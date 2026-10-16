"""AIENG-29: BM25 sparse retrieval behind the Retriever interface."""

import pytest

from incidentops_ai.retrieval.bm25 import Bm25Retriever, tokenize
from incidentops_ai.retrieval.chunking import Chunk, FixedChunker
from incidentops_ai.retrieval.documents import DocType, parse_document
from incidentops_ai.retrieval.retriever import RetrievalFilter


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


def bm25(*chunk_lists: list[Chunk], with_context: bool = False) -> Bm25Retriever:
    return Bm25Retriever(
        [c for chunk_list in chunk_lists for c in chunk_list], with_context=with_context
    )


DEFAULT = RetrievalFilter(tenant_id="default")


def test_tokenize_keeps_identifiers_whole_and_in_parts():
    tokens = tokenize("PAY-1042 INDEXER_BATCH_SIZE kafka.errors.NoBrokersAvailable")

    assert {
        "pay-1042",
        "pay",
        "1042",
        "indexer_batch_size",
        "indexer",
        "batch",
        "size",
        "kafka.errors.nobrokersavailable",
        "kafka",
        "errors",
        "nobrokersavailable",
    } <= set(tokens)
    assert all(token == token.lower() for token in tokens)


def test_tokenize_does_not_glue_sentence_punctuation_to_words():
    tokens = tokenize("Restart the pool. Then check logs, again!")

    assert "pool" in tokens
    assert "pool." not in tokens
    assert "logs" in tokens


def test_tokenize_drops_stop_words():
    tokens = tokenize("what is the memory limit")

    assert not {"what", "is", "the"} & set(tokens)
    assert {"memory", "limit"} <= set(tokens)


async def test_rare_term_beats_common_term_even_in_a_longer_chunk():
    longer = "proxy keepalive timeout is shorter than the upstream timeout, " + "filler " * 20
    r = bm25(
        chunks_of("a", longer),
        chunks_of("b", "proxy timeout settings for the gateway"),
        chunks_of("c", "proxy retries and timeout budget"),
    )

    results = await r.retrieve("proxy keepalive", k=3, filters=DEFAULT)

    assert results[0].chunk.doc_id == "a"
    assert [x.rank for x in results] == [1, 2, 3]
    assert [x.score for x in results] == sorted((x.score for x in results), reverse=True)


async def test_shorter_chunk_wins_when_term_counts_tie():
    r = bm25(
        chunks_of("long", "pool exhausted " + "unrelated filler words here " * 30),
        chunks_of("short", "pool exhausted"),
    )

    results = await r.retrieve("pool", k=2, filters=DEFAULT)

    assert [x.chunk.doc_id for x in results] == ["short", "long"]


async def test_identifier_query_matches_the_joined_form():
    r = bm25(
        chunks_of("parts", "payments gateway pay errors, retry 1042 times"),
        chunks_of("code", "PAY-1042 means the acquirer timed out"),
        chunks_of("other", "cache purge after release"),
    )

    results = await r.retrieve("PAY-1042", k=3, filters=DEFAULT)

    assert [x.chunk.doc_id for x in results] == ["code", "parts"]


@pytest.mark.parametrize("query", ["", "   ", "what is the"])
async def test_empty_and_stop_word_only_queries_return_nothing(query):
    r = bm25(chunks_of("pool", "database connection pool exhausted"))

    assert await r.retrieve(query, k=5, filters=DEFAULT) == []


async def test_tenant_isolation_in_results_and_in_statistics():
    mine = chunks_of("mine", "proxy keepalive timeout")
    others = [
        chunks_of(f"theirs{i}", "keepalive keepalive keepalive", tenant="acme") for i in range(5)
    ]
    alone = bm25(mine, chunks_of("noise", "cache purge"))
    shared = bm25(mine, chunks_of("noise", "cache purge"), *others)

    before = await alone.retrieve("keepalive", k=5, filters=DEFAULT)
    after = await shared.retrieve("keepalive", k=5, filters=DEFAULT)

    assert [x.chunk.doc_id for x in after] == ["mine"]
    assert after[0].score == pytest.approx(before[0].score, rel=1e-12)
    acme = await shared.retrieve("keepalive", k=10, filters=RetrievalFilter("acme"))
    assert {x.chunk.tenant_id for x in acme} == {"acme"}
    assert len(acme) == 5


async def test_service_and_type_filters_narrow_candidates_without_changing_scores():
    r = bm25(
        chunks_of("orders-rb", "pool exhausted", service="orders-api", doc_type="runbook"),
        chunks_of("orders-pm", "pool exhausted", service="orders-api", doc_type="postmortem"),
        chunks_of("billing-rb", "pool exhausted", service="billing-service", doc_type="runbook"),
    )

    unfiltered = {x.chunk.doc_id: x.score for x in await r.retrieve("pool", k=5, filters=DEFAULT)}
    by_service = await r.retrieve(
        "pool", k=5, filters=RetrievalFilter("default", services=("billing-service",))
    )
    by_type = await r.retrieve(
        "pool", k=5, filters=RetrievalFilter("default", doc_types=(DocType.POSTMORTEM,))
    )

    assert [x.chunk.doc_id for x in by_service] == ["billing-rb"]
    assert [x.chunk.doc_id for x in by_type] == ["orders-pm"]
    assert by_service[0].score == pytest.approx(unfiltered["billing-rb"], rel=1e-12)


async def test_k_must_be_positive_and_ties_keep_corpus_order():
    r = bm25(chunks_of("a", "pool"), chunks_of("b", "pool"), chunks_of("c", "pool"))

    with pytest.raises(ValueError, match="k must"):
        await r.retrieve("pool", k=0, filters=DEFAULT)
    results = await r.retrieve("pool", k=2, filters=DEFAULT)

    assert [x.chunk.doc_id for x in results] == ["a", "b"]


async def test_context_header_is_searchable_only_when_enabled():
    chunks = chunks_of("kubelet-eviction", "pods restart after memory pressure")

    assert await bm25(chunks).retrieve("kubelet", k=3, filters=DEFAULT) == []
    with_header = await bm25(chunks, with_context=True).retrieve("kubelet", k=3, filters=DEFAULT)
    assert [x.chunk.doc_id for x in with_header] == ["kubelet-eviction"]
