"""Reranking: read the question and each candidate together and re-order the candidates (AIENG-31).

Dense and BM25 score a chunk without seeing the question and the chunk side by side. A
cross-encoder does, which can fix "right topic, wrong passage" cases, at the price of one model run
per candidate. It sits behind the same `Retriever` interface as every other stage.

The real model is an optional extra (`uv sync --extra rerank`), imported only when first used, so
the default install, CI and the Docker image never load it. Tests use `FakeReranker`.
"""

import asyncio
import threading
import time
from collections.abc import Sequence
from dataclasses import replace
from typing import Any, Protocol

from incidentops_ai.retrieval.bm25 import tokenize
from incidentops_ai.retrieval.retriever import RetrievalFilter, RetrievedChunk, Retriever

RERANK_DEPTH = 20  # candidates taken from the stage below
# ONNX export of cross-encoder/ms-marco-MiniLM-L-6-v2 (22M parameters, about 80 MB), run without torch.
DEFAULT_MODEL = "Xenova/ms-marco-MiniLM-L-6-v2"
TOKEN_LIMIT = 512
DEFAULT_BATCH_SIZE = 64  # fastembed's own default: all 20 candidates go through in one batch
CHARS_PER_TOKEN = 4


class RerankerError(RuntimeError):
    """The reranker is missing, or gave an answer that cannot be used."""


class Reranker(Protocol):
    name: str

    async def score(self, query: str, passages: Sequence[str]) -> list[float]:
        """One score per passage, in order. Higher is more relevant to the query."""
        ...


class FakeReranker:
    """Offline stand-in: the share of the query's words that a passage contains."""

    name = "fake-rerank"

    async def score(self, query: str, passages: Sequence[str]) -> list[float]:
        words = set(tokenize(query))
        if not words:
            return [0.0] * len(passages)
        return [len(words & set(tokenize(passage))) / len(words) for passage in passages]


class CrossEncoderReranker:
    """A small cross-encoder run locally on CPU. Loaded on first use, inference off the event loop.

    `load_seconds` is how long loading took (None before first use). `truncated` counts passages
    that exceeded the model's input limit and were cut, summed over all calls.
    """

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        *,
        max_length: int = TOKEN_LIMIT,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> None:
        self.name = model_name
        self.load_seconds: float | None = None
        self.truncated = 0
        self._max_length = max_length
        self._batch_size = batch_size  # passages scored at once; smaller uses less memory
        self._model: Any = None
        self._lock = threading.Lock()

    def _load(self) -> Any:
        with self._lock:
            if self._model is None:
                try:
                    from fastembed.rerank.cross_encoder import TextCrossEncoder
                except ImportError as error:
                    raise RerankerError(
                        "the cross-encoder needs the optional extra: uv sync --extra rerank"
                    ) from error
                started = time.perf_counter()
                self._model = TextCrossEncoder(model_name=self.name)
                self.load_seconds = time.perf_counter() - started
            return self._model

    async def score(self, query: str, passages: Sequence[str]) -> list[float]:
        if not passages:
            return []
        model = await asyncio.to_thread(self._load)
        self.truncated += self._count_truncated(model, query, passages)
        return await asyncio.to_thread(
            lambda: [float(s) for s in model.rerank(query, passages, batch_size=self._batch_size)]
        )

    def _count_truncated(self, model: Any, query: str, passages: Sequence[str]) -> int:
        """Passages cut at the model's input limit: exact when the model's tokenizer is reachable
        (it reports the tokens that overflowed), else estimated from the character count."""
        tokenizer = getattr(getattr(model, "model", None), "tokenizer", None)
        if tokenizer is not None:
            return sum(bool(tokenizer.encode(query, passage).overflowing) for passage in passages)
        limit = self._max_length * CHARS_PER_TOKEN
        return sum(len(query) + len(passage) > limit for passage in passages)


class RerankingRetriever:
    def __init__(self, base: Retriever, reranker: Reranker, *, depth: int = RERANK_DEPTH) -> None:
        self.name = f"{base.name}+rerank"
        self._base = base
        self._reranker = reranker
        self._depth = depth

    async def retrieve(
        self, query: str, *, k: int, filters: RetrievalFilter
    ) -> list[RetrievedChunk]:
        """Only the top `depth` results of the base stage can appear, however large `k` is."""
        if k <= 0:
            raise ValueError("k must be positive")
        candidates = await self._base.retrieve(query, k=self._depth, filters=filters)
        if not candidates:
            return []
        scores = await self._reranker.score(
            query, [c.chunk.embedding_text(with_context=True) for c in candidates]
        )
        if len(scores) != len(candidates):
            raise RerankerError(
                f"{self._reranker.name} returned {len(scores)} scores for "
                f"{len(candidates)} candidates"
            )
        order = sorted(range(len(candidates)), key=lambda i: -scores[i])  # stable: ties keep order
        return [
            replace(candidates[i], score=scores[i], rank=rank)
            for rank, i in enumerate(order[:k], start=1)
        ]
