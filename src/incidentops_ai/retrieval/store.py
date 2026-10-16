"""Embedding chunks into PostgreSQL (pgvector) and searching them.

Search is an exact cosine scan filtered by tenant first. At this corpus size that is faster than
an approximate index and never misses; see the note in the migration for when that changes.
"""

from collections.abc import Sequence

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from incidentops_ai.retrieval.chunking import Chunk
from incidentops_ai.retrieval.documents import DEFAULT_TENANT, DocType
from incidentops_ai.retrieval.embeddings import Embedder
from incidentops_ai.retrieval.orm import EMBEDDING_DIMENSIONS, ChunkRow, DocumentRow
from incidentops_ai.retrieval.retriever import RetrievalFilter, RetrievedChunk


def row_to_chunk(row: ChunkRow, *, title: str, source_path: str) -> Chunk:
    return Chunk(
        tenant_id=row.tenant_id,
        doc_id=row.doc_id,
        doc_type=DocType(row.doc_type),
        service=row.service,
        title=title,
        source_path=source_path,
        chunker=row.chunker,
        ordinal=row.ordinal,
        start=row.start_char,
        end=row.end_char,
        page=row.page,
        page_end=row.page_end,
        section=row.section,
        text=row.text,
    )


async def load_chunks(session: AsyncSession, tenant_id: str) -> list[Chunk]:
    """Every stored chunk of one tenant, in corpus order (source path, then position).

    Feeds the in-memory BM25 index, which must see the same chunks the vector search does.
    """
    rows = (
        await session.execute(
            select(ChunkRow, DocumentRow.title, DocumentRow.source_path)
            .join(
                DocumentRow,
                and_(
                    DocumentRow.tenant_id == ChunkRow.tenant_id, DocumentRow.id == ChunkRow.doc_id
                ),
            )
            .where(ChunkRow.tenant_id == tenant_id)
            .order_by(DocumentRow.source_path, ChunkRow.ordinal)
        )
    ).all()
    return [row_to_chunk(row, title=title, source_path=path) for row, title, path in rows]


def _check_dimensions(embedder: Embedder) -> None:
    if embedder.dimensions != EMBEDDING_DIMENSIONS:
        raise ValueError(
            f"the chunks table holds {EMBEDDING_DIMENSIONS}-dimensional vectors, "
            f"but {embedder.model} gives {embedder.dimensions}"
        )


async def index_chunks(
    session: AsyncSession,
    embedder: Embedder,
    *,
    tenant_id: str = DEFAULT_TENANT,
    with_context: bool = False,
    batch_size: int = 64,
) -> int:
    """Embed chunks that have no vector yet, or one made by another model or context setting.

    Returns how many were embedded. The caller owns the transaction: if embedding fails part way,
    rolling back leaves the previous vectors untouched.
    """
    _check_dimensions(embedder)
    stale = (
        ChunkRow.embedding.is_(None)
        | ChunkRow.embedding_model.is_distinct_from(embedder.model)
        | ChunkRow.embedding_context.is_distinct_from(with_context)
    )
    rows = (
        await session.execute(
            select(ChunkRow, DocumentRow.title, DocumentRow.source_path)
            .join(
                DocumentRow,
                and_(
                    DocumentRow.tenant_id == ChunkRow.tenant_id, DocumentRow.id == ChunkRow.doc_id
                ),
            )
            .where(ChunkRow.tenant_id == tenant_id, stale)
            .order_by(DocumentRow.source_path, ChunkRow.ordinal)
        )
    ).all()

    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        texts = [
            row_to_chunk(r, title=title, source_path=path).embedding_text(with_context=with_context)
            for r, title, path in batch
        ]
        for (row, _, _), vector in zip(batch, await embedder.embed(texts), strict=True):
            row.embedding = vector
            row.embedding_model = embedder.model
            row.embedding_context = with_context
    await session.flush()
    return len(rows)


class PgVectorRetriever:
    name = "dense-pgvector"

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], embedder: Embedder
    ) -> None:
        _check_dimensions(embedder)
        self._session_factory = session_factory
        self._embedder = embedder

    async def retrieve(
        self, query: str, *, k: int, filters: RetrievalFilter
    ) -> list[RetrievedChunk]:
        if k <= 0:
            raise ValueError("k must be positive")
        [vector] = await self._embedder.embed([query])
        distance = ChunkRow.embedding.cosine_distance(vector).label("distance")
        conditions = [
            ChunkRow.tenant_id == filters.tenant_id,
            ChunkRow.embedding.is_not(None),
            ChunkRow.embedding_model == self._embedder.model,
        ]
        if filters.services is not None:
            conditions.append(ChunkRow.service.in_(filters.services))
        if filters.doc_types is not None:
            conditions.append(ChunkRow.doc_type.in_([t.value for t in filters.doc_types]))

        statement = (
            select(ChunkRow, DocumentRow.title, DocumentRow.source_path, distance)
            .join(
                DocumentRow,
                and_(
                    DocumentRow.tenant_id == ChunkRow.tenant_id, DocumentRow.id == ChunkRow.doc_id
                ),
            )
            .where(*conditions)
            # Ties fall back to corpus order, like the in-memory reference.
            .order_by(distance, DocumentRow.source_path, ChunkRow.ordinal)
            .limit(k)
        )
        async with self._session_factory() as session:
            rows: Sequence = (await session.execute(statement)).all()
        return [
            RetrievedChunk(
                chunk=row_to_chunk(row, title=title, source_path=path),
                score=1.0 - float(dist),
                rank=rank,
            )
            for rank, (row, title, path, dist) in enumerate(rows, start=1)
        ]
