import json
from pathlib import Path

import pytest

from incidentops_ai.config import Settings
from incidentops_ai.errors import LLMNotConfiguredError
from incidentops_ai.retrieval.chunking_experiment import (
    PRIMARY_CONFIGS,
    run_chunking_experiment,
)
from incidentops_ai.retrieval.documents import load_corpus
from incidentops_ai.retrieval.embeddings import FakeEmbedder, OpenAIEmbedder
from incidentops_ai.retrieval.evalset import load_questions
from incidentops_ai.retrieval.factory import build_embedder

ROOT = Path(__file__).resolve().parent.parent
DOCUMENTS = load_corpus(ROOT / "data" / "corpus")
QUESTIONS = load_questions(ROOT / "evals" / "retrieval" / "questions.jsonl")


async def test_the_experiment_runs_the_preregistered_configs_and_names_a_winner(tmp_path: Path):
    outcome = await run_chunking_experiment(
        DOCUMENTS, QUESTIONS, FakeEmbedder(), out_dir=tmp_path, run_date="2026-10-17"
    )

    assert (
        [r.name for r in outcome.primary]
        == list(PRIMARY_CONFIGS)
        == [
            "fixed-500-50",
            "fixed-1000-100",
            "fixed-2000-200",
            "paragraph-1000",
            "section-1500",
        ]
    )
    assert outcome.winner.name in PRIMARY_CONFIGS
    assert [r.name for r in outcome.with_context] == [f"{n}+ctx" for n in PRIMARY_CONFIGS]
    assert outcome.winner_filtered.name == f"{outcome.winner.name} (service filter)"


async def test_the_report_and_the_per_question_data_are_written(tmp_path: Path):
    outcome = await run_chunking_experiment(
        DOCUMENTS, QUESTIONS, FakeEmbedder(), out_dir=tmp_path, run_date="2026-10-17"
    )

    report = (tmp_path / "2026-10-17-chunking-fake-hash-1536.md").read_text()
    assert "fake-hash-1536" in report  # the embedding model is always on the page
    assert "## Primary configurations" in report
    assert f"**{outcome.winner.name}**" in report
    assert "## Context header" in report and "## Service filter" in report
    assert "## MRR by question style" in report

    data = json.loads((tmp_path / "2026-10-17-chunking-fake-hash-1536.json").read_text())
    assert data["embedding_model"] == "fake-hash-1536"
    assert data["winner"] == outcome.winner.name
    assert set(data["configs"]) >= set(PRIMARY_CONFIGS)
    questions = data["configs"]["section-1500"]["questions"]
    assert len(questions) == 30
    assert set(questions["q01"]) == {"reciprocal_rank", "first_relevant_rank", "recall@5"}


async def test_the_same_inputs_give_the_same_report(tmp_path: Path):
    first = tmp_path / "a"
    second = tmp_path / "b"

    await run_chunking_experiment(DOCUMENTS, QUESTIONS, FakeEmbedder(), out_dir=first, run_date="d")
    await run_chunking_experiment(
        DOCUMENTS, QUESTIONS, FakeEmbedder(), out_dir=second, run_date="d"
    )

    assert (first / "d-chunking-fake-hash-1536.json").read_text() == (
        second / "d-chunking-fake-hash-1536.json"
    ).read_text()


def test_the_embedder_comes_from_settings():
    settings = Settings(
        _env_file=None,
        openai_api_key="sk-test-0123456789",
        embedding_model="text-embedding-3-large",
        embedding_dimensions=1536,
    )

    embedder = build_embedder(settings)

    assert isinstance(embedder, OpenAIEmbedder)
    assert (embedder.model, embedder.dimensions) == ("text-embedding-3-large", 1536)


def test_without_an_api_key_there_is_no_embedder():
    with pytest.raises(LLMNotConfiguredError):
        build_embedder(Settings(_env_file=None, openai_api_key=None))


def test_the_default_embedding_model_is_the_small_one():
    settings = Settings(_env_file=None)

    assert (settings.embedding_model, settings.embedding_dimensions) == (
        "text-embedding-3-small",
        1536,
    )
