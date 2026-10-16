"""Turning text into vectors. The only module besides the LLM provider that imports `openai`.

Every embedder returns unit-length vectors, so cosine similarity is a dot product. Vectors from
different models (or sizes) are not comparable: the model name travels with the vectors.
"""

import asyncio
import base64
import hashlib
import json
import re
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Protocol

import httpx2
import numpy as np
import openai


class EmbeddingError(RuntimeError):
    """Embedding failed for a reason the caller should see; never a guessed or empty vector."""


class Embedder(Protocol):
    model: str
    dimensions: int

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


def _unit(vector: np.ndarray) -> list[float]:
    norm = float(np.linalg.norm(vector))
    if norm == 0.0:
        vector = np.zeros_like(vector)
        vector[0] = 1.0
        return vector.tolist()
    return (vector / norm).tolist()


class FakeEmbedder:
    """Offline stand-in: hashed bag of words. Deterministic, and texts that share words land close
    together, which is enough to test ranking, filtering and storage without a network."""

    def __init__(self, dimensions: int = 1536) -> None:
        self.dimensions = dimensions
        self.model = f"fake-hash-{dimensions}"

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            vector = np.zeros(self.dimensions, dtype=np.float64)
            for token in re.findall(r"\w+", text.lower()):
                bucket = int.from_bytes(hashlib.sha256(token.encode()).digest()[:4], "big")
                vector[bucket % self.dimensions] += 1.0
            vectors.append(_unit(vector))
        return vectors


_REJECTED = (
    openai.BadRequestError,
    openai.AuthenticationError,
    openai.PermissionDeniedError,
    openai.NotFoundError,
    openai.UnprocessableEntityError,
)
_RETRYABLE = (openai.APITimeoutError, openai.APIConnectionError, openai.RateLimitError)


class OpenAIEmbedder:
    """OpenAI embeddings with our own bounded retry (the SDK's retries are off, as in W02)."""

    def __init__(
        self,
        api_key: str,
        *,
        model: str,
        dimensions: int,
        batch_size: int = 128,
        max_attempts: int = 3,
        timeout_s: float = 30.0,
        http_client: httpx2.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.model = model
        self.dimensions = dimensions
        self._batch_size = batch_size
        self._max_attempts = max_attempts
        self._sleep = sleep
        self._client = openai.AsyncOpenAI(
            api_key=api_key, max_retries=0, timeout=timeout_s, http_client=http_client
        )

    async def aclose(self) -> None:
        await self._client.close()

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if any(not text.strip() for text in texts):
            raise EmbeddingError("cannot embed empty text")
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            vectors.extend(await self._embed_batch(list(texts[start : start + self._batch_size])))
        return vectors

    async def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        detail = ""
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = await self._client.embeddings.create(
                    model=self.model,
                    input=batch,
                    dimensions=self.dimensions,
                    encoding_format="float",
                )
            except _REJECTED as exc:
                raise EmbeddingError(
                    f"request rejected ({exc.status_code}): {exc.message}"
                ) from exc
            except _RETRYABLE as exc:
                detail = type(exc).__name__
            except openai.APIStatusError as exc:
                if exc.status_code < 500:
                    raise EmbeddingError(
                        f"request failed ({exc.status_code}): {exc.message}"
                    ) from exc
                detail = f"HTTP {exc.status_code}"
            except openai.OpenAIError as exc:
                raise EmbeddingError(f"embedding failed: {exc}") from exc
            else:
                return self._vectors(response.data, expected=len(batch))
            if attempt < self._max_attempts:
                await self._sleep(min(2.0 ** (attempt - 1), 10.0))
        raise EmbeddingError(f"embedding failed after {self._max_attempts} attempts: {detail}")

    def _vectors(self, data, *, expected: int) -> list[list[float]]:
        if len(data) != expected:
            raise EmbeddingError(f"expected {expected} vectors, got {len(data)}")
        ordered = sorted(data, key=lambda item: item.index)
        if [item.index for item in ordered] != list(range(expected)):
            raise EmbeddingError("embedding indexes do not match the inputs")
        vectors = []
        for item in ordered:
            if len(item.embedding) != self.dimensions:
                raise EmbeddingError(
                    f"expected {self.dimensions} dimensions, got {len(item.embedding)}"
                )
            vectors.append(_unit(np.asarray(item.embedding, dtype=np.float64)))
        return vectors


class CachingEmbedder:
    """Remembers vectors on disk by text hash, so re-running an experiment costs nothing.

    One file per model and size. Vectors are stored as float32, which is what the database keeps
    anyway.
    """

    def __init__(self, inner: Embedder, cache_dir: Path) -> None:
        self.model = inner.model
        self.dimensions = inner.dimensions
        self._inner = inner
        safe_model = re.sub(r"[^A-Za-z0-9._-]", "_", inner.model)
        self._path = cache_dir / f"{safe_model}-{inner.dimensions}.jsonl"
        self._vectors: dict[str, list[float]] | None = None

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        cache = self._load()
        keys = [hashlib.sha256(text.encode()).hexdigest() for text in texts]
        missing = list(dict.fromkeys(t for t, k in zip(texts, keys, strict=True) if k not in cache))
        if missing:
            fresh = await self._inner.embed(missing)
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as file:
                for text, vector in zip(missing, fresh, strict=True):
                    key = hashlib.sha256(text.encode()).hexdigest()
                    stored = np.asarray(vector, dtype=np.float32)
                    cache[key] = stored.tolist()  # what a later run will read back, exactly
                    packed = base64.b64encode(stored.tobytes())
                    file.write(f'{{"k": "{key}", "v": "{packed.decode()}"}}\n')
        return [cache[key] for key in keys]

    def _load(self) -> dict[str, list[float]]:
        if self._vectors is None:
            self._vectors = {}
            if self._path.exists():
                for line in self._path.read_text(encoding="utf-8").splitlines():
                    record = json.loads(line)
                    raw = base64.b64decode(record["v"])
                    self._vectors[record["k"]] = np.frombuffer(raw, dtype=np.float32).tolist()
        return self._vectors
