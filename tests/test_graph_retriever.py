"""AIENG-37: retrieval inside the documents an anchor service points at, fused with open retrieval."""

from pathlib import Path

import pytest

from incidentops_ai.graph.catalogue import load_catalogue
from incidentops_ai.graph.memory import InMemoryGraphStore
from incidentops_ai.graph.retriever import (
    AnchoredRetriever,
    GraphNeighbourhood,
    OwnDocuments,
    link_services,
)
from incidentops_ai.retrieval.chunking import Chunk, FixedChunker
from incidentops_ai.retrieval.documents import parse_document
from incidentops_ai.retrieval.retriever import QueryHints, RetrievalFilter, RetrievedChunk

ROOT = Path(__file__).resolve().parent.parent
GRAPH = InMemoryGraphStore([load_catalogue(ROOT / "data" / "graph" / "catalogue.toml")])
DEFAULT = RetrievalFilter(tenant_id="default")


def chunk(doc_id: str, service: str) -> Chunk:
    raw = f"---\nid: {doc_id}\ntype: runbook\ntitle: {doc_id}\nservice: {service}\n---\nsome text\n"
    [only] = FixedChunker(size=2000, overlap=0).chunk(
        parse_document(raw, source_path=f"{doc_id}.md")
    )
    return only


class ListRetriever:
    """Returns its chunks in a fixed order, keeping those the filter allows. Records every call."""

    name = "list"

    def __init__(self, chunks: list[Chunk]) -> None:
        self._chunks = chunks
        self.calls: list[RetrievalFilter] = []

    async def retrieve(self, query, *, k, filters, hints=None):
        self.calls.append(filters)
        allowed = [c for c in self._chunks if filters.allows(c)][:k]
        return [RetrievedChunk(chunk=c, score=1.0 / i, rank=i) for i, c in enumerate(allowed, 1)]


CHUNKS = [
    chunk("rb-checkout-502", "checkout-api"),
    chunk("rb-search-indexer-oom", "search-indexer"),
    chunk("rb-payments-latency", "payments-gateway"),
    chunk("pm-2026-09-billing", "billing-service"),
]


async def test_without_an_anchor_the_open_results_are_returned_unchanged():
    base = ListRetriever(CHUNKS)

    results = await AnchoredRetriever(base, OwnDocuments()).retrieve(
        "what now", k=2, filters=DEFAULT
    )

    assert [r.chunk.doc_id for r in results] == ["rb-checkout-502", "rb-search-indexer-oom"]
    assert len(base.calls) == 1


async def test_the_anchors_own_documents_are_fused_in_and_move_up():
    base = ListRetriever(CHUNKS)

    results = await AnchoredRetriever(base, OwnDocuments()).retrieve(
        "what now", k=3, filters=DEFAULT, hints=QueryHints(services=("payments-gateway",))
    )

    assert results[0].chunk.doc_id == "rb-payments-latency"
    assert base.calls[1].services == ("payments-gateway",)
    assert [r.rank for r in results] == [1, 2, 3]


async def test_the_graph_scope_searches_the_documents_related_to_the_anchor():
    base = ListRetriever(CHUNKS)

    results = await AnchoredRetriever(base, GraphNeighbourhood(GRAPH, hops=1)).retrieve(
        "what now", k=4, filters=DEFAULT, hints=QueryHints(services=("payments-gateway",))
    )

    assert set(base.calls[1].doc_ids) == {
        "rb-payments-latency",
        "pm-2026-06-payments",
        "rb-checkout-502",
        "pm-2026-03-checkout",
        "rb-billing-duplicates",
        "pm-2026-09-billing",
    }
    top_two = {r.chunk.doc_id for r in results[:2]}
    assert top_two == {"rb-checkout-502", "rb-payments-latency"}
    assert "rb-search-indexer-oom" not in top_two  # not related to payments-gateway


async def test_a_service_named_in_the_question_becomes_the_anchor_when_there_is_no_hint():
    base = ListRetriever(CHUNKS)

    await AnchoredRetriever(base, GraphNeighbourhood(GRAPH, hops=0)).retrieve(
        "Why is payments-gateway timing out?", k=3, filters=DEFAULT
    )

    assert set(base.calls[1].doc_ids) == {"rb-payments-latency", "pm-2026-06-payments"}


def test_linking_matches_whole_service_names_only():
    known = {"kafka", "orders-db", "checkout-api"}

    assert link_services("checkout-api and Kafka both failed", known) == ("checkout-api", "kafka")
    assert link_services("a kafkaesque outage on orders-db-primary", known) == ()


async def test_existing_filters_still_apply_inside_the_scope():
    base = ListRetriever(CHUNKS)
    only_checkout = RetrievalFilter("default", services=("checkout-api",))

    results = await AnchoredRetriever(base, OwnDocuments()).retrieve(
        "q", k=4, filters=only_checkout, hints=QueryHints(services=("payments-gateway",))
    )

    assert [r.chunk.doc_id for r in results] == ["rb-checkout-502"]
    assert len(base.calls) == 1  # the scope and the filter do not overlap, so no second search


async def test_an_anchor_the_graph_does_not_know_falls_back_to_open_retrieval():
    base = ListRetriever(CHUNKS)

    results = await AnchoredRetriever(base, GraphNeighbourhood(GRAPH, hops=1)).retrieve(
        "q", k=2, filters=DEFAULT, hints=QueryHints(services=("no-such-service",))
    )

    assert [r.chunk.doc_id for r in results] == ["rb-checkout-502", "rb-search-indexer-oom"]
    assert len(base.calls) == 1


async def test_names_and_argument_checks():
    base = ListRetriever(CHUNKS)

    assert AnchoredRetriever(base, OwnDocuments()).name == "list+anchor"
    assert AnchoredRetriever(base, GraphNeighbourhood(GRAPH, hops=1)).name == "list+graph1"
    with pytest.raises(ValueError, match="k must"):
        await AnchoredRetriever(base, OwnDocuments()).retrieve("q", k=0, filters=DEFAULT)
