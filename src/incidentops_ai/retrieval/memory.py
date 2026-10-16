"""Dense retrieval held in memory: the reference implementation and the experiment workhorse.

Chunking experiments build many indexes quickly; this avoids a database round trip per config.
The pgvector retriever (pgvector.py) must rank the same way, and a test holds it to that.
"""

from collections.abc import Sequence

import numpy as np

from incidentops_ai.retrieval.chunking import Chunk
from incidentops_ai.retrieval.embeddings import Embedder
from incidentops_ai.retrieval.retriever import RetrievalFilter, RetrievedChunk


class InMemoryDenseRetriever:
    name = "dense-memory"

    def __init__(self, chunks: Sequence[Chunk], matrix: np.ndarray, embedder: Embedder) -> None:
        if len(chunks) != len(matrix):
            raise ValueError("one vector per chunk is required")
        self._chunks = list(chunks)
        self._matrix = matrix
        self._embedder = embedder

    @classmethod
    async def build(
        cls, chunks: Sequence[Chunk], embedder: Embedder, *, with_context: bool
    ) -> "InMemoryDenseRetriever":
        texts = [chunk.embedding_text(with_context=with_context) for chunk in chunks]
        vectors = await embedder.embed(texts) if texts else []
        matrix = np.asarray(vectors, dtype=np.float32).reshape(len(texts), embedder.dimensions)
        return cls(chunks, matrix, embedder)

    async def retrieve(
        self, query: str, *, k: int, filters: RetrievalFilter
    ) -> list[RetrievedChunk]:
        if k <= 0:
            raise ValueError("k must be positive")
        allowed = np.flatnonzero([filters.allows(chunk) for chunk in self._chunks])
        if allowed.size == 0:
            return []
        [vector] = await self._embedder.embed([query])
        scores = self._matrix[allowed] @ np.asarray(vector, dtype=np.float32)
        order = np.argsort(-scores, kind="stable")[:k]  # stable: ties keep corpus order
        return [
            RetrievedChunk(chunk=self._chunks[allowed[i]], score=float(scores[i]), rank=rank)
            for rank, i in enumerate(order, start=1)
        ]
