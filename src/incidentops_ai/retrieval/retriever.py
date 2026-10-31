"""The interface every retriever implements (dense now; BM25, hybrid, graph and multimodal later).

Two rules are baked in from day one, because adding them later touches every retriever:
- a tenant is mandatory, so one customer's chunks can never be returned to another (W10);
- results carry the full chunk with its provenance, so any answer can cite its source.
"""

from dataclasses import dataclass
from typing import Protocol

from incidentops_ai.retrieval.chunking import Chunk
from incidentops_ai.retrieval.documents import DocType


@dataclass(frozen=True, slots=True)
class RetrievalFilter:
    """Metadata a retriever must apply *before* ranking. None means "no restriction".

    Filtering before ranking matters: filtering after a top-k search can leave fewer than k
    results, or none, when the best matches belong to another service.
    """

    tenant_id: str  # required; whose chunks may be searched
    services: tuple[str, ...] | None = None  # only chunks about one of these services
    doc_types: tuple[DocType, ...] | None = None  # only these kinds of document

    def __post_init__(self) -> None:
        """Runs right after the dataclass `__init__`: reject a filter with no tenant."""
        if not self.tenant_id.strip():
            raise ValueError("a tenant_id is required to retrieve")

    def allows(self, chunk: Chunk) -> bool:
        """Whether `chunk` passes the filter. Used by in-memory retrievers; the database
        retriever expresses the same conditions as SQL."""
        return (
            chunk.tenant_id == self.tenant_id
            and (self.services is None or chunk.service in self.services)
            and (self.doc_types is None or chunk.doc_type in self.doc_types)
        )


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    """One search result: the chunk, how well it matched, and its position in the results.

    `doc_id`, `start` and `end` make it satisfy `metrics.Located`, so results can be scored
    against gold evidence spans directly.
    """

    chunk: Chunk
    score: float  # higher is better; comparable only within one retriever
    rank: int  # 1-based

    @property
    def doc_id(self) -> str:
        return self.chunk.doc_id

    @property
    def start(self) -> int:
        return self.chunk.start

    @property
    def end(self) -> int:
        return self.chunk.end


class Retriever(Protocol):
    """Anything that can answer "which chunks best match this query?"."""

    name: str  # short identifier shown in reports, e.g. "dense-pgvector"

    async def retrieve(
        self, query: str, *, k: int, filters: RetrievalFilter
    ) -> list[RetrievedChunk]:
        """The best `k` chunks for `query` among those `filters` allows, best first (rank 1)."""
        ...
