"""Dense retrieval held in memory: the reference implementation and the experiment workhorse.

Chunking experiments build many indexes quickly; this avoids a database round trip per config.
The pgvector retriever (store.py) must rank the same way, and a test holds it to that.
"""

from collections.abc import Sequence

import numpy as np

from incidentops_ai.retrieval.chunking import Chunk
from incidentops_ai.retrieval.embeddings import Embedder
from incidentops_ai.retrieval.retriever import RetrievalFilter, RetrievedChunk


class InMemoryDenseRetriever:
    """Exact cosine search over a numpy matrix with one unit-length row per chunk.

    Because every vector has length 1, `matrix @ query` gives the cosine similarity of the query
    with every chunk in a single matrix-vector product.
    """

    name = "dense-memory"

    def __init__(self, chunks: Sequence[Chunk], matrix: np.ndarray, embedder: Embedder) -> None:
        """`matrix[i]` is the vector of `chunks[i]`; `embedder` must be the one that made them,
        because the query has to be embedded into the same vector space."""
        if len(chunks) != len(matrix):
            raise ValueError("one vector per chunk is required")
        self._chunks = list(chunks)
        self._matrix = matrix
        self._embedder = embedder

    @classmethod
    async def build(
        cls, chunks: Sequence[Chunk], embedder: Embedder, *, with_context: bool
    ) -> "InMemoryDenseRetriever":
        """Embed `chunks` and return a retriever over them.

        A classmethod because construction needs an `await` (embedding), which `__init__` cannot
        do. `with_context` chooses whether title and section are embedded with each chunk.
        """
        texts = [chunk.embedding_text(with_context=with_context) for chunk in chunks]
        vectors = await embedder.embed(texts) if texts else []
        # reshape keeps the matrix 2-D (0 x dimensions) even when there are no chunks.
        matrix = np.asarray(vectors, dtype=np.float32).reshape(len(texts), embedder.dimensions)
        return cls(chunks, matrix, embedder)

    async def retrieve(
        self, query: str, *, k: int, filters: RetrievalFilter
    ) -> list[RetrievedChunk]:
        """Filter first, then score only the allowed chunks and return the best `k`."""
        if k <= 0:
            raise ValueError("k must be positive")
        # Row numbers of the chunks the filter lets through.
        allowed = np.flatnonzero([filters.allows(chunk) for chunk in self._chunks])
        if allowed.size == 0:
            return []
        [vector] = await self._embedder.embed([query])
        scores = self._matrix[allowed] @ np.asarray(vector, dtype=np.float32)
        order = np.argsort(-scores, kind="stable")[:k]  # stable: ties keep corpus order
        return [
            # `i` indexes `allowed`, so allowed[i] is the chunk's row in the full list.
            RetrievedChunk(chunk=self._chunks[allowed[i]], score=float(scores[i]), rank=rank)
            for rank, i in enumerate(order, start=1)
        ]
