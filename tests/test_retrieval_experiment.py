from pathlib import Path

import pytest

from incidentops_ai.retrieval.chunking import FixedChunker, SectionChunker
from incidentops_ai.retrieval.documents import load_corpus
from incidentops_ai.retrieval.embeddings import FakeEmbedder
from incidentops_ai.retrieval.evalset import load_questions
from incidentops_ai.retrieval.experiment import (
    ConfigResult,
    ExperimentConfig,
    pick_winner,
    render_comparison,
    run_config,
)
from incidentops_ai.retrieval.metrics import Summary

ROOT = Path(__file__).resolve().parent.parent
DOCUMENTS = load_corpus(ROOT / "data" / "corpus")
QUESTIONS = load_questions(ROOT / "evals" / "retrieval" / "questions.jsonl")


async def run(config: ExperimentConfig, **kwargs):
    return await run_config(config, DOCUMENTS, QUESTIONS, FakeEmbedder(), **kwargs)


async def test_a_run_scores_every_question_against_its_gold_spans():
    result = await run(ExperimentConfig(SectionChunker(max_size=1500)))

    assert len(result.scores) == len(QUESTIONS) == 30
    assert result.chunk_count > 18
    assert result.mean_chunk_chars > 100
    assert result.summary.n == 30
    # Random ranking would score near 0.03. A wiring bug (offsets, tenants, gold spans) scores
    # about that too, so this catches it without depending on the real model.
    assert result.summary.mrr > 0.3
    assert set(result.by_category) == {"identifier", "lookup", "multi", "paraphrase", "procedure"}


async def test_a_run_is_repeatable():
    config = ExperimentConfig(FixedChunker(size=500, overlap=50))

    first = await run(config)
    second = await run(config)

    assert first.summary == second.summary
    assert [s.reciprocal_rank for s in first.scores] == [s.reciprocal_rank for s in second.scores]


async def test_the_service_filter_keeps_only_that_services_chunks():
    result = await run(ExperimentConfig(SectionChunker(max_size=1500)), use_service_filter=True)

    for question in QUESTIONS:
        if question.service is None:
            continue
        returned = result.rankings[question.id]
        assert returned, question.id
        assert {r.chunk.service for r in returned} == {question.service}, question.id


async def test_rankings_keep_the_top_k_for_inspecting_misses():
    result = await run(ExperimentConfig(SectionChunker(max_size=1500)), k=7)

    assert all(len(ranked) == 7 for ranked in result.rankings.values())


async def test_context_changes_what_is_embedded_and_is_part_of_the_name():
    plain = ExperimentConfig(SectionChunker(max_size=1500))
    with_context = ExperimentConfig(SectionChunker(max_size=1500), with_context=True)

    assert plain.name == "section-1500"
    assert with_context.name == "section-1500+ctx"


async def test_the_comparison_table_lists_each_config_with_its_metrics():
    results = [
        await run(ExperimentConfig(FixedChunker(size=500, overlap=50))),
        await run(ExperimentConfig(SectionChunker(max_size=1500))),
    ]

    table = render_comparison(results)

    assert "fixed-500-50" in table and "section-1500" in table
    assert "MRR" in table and "Recall@5" in table and "Precision@5" in table
    assert table.count("\n") >= 3


async def test_nothing_to_run_is_an_error():
    with pytest.raises(ValueError):
        await run_config(
            ExperimentConfig(FixedChunker(size=500, overlap=50)), [], QUESTIONS, FakeEmbedder()
        )


# --- reading cost and the pre-registered selection rule ---------------------------------------


async def test_a_run_reports_how_much_text_the_top_five_make_you_read():
    result = await run(ExperimentConfig(FixedChunker(size=500, overlap=50)))

    expected = sum(
        sum(len(r.chunk.text) for r in ranked[:5]) for ranked in result.rankings.values()
    ) / len(result.rankings)
    assert result.mean_chars_read_at_5 == pytest.approx(expected)
    assert "Chars read@5" in render_comparison([result])


def fake_result(name: str, *, mrr: float, recall5: float, chars: float) -> ConfigResult:
    summary = Summary(n=30, recall={5: recall5}, precision={5: 0.2}, hit={5: 1.0}, mrr=mrr)
    return ConfigResult(
        name=name,
        chunk_count=10,
        mean_chunk_chars=chars,
        mean_chars_read_at_5=chars * 5,
        scores=[],
        rankings={},
        summary=summary,
        by_category={},
    )


def test_the_highest_mrr_wins():
    results = [
        fake_result("a", mrr=0.60, recall5=0.9, chars=500),
        fake_result("b", mrr=0.70, recall5=0.7, chars=900),
    ]

    assert pick_winner(results).name == "b"


def test_configs_within_two_points_of_mrr_are_decided_by_recall_at_5():
    results = [
        fake_result("best-mrr", mrr=0.700, recall5=0.80, chars=900),
        fake_result("close-better-recall", mrr=0.685, recall5=0.90, chars=900),
        fake_result("too-far", mrr=0.600, recall5=1.00, chars=100),
    ]

    assert pick_winner(results).name == "close-better-recall"


def test_if_recall_ties_too_the_smaller_chunks_win():
    results = [
        fake_result("big", mrr=0.70, recall5=0.80, chars=1500),
        fake_result("small", mrr=0.69, recall5=0.80, chars=400),
    ]

    assert pick_winner(results).name == "small"


def test_picking_a_winner_from_nothing_is_an_error():
    with pytest.raises(ValueError):
        pick_winner([])
