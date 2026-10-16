import json

import httpx2
import numpy as np
import pytest

from incidentops_ai.retrieval.embeddings import (
    CachingEmbedder,
    EmbeddingError,
    FakeEmbedder,
    OpenAIEmbedder,
)


def cosine(a, b) -> float:
    return float(np.dot(a, b))


# --- FakeEmbedder ----------------------------------------------------------------------------


async def test_fake_vectors_are_deterministic_unit_length_and_the_requested_size():
    embedder = FakeEmbedder()

    first = await embedder.embed(["database pool exhausted"])
    again = await embedder.embed(["database pool exhausted"])

    assert first == again
    assert len(first[0]) == embedder.dimensions == 1536
    assert abs(np.linalg.norm(first[0]) - 1.0) < 1e-6


async def test_fake_vectors_put_texts_that_share_words_closer_together():
    embedder = FakeEmbedder()

    pool, pool2, kafka = await embedder.embed(
        [
            "database connection pool exhausted",
            "the connection pool is exhausted again",
            "kafka brokers unavailable",
        ]
    )

    assert cosine(pool, pool2) > cosine(pool, kafka) + 0.2


async def test_fake_embedder_copes_with_text_that_has_no_words():
    [vector] = await FakeEmbedder().embed(["   !!! "])

    assert abs(np.linalg.norm(vector) - 1.0) < 1e-6


# --- OpenAIEmbedder against a mocked HTTP layer -----------------------------------------------


def embedding_response(vectors: list[list[float]], order: list[int] | None = None) -> dict:
    order = order if order is not None else list(range(len(vectors)))
    return {
        "object": "list",
        "model": "text-embedding-3-small",
        "usage": {"prompt_tokens": 5, "total_tokens": 5},
        "data": [{"object": "embedding", "index": i, "embedding": vectors[i]} for i in order],
    }


def embedder_with(handler, **kwargs) -> tuple[OpenAIEmbedder, list[dict]]:
    seen: list[dict] = []

    def record(request: httpx2.Request) -> httpx2.Response:
        seen.append(json.loads(request.content))
        return handler(request, len(seen))

    http = httpx2.AsyncClient(transport=httpx2.MockTransport(record))

    async def no_sleep(_seconds: float) -> None:
        return None

    embedder = OpenAIEmbedder(
        "sk-test",
        model="text-embedding-3-small",
        dimensions=3,
        http_client=http,
        sleep=no_sleep,
        **kwargs,
    )
    return embedder, seen


async def test_openai_embedder_sends_model_and_dimensions_and_normalises():
    embedder, seen = embedder_with(
        lambda req, n: httpx2.Response(200, json=embedding_response([[3.0, 4.0, 0.0]]))
    )

    [vector] = await embedder.embed(["hello"])

    assert seen[0]["model"] == "text-embedding-3-small"
    assert seen[0]["dimensions"] == 3
    assert seen[0]["input"] == ["hello"]
    assert vector == pytest.approx([0.6, 0.8, 0.0])


async def test_openai_embedder_batches_and_keeps_input_order():
    def handler(req, n):
        texts = json.loads(req.content)["input"]
        vectors = [[float(len(t)), 1.0, 0.0] for t in texts]
        return httpx2.Response(
            200, json=embedding_response(vectors, order=list(reversed(range(len(texts)))))
        )

    embedder, seen = embedder_with(handler, batch_size=2)

    vectors = await embedder.embed(["a", "bb", "ccc"])

    assert [len(r["input"]) for r in seen] == [2, 1]
    # The API answered in reverse order; results must still line up with the inputs.
    norms = [v[0] / v[1] for v in vectors]
    assert norms == pytest.approx([1.0, 2.0, 3.0])


async def test_openai_embedder_retries_a_rate_limit_then_succeeds():
    def handler(req, n):
        if n == 1:
            return httpx2.Response(429, json={"error": {"message": "slow down"}})
        return httpx2.Response(200, json=embedding_response([[1.0, 0.0, 0.0]]))

    embedder, seen = embedder_with(handler)

    [vector] = await embedder.embed(["x"])

    assert len(seen) == 2
    assert vector == pytest.approx([1.0, 0.0, 0.0])


async def test_openai_embedder_gives_up_after_the_attempt_limit():
    embedder, seen = embedder_with(
        lambda req, n: httpx2.Response(503, json={"error": {"message": "down"}}), max_attempts=3
    )

    with pytest.raises(EmbeddingError, match="3 attempts"):
        await embedder.embed(["x"])

    assert len(seen) == 3


async def test_openai_embedder_does_not_retry_a_bad_request():
    embedder, seen = embedder_with(
        lambda req, n: httpx2.Response(400, json={"error": {"message": "bad input"}})
    )

    with pytest.raises(EmbeddingError, match="rejected"):
        await embedder.embed(["x"])

    assert len(seen) == 1


async def test_openai_embedder_rejects_an_answer_with_the_wrong_number_of_vectors():
    embedder, _ = embedder_with(
        lambda req, n: httpx2.Response(200, json=embedding_response([[1.0, 0.0, 0.0]]))
    )

    with pytest.raises(EmbeddingError, match="expected 2"):
        await embedder.embed(["a", "b"])


async def test_openai_embedder_refuses_empty_input_text():
    embedder, seen = embedder_with(lambda req, n: httpx2.Response(500))

    with pytest.raises(EmbeddingError, match="empty"):
        await embedder.embed(["fine", "  "])

    assert seen == []


# --- CachingEmbedder -------------------------------------------------------------------------


class Counting(FakeEmbedder):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.requests: list[list[str]] = []

    async def embed(self, texts):
        self.requests.append(list(texts))
        return await super().embed(texts)


async def test_cache_embeds_each_text_once(tmp_path):
    inner = Counting(dimensions=16)
    cached = CachingEmbedder(inner, tmp_path)

    first = await cached.embed(["a", "b"])
    second = await cached.embed(["b", "a"])

    assert inner.requests == [["a", "b"]]
    assert second == [first[1], first[0]]


async def test_cache_survives_a_restart(tmp_path):
    first_inner = Counting(dimensions=16)
    first = await CachingEmbedder(first_inner, tmp_path).embed(["a"])

    second_inner = Counting(dimensions=16)
    second = await CachingEmbedder(second_inner, tmp_path).embed(["a"])

    assert second_inner.requests == []
    np.testing.assert_allclose(second, first, atol=1e-6)


async def test_cache_only_asks_for_texts_it_is_missing_and_keeps_order(tmp_path):
    inner = Counting(dimensions=16)
    cached = CachingEmbedder(inner, tmp_path)
    await cached.embed(["b"])

    vectors = await cached.embed(["a", "b", "c"])

    assert inner.requests[1] == ["a", "c"]
    expected = await FakeEmbedder(dimensions=16).embed(["a", "b", "c"])
    np.testing.assert_allclose(vectors, expected, atol=1e-6)


async def test_cache_keeps_models_apart(tmp_path):
    small = Counting(dimensions=16)
    other = Counting(dimensions=32)
    await CachingEmbedder(small, tmp_path).embed(["a"])

    vectors = await CachingEmbedder(other, tmp_path).embed(["a"])

    assert other.requests == [["a"]]
    assert len(vectors[0]) == 32
