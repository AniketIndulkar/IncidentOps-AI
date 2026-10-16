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
    """Metadata a retriever must apply *before* ranking. None means "no restriction"."""

    tenant_id: str
    services: tuple[str, ...] | None = None
    doc_types: tuple[DocType, ...] | None = None

    def __post_init__(self) -> None:
        if not self.tenant_id.strip():
            raise ValueError("a tenant_id is required to retrieve")

    def allows(self, chunk: Chunk) -> bool:
        return (
            chunk.tenant_id == self.tenant_id
            and (self.services is None or chunk.service in self.services)
            and (self.doc_types is None or chunk.doc_type in self.doc_types)
        )


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
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
    name: str

    async def retrieve(
        self, query: str, *, k: int, filters: RetrievalFilter
    ) -> list[RetrievedChunk]: ...
