"""Turning text into vectors. The only module besides the LLM provider that imports `openai`.

Every embedder returns unit-length vectors, so cosine similarity is a dot product. Vectors from
different models (or sizes) are not comparable: the model name travels with the vectors.

Three implementations of the `Embedder` protocol:

- `OpenAIEmbedder`: the real model, with its own bounded retry.
- `FakeEmbedder`: hashed words, for offline tests and wiring checks. Not a language model.
- `CachingEmbedder`: wraps either of the above and remembers vectors on disk (decorator pattern).
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
    """Anything that turns texts into vectors. Callers depend on this, never on a concrete class."""

    model: str  # model identifier, stored next to every vector so different models never mix
    dimensions: int  # length of every returned vector

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """One unit-length vector per input text, in the same order as `texts`."""
        ...


def _unit(vector: np.ndarray) -> list[float]:
    """Scale `vector` to length 1, so a dot product between two vectors equals their cosine.

    An all-zero vector has no direction; it is replaced by a fixed unit vector instead of
    dividing by zero, so it stays valid (and simply matches nothing well).
    """
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
        self.model = f"fake-hash-{dimensions}"  # the "fake" prefix triggers the report's banner

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Count each word into one of `dimensions` buckets chosen by its hash, then normalise."""
        vectors = []
        for text in texts:
            vector = np.zeros(self.dimensions, dtype=np.float64)
            for token in re.findall(r"\w+", text.lower()):
                # sha256, not hash(): Python's built-in hash is randomised per process.
                bucket = int.from_bytes(hashlib.sha256(token.encode()).digest()[:4], "big")
                vector[bucket % self.dimensions] += 1.0
            vectors.append(_unit(vector))
        return vectors


# Errors where trying again cannot help (bad input, bad key, unknown model): fail at once.
_REJECTED = (
    openai.BadRequestError,
    openai.AuthenticationError,
    openai.PermissionDeniedError,
    openai.NotFoundError,
    openai.UnprocessableEntityError,
)
# Transient errors that often succeed on a later attempt.
_RETRYABLE = (openai.APITimeoutError, openai.APIConnectionError, openai.RateLimitError)


class OpenAIEmbedder:
    """OpenAI embeddings with our own bounded retry (the SDK's retries are off, as in W02).

    Owning the retry loop keeps the number of attempts and the waits visible, testable and
    bounded, instead of hidden SDK retries on top of a long default timeout.
    """

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
        """Configure the client; nothing is sent until `embed` is called.

        Args:
            api_key: OpenAI API key.
            model: e.g. "text-embedding-3-small".
            dimensions: vector size to ask the API for (text-embedding-3 models can shorten).
            batch_size: texts sent per API request.
            max_attempts: total tries per batch, including the first.
            timeout_s: per-request timeout.
            http_client: optional client, so tests can plug in a mock transport.
            sleep: how to wait between attempts; tests pass a no-op so they run instantly.
        """
        self.model = model
        self.dimensions = dimensions
        self._batch_size = batch_size
        self._max_attempts = max_attempts
        self._sleep = sleep
        self._client = openai.AsyncOpenAI(
            api_key=api_key, max_retries=0, timeout=timeout_s, http_client=http_client
        )

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.close()

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed `texts` in batches of `batch_size`. Raises `EmbeddingError` on failure."""
        if any(not text.strip() for text in texts):
            # The API rejects empty input; failing here gives a clearer message.
            raise EmbeddingError("cannot embed empty text")
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            vectors.extend(await self._embed_batch(list(texts[start : start + self._batch_size])))
        return vectors

    async def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        """One API request, retried on transient errors with exponential backoff (1s, 2s, 4s...,
        capped at 10s)."""
        detail = ""  # reason for the last failed attempt, for the final error message
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
                # Any other 4xx is our fault and will not change on retry; 5xx is the server's.
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
        """Check the API response and return its vectors in input order, normalised.

        The response is validated rather than trusted: a missing item or a wrong size would
        otherwise attach a vector to the wrong chunk without any error.
        """
        if len(data) != expected:
            raise EmbeddingError(f"expected {expected} vectors, got {len(data)}")
        # Each item says which input it belongs to; sort by that rather than assume the order.
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

    It implements `Embedder` itself and delegates misses to `inner`, so it can wrap any embedder
    without the caller noticing. The cache file is append-only JSON Lines: one
    `{"k": sha256 of text, "v": base64 float32 bytes}` record per line.
    """

    def __init__(self, inner: Embedder, cache_dir: Path) -> None:
        """`inner` does the real embedding; `cache_dir` holds one cache file per model and size."""
        self.model = inner.model
        self.dimensions = inner.dimensions
        self._inner = inner
        safe_model = re.sub(r"[^A-Za-z0-9._-]", "_", inner.model)  # usable as a file name
        self._path = cache_dir / f"{safe_model}-{inner.dimensions}.jsonl"
        self._vectors: dict[str, list[float]] | None = None  # loaded lazily on first use

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Return cached vectors, embedding (and appending to the file) only texts not seen yet."""
        cache = self._load()
        keys = [hashlib.sha256(text.encode()).hexdigest() for text in texts]
        # Texts not in the cache, each once, in first-seen order (dict keys keep insertion order).
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
        """Read the cache file once and keep it in memory as {text hash: vector}."""
        if self._vectors is None:
            self._vectors = {}
            if self._path.exists():
                for line in self._path.read_text(encoding="utf-8").splitlines():
                    record = json.loads(line)
                    raw = base64.b64decode(record["v"])
                    self._vectors[record["k"]] = np.frombuffer(raw, dtype=np.float32).tolist()
        return self._vectors
