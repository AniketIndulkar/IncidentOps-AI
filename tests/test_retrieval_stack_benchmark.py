"""AIENG-29/30/31/33: compare retrieval stages (dense, BM25, ...) on the eval set."""

import json
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import pytest

from incidentops_ai.retrieval.documents import load_corpus
from incidentops_ai.retrieval.embeddings import FakeEmbedder
from incidentops_ai.retrieval.evalset import load_questions
from incidentops_ai.retrieval.metrics import QuestionScore
from incidentops_ai.retrieval.rerank import FakeReranker
from incidentops_ai.retrieval.stack_benchmark import (
    STAGES,
    StageRun,
    parse_config,
    questions_moved,
    render_stack_report,
    rerank_verdict,
    run_stages,
    write_stack_report,
)

ROOT = Path(__file__).resolve().parent.parent
DOCUMENTS = load_corpus(ROOT / "data" / "corpus")
QUESTIONS = load_questions(ROOT / "evals" / "retrieval" / "questions.jsonl")


class CountingEmbedder:
    """Wraps an embedder and remembers every text it was asked to embed."""

    def __init__(self, inner: FakeEmbedder) -> None:
        self.model = inner.model
        self.dimensions = inner.dimensions
        self.texts: list[str] = []
        self._inner = inner

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.texts.extend(texts)
        return await self._inner.embed(texts)


async def runs_for(config: str, stages: Sequence[str] = ("dense", "bm25")) -> list[StageRun]:
    return await run_stages(
        parse_config(config), DOCUMENTS, QUESTIONS, FakeEmbedder(), stages=stages
    )


def score(question_id: str, *, recall_5: float, rr: float) -> QuestionScore:
    return QuestionScore(
        question_id=question_id,
        category="lookup",
        recall={1: recall_5, 3: recall_5, 5: recall_5, 10: recall_5},
        precision={1: 0.0, 3: 0.0, 5: 0.0, 10: 0.0},
        hit={1: 0.0, 3: 0.0, 5: 0.0, 10: 0.0},
        reciprocal_rank=rr,
        first_relevant_rank=None,
    )


def test_a_config_name_gives_the_chunker_and_the_context_setting():
    plain = parse_config("fixed-2000-200")
    header = parse_config("section-1500+ctx")

    assert (plain.chunker.name, plain.with_context) == ("fixed-2000-200", False)
    assert (header.chunker.name, header.with_context) == ("section-1500", True)
    assert header.name == "section-1500+ctx"
    with pytest.raises(ValueError, match="unknown chunker"):
        parse_config("sentence-3")


async def test_run_stages_returns_one_run_per_requested_stage_in_order():
    runs = await runs_for("section-1500+ctx", stages=("bm25", "dense"))

    assert [r.stage for r in runs] == ["bm25", "dense"]
    assert {r.config for r in runs} == {"section-1500+ctx"}
    assert all(len(r.result.scores) == 30 and len(r.result.latencies_ms) == 30 for r in runs)
    assert all(r.mean_chars_read_at_5 > 0 for r in runs)
    assert "dense" in STAGES and "bm25" in STAGES


async def test_an_unknown_stage_is_rejected_with_the_valid_names():
    with pytest.raises(ValueError, match="unknown stage 'fusion'.*dense"):
        await runs_for("fixed-2000-200", stages=("dense", "fusion"))


async def test_only_stages_that_embed_the_query_have_an_embedding_cost():
    dense, bm25 = await runs_for("fixed-2000-200")

    assert dense.extras["usd_per_1000_queries"] > 0
    assert bm25.extras["usd_per_1000_queries"] == 0


async def test_questions_are_embedded_by_the_query_embedder_when_one_is_given():
    chunk_embedder = CountingEmbedder(FakeEmbedder())
    query_embedder = CountingEmbedder(FakeEmbedder())

    await run_stages(
        parse_config("fixed-2000-200"),
        DOCUMENTS,
        QUESTIONS,
        chunk_embedder,
        stages=("dense",),
        query_embedder=query_embedder,
    )

    assert sorted(query_embedder.texts) == sorted(q.text for q in QUESTIONS)
    assert not {q.text for q in QUESTIONS} & set(chunk_embedder.texts)
    assert chunk_embedder.texts  # the chunks themselves still come from this one


async def test_questions_moved_lists_improved_and_worse_ids():
    [dense] = await runs_for("fixed-2000-200", stages=("dense",))
    before = StageRun(
        "dense",
        "c",
        _with_scores(
            dense,
            [
                score("q1", recall_5=0.5, rr=0.5),
                score("q2", recall_5=1.0, rr=1.0),
                score("q3", recall_5=1.0, rr=0.5),
                score("q4", recall_5=0.5, rr=0.5),
            ],
        ),
        0.0,
    )
    after = StageRun(
        "bm25",
        "c",
        _with_scores(
            dense,
            [
                score("q1", recall_5=1.0, rr=0.5),  # second half of the evidence found
                score("q2", recall_5=1.0, rr=0.5),  # right chunk moved down
                score("q3", recall_5=1.0, rr=0.5),  # unchanged
                score("q4", recall_5=0.5, rr=1.0),  # same recall, found sooner
            ],
        ),
        0.0,
    )

    improved, worse = questions_moved(before, after)

    assert improved == ["q1", "q4"]
    assert worse == ["q2"]


