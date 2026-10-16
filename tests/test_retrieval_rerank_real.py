"""The real cross-encoder. Downloads a model (about 90 MB) on first use, so it is opt-in:

uv sync --extra rerank
RUN_REAL_RERANKER=1 uv run pytest tests/test_retrieval_rerank_real.py
"""

import os

import pytest

from incidentops_ai.retrieval.rerank import CrossEncoderReranker

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_REAL_RERANKER") != "1",
    reason="downloads a model; set RUN_REAL_RERANKER=1 to run",
)


async def test_the_cross_encoder_puts_the_passage_that_answers_the_question_first():
    reranker = CrossEncoderReranker()
    passages = [
        "A stale index page is served by the CDN after a release; purge the HTML entry points.",
        "To roll back a bad release run `kubectl rollout undo deployment/checkout-api`.",
        "Each document costs roughly 350 KB of memory while the search index is built.",
    ]

    scores = await reranker.score("Which command rolls back a bad checkout-api release?", passages)

    assert len(scores) == 3
    assert scores.index(max(scores)) == 1
    assert reranker.load_seconds is not None and reranker.load_seconds > 0
