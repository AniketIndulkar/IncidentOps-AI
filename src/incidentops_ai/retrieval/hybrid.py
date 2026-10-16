"""Hybrid retrieval: dense and BM25 results fused by reciprocal rank (AIENG-30).

Dense and BM25 scores live on different scales, so scores cannot be added. Reciprocal Rank Fusion
uses only the ranks: a chunk earns 1 / (60 + rank) from every list it appears in. Equal weights and
k = 60 are the standard values and are fixed (docs/roadmap-w04.md); with 30 questions, tuning them
would only fit the eval set.
"""

import asyncio
from collections.abc import Sequence
from dataclasses import replace

from incidentops_ai.retrieval.retriever import RetrievalFilter, RetrievedChunk, Retriever

RRF_K = 60
CANDIDATE_DEPTH = 50  # each source contributes its top 50


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[RetrievedChunk]], *, k: int = RRF_K
) -> list[RetrievedChunk]:
    """One list from several ranked lists. Chunks are matched by id, so a chunk found by both
    sources appears once with both contributions. Ranks are renumbered from 1 and `score` is the
    fused score. Equal scores keep the order the chunks were first seen in."""
    scores: dict = {}
    best_rank: dict = {}
    first_seen: dict = {}
    results: dict = {}
    for ranking in rankings:
        for position, result in enumerate(ranking, start=1):
            key = result.chunk.id
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + position)
            best_rank[key] = min(best_rank.get(key, position), position)
            first_seen.setdefault(key, len(first_seen))
            results.setdefault(key, result)
    order = sorted(scores, key=lambda key: (-scores[key], best_rank[key], first_seen[key]))
    return [
        replace(results[key], score=scores[key], rank=rank)
        for rank, key in enumerate(order, start=1)
    ]


class HybridRetriever:
    name = "hybrid-rrf"

    def __init__(
        self, dense: Retriever, sparse: Retriever, *, depth: int = CANDIDATE_DEPTH
    ) -> None:
        self._dense = dense
        self._sparse = sparse
        self._depth = depth

    async def retrieve(
        self, query: str, *, k: int, filters: RetrievalFilter
    ) -> list[RetrievedChunk]:
        if k <= 0:
            raise ValueError("k must be positive")
        fetch = max(self._depth, k)  # asking for more than the depth must still work
        dense, sparse = await asyncio.gather(
            self._dense.retrieve(query, k=fetch, filters=filters),
            self._sparse.retrieve(query, k=fetch, filters=filters),
        )
        return reciprocal_rank_fusion([dense, sparse])[:k]
