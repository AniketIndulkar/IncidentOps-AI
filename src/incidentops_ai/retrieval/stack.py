"""The retrieval stacks the API can serve, built from settings (W04 chose hybrid).

dense          pgvector cosine search
hybrid         dense + BM25, fused by reciprocal rank
hybrid-rerank  hybrid, then a cross-encoder (needs the optional "rerank" extra)
"""

import asyncio
import hashlib
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from incidentops_ai.retrieval.bm25 import Bm25Retriever
from incidentops_ai.retrieval.embeddings import Embedder
from incidentops_ai.retrieval.hybrid import HybridRetriever
from incidentops_ai.retrieval.orm import ChunkRow, DocumentRow
from incidentops_ai.retrieval.rerank import Reranker, RerankingRetriever
from incidentops_ai.retrieval.retriever import (
    QueryHints,
    RetrievalFilter,
    RetrievedChunk,
    Retriever,
)
from incidentops_ai.retrieval.store import PgVectorRetriever, load_chunks

STACKS: Sequence[str] = ("dense", "hybrid", "hybrid-rerank", "bm25")


async def _fingerprint(session: AsyncSession, tenant_id: str) -> str:
    """Changes whenever a tenant's stored chunks could have: a document added, removed, edited,
    parsed by a newer parser or cut by another chunker. Cheap: one row per document."""
    documents = (
        await session.execute(
            select(
                DocumentRow.id,
                DocumentRow.content_sha256,
                DocumentRow.parser_version,
                DocumentRow.chunker,
            )
            .where(DocumentRow.tenant_id == tenant_id)
            .order_by(DocumentRow.id)
        )
    ).all()
    chunk_count = (
        await session.execute(
            select(func.count()).select_from(ChunkRow).where(ChunkRow.tenant_id == tenant_id)
        )
    ).scalar_one()
    digest = hashlib.sha256(f"{chunk_count}|{documents!r}".encode())
    return digest.hexdigest()


class DbBm25Retriever:
    """BM25 over the chunks stored in PostgreSQL, so it sees what the vector search sees.

    The index lives in memory, one per tenant, and is rebuilt when the stored chunks change, so a
    re-ingestion is picked up without restarting the API.
    """

    name = "bm25"

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], *, with_context: bool = False
    ) -> None:
        self._session_factory = session_factory
        self._with_context = with_context
        self._indexes: dict[str, tuple[str, Bm25Retriever]] = {}
        self._rebuild = asyncio.Lock()

    async def retrieve(
        self,
        query: str,
        *,
        k: int,
        filters: RetrievalFilter,
        hints: QueryHints | None = None,
    ) -> list[RetrievedChunk]:
        index = await self._index_for(filters.tenant_id)
        return await index.retrieve(query, k=k, filters=filters, hints=hints)

    async def _index_for(self, tenant_id: str) -> Bm25Retriever:
        async with self._session_factory() as session:
            current = await _fingerprint(session, tenant_id)
            cached = self._indexes.get(tenant_id)
            if cached is not None and cached[0] == current:
                return cached[1]
            async with self._rebuild:  # one rebuild at a time; a waiting request reuses it
                cached = self._indexes.get(tenant_id)
                if cached is not None and cached[0] == current:
                    return cached[1]
                chunks = await load_chunks(session, tenant_id)
                index = Bm25Retriever(chunks, with_context=self._with_context)
                self._indexes[tenant_id] = (current, index)
                return index


def build_retriever(
    stack: str,
    session_factory: async_sessionmaker[AsyncSession],
    embedder: Embedder | None,
    reranker: Reranker | None = None,
) -> Retriever:
    if stack not in STACKS:
        raise ValueError(f"unknown stack {stack!r}; valid stacks: {', '.join(STACKS)}")
    if stack == "bm25":  # W12: no embeddings at all (load tests, or no embedding provider)
        return DbBm25Retriever(session_factory)
    if embedder is None:
        raise ValueError(f"the {stack!r} stack needs an embedder")
    dense = PgVectorRetriever(session_factory, embedder)
    if stack == "dense":
        return dense
    hybrid = HybridRetriever(dense, DbBm25Retriever(session_factory))
    if stack == "hybrid":
        return hybrid
    if reranker is None:
        raise ValueError("hybrid-rerank needs a reranker")
    return RerankingRetriever(hybrid, reranker)