async def test_the_report_has_a_table_row_per_stage_per_config_and_lists_misses():
    runs = {
        "fixed-2000-200": await runs_for("fixed-2000-200"),
        "section-1500": await runs_for("section-1500"),
    }

    report = render_stack_report(runs, embedding_model="text-embedding-3-small", run_date="d")

    for expected in (
        "## fixed-2000-200 (decision config)",
        "## section-1500 (exploratory)",
        "| dense |",
        "| bm25 |",
        "Recall@5",
        "MRR",
        "p50",
        "p95",
        "$/1,000 queries",
        "### Questions moved",
        "- bm25 vs dense: improved",
        "### Misses: bm25",
        "text-embedding-3-small",
    ):
        assert expected in report
    assert "OFFLINE" not in report


async def test_a_run_on_fake_embeddings_carries_the_wiring_check_banner():
    report = render_stack_report(
        {"fixed-2000-200": await runs_for("fixed-2000-200")},
        embedding_model="fake-hash-1536",
        run_date="d",
    )

    assert "OFFLINE WIRING CHECK" in report


async def test_the_report_and_per_question_data_are_written(tmp_path: Path):
    runs = {"fixed-2000-200": await runs_for("fixed-2000-200")}

    files = write_stack_report(
        runs, tmp_path, embedding_model="m", run_date="2026-10-20", name="stack-comparison"
    )

    assert files.report_path.name == "2026-10-20-stack-comparison.md"
    assert files.data_path.name == "2026-10-20-stack-comparison.json"
    data = json.loads(files.data_path.read_text())
    bm25 = data["configs"]["fixed-2000-200"]["bm25"]
    assert set(bm25) >= {"summary", "by_category", "questions", "latency_ms", "extras"}
    assert len(bm25["questions"]) == 30
    assert set(bm25["questions"]["q01"]) >= {"reciprocal_rank", "first_relevant_rank", "recall"}


def _with_scores(run: StageRun, scores: list[QuestionScore]):
    return replace(run.result, scores=scores)


async def test_the_hybrid_stage_is_compared_with_both_of_its_sources():
    runs = {"fixed-2000-200": await runs_for("fixed-2000-200", stages=("dense", "bm25", "hybrid"))}

    report = render_stack_report(runs, embedding_model="m", run_date="d")

    assert [r.stage for r in runs["fixed-2000-200"]] == ["dense", "bm25", "hybrid"]
    assert runs["fixed-2000-200"][2].result.retriever == "hybrid-rrf"
    assert runs["fixed-2000-200"][2].extras["usd_per_1000_queries"] > 0
    assert "- hybrid vs dense: improved" in report
    assert "- hybrid vs bm25: improved" in report
    assert "### Misses: hybrid" in report


async def rerank_runs(config: str = "fixed-2000-200") -> list[StageRun]:
    return await run_stages(
        parse_config(config),
        DOCUMENTS,
        QUESTIONS,
        FakeEmbedder(),
        stages=("hybrid", "hybrid-rerank"),
        reranker=FakeReranker(),
    )


async def test_the_rerank_stage_refuses_to_run_without_a_reranker():
    with pytest.raises(ValueError, match="hybrid-rerank.*needs a reranker"):
        await run_stages(
            parse_config("fixed-2000-200"),
            DOCUMENTS,
            QUESTIONS,
            FakeEmbedder(),
            stages=("hybrid-rerank",),
        )


async def test_the_rerank_stage_records_what_it_costs_locally():
    hybrid, rerank = await rerank_runs()

    assert rerank.result.retriever == "hybrid-rrf+rerank"
    assert set(rerank.extras) >= {
        "load_seconds",
        "rerank_mean_ms",
        "rerank_p95_ms",
        "rss_added_mb",
        "truncated",
        "usd_per_1000_queries",
    }
    assert rerank.extras["rerank_p95_ms"] >= rerank.extras["rerank_mean_ms"] >= 0
    assert rerank.extras["usd_per_1000_queries"] == hybrid.extras["usd_per_1000_queries"] > 0
    assert len(rerank.result.scores) == 30


@pytest.mark.parametrize(
    ("p95_ms", "added_mb", "heavy", "mentions"),
    [
        (120.0, 40.0, False, "Within"),
        (350.0, 40.0, True, "p95"),
        (100.0, 600.0, True, "memory"),
        (350.0, 600.0, True, "p95"),
    ],
)
def test_the_local_verdict_applies_the_bars_fixed_in_advance(p95_ms, added_mb, heavy, mentions):
    too_heavy, text = rerank_verdict(p95_ms, added_mb)

    assert too_heavy is heavy
    assert mentions in text
    assert "300" in text and "500" in text  # the bars are always printed next to the numbers


async def test_the_report_has_the_reranker_section_and_its_comparison():
    runs = {"fixed-2000-200": await rerank_runs()}

    report = render_stack_report(runs, embedding_model="m", run_date="d")

    assert "### Reranker, local cost" in report
    assert "- hybrid-rerank vs hybrid: improved" in report
    assert "### Misses: hybrid-rerank" in report
