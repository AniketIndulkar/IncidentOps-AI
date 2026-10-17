"""AIENG-38: graph stages on both question sets, and the adoption rule fixed in docs/roadmap-w05.md."""

from pathlib import Path

import pytest

from incidentops_ai.graph.benchmark import (
    GRAPH_STAGES,
    apply_rule,
    render_decision,
    run_graph_stages,
)
from incidentops_ai.graph.catalogue import load_catalogue
from incidentops_ai.graph.memory import InMemoryGraphStore
from incidentops_ai.retrieval.benchmark import BenchmarkResult
from incidentops_ai.retrieval.documents import load_corpus
from incidentops_ai.retrieval.embeddings import FakeEmbedder
from incidentops_ai.retrieval.evalset import load_questions
from incidentops_ai.retrieval.metrics import Summary
from incidentops_ai.retrieval.stack_benchmark import StageRun, parse_config

ROOT = Path(__file__).resolve().parent.parent
DOCUMENTS = load_corpus(ROOT / "data" / "corpus")
GRAPH_QUESTIONS = load_questions(ROOT / "evals" / "graph" / "questions.jsonl")
STORE = InMemoryGraphStore([load_catalogue(ROOT / "data" / "graph" / "catalogue.toml")])


async def test_every_graph_stage_runs_on_the_question_set():
    runs = await run_graph_stages(
        parse_config("fixed-2000-200"), DOCUMENTS, GRAPH_QUESTIONS, FakeEmbedder(), STORE
    )

    assert [r.stage for r in runs] == list(GRAPH_STAGES)
    assert GRAPH_STAGES == ("hybrid", "hard-filter", "anchor", "graph", "graph-0hop")
    assert all(len(r.result.scores) == 12 for r in runs)
    by_stage = {r.stage: r for r in runs}
    assert by_stage["graph"].result.retriever == "hybrid-rrf+graph1"
    # An anchored stage searches twice per question, so it pays for two query embeddings.
    assert by_stage["graph"].extras["usd_per_1000_queries"] == pytest.approx(
        2 * by_stage["hybrid"].extras["usd_per_1000_queries"]
    )


def run(stage: str, mrr: float, recall_5: float) -> StageRun:
    summary = Summary(n=12, recall={5: recall_5}, precision={5: 0.0}, hit={1: 0.0}, mrr=mrr)
    result = BenchmarkResult(stage, 10, [], {}, [], summary, {}, [])
    return StageRun(stage, "c", result, 0.0)


def table(**stages: tuple[float, float]) -> dict[str, StageRun]:
    return {name.replace("_", "-"): run(name, *values) for name, values in stages.items()}


NEUTRAL = table(hybrid=(0.80, 0.9), anchor=(0.80, 0.9), graph=(0.80, 0.9))


def decide(prod_graph, prod_retrieval=NEUTRAL, check_graph=NEUTRAL, check_retrieval=NEUTRAL):
    return apply_rule(
        production={"graph": prod_graph, "retrieval": prod_retrieval},
        check={"graph": check_graph, "retrieval": check_retrieval},
    )


def test_the_soft_anchor_is_adopted_when_it_clears_the_bar_and_the_graph_does_not():
    decision = decide(table(hybrid=(0.70, 0.8), anchor=(0.75, 0.8), graph=(0.78, 0.9)))

    assert decision.stage == "anchor"


def test_the_graph_replaces_the_anchor_when_it_clears_the_bar_again():
    decision = decide(table(hybrid=(0.70, 0.8), anchor=(0.75, 0.8), graph=(0.80, 0.9)))

    assert decision.stage == "graph"


def test_the_graph_is_compared_with_hybrid_when_the_anchor_was_not_adopted():
    decision = decide(table(hybrid=(0.70, 0.8), anchor=(0.72, 0.8), graph=(0.75, 0.8)))

    assert decision.stage == "graph"


def test_a_stage_whose_recall_falls_is_not_adopted():
    decision = decide(table(hybrid=(0.70, 0.9), anchor=(0.80, 0.8), graph=(0.70, 0.9)))

    assert decision.stage == "hybrid"


def test_a_stage_that_costs_more_than_0_02_on_the_thirty_questions_is_not_adopted():
    decision = decide(
        table(hybrid=(0.70, 0.8), anchor=(0.80, 0.9), graph=(0.70, 0.8)),
        prod_retrieval=table(hybrid=(0.90, 1.0), anchor=(0.87, 1.0), graph=(0.90, 1.0)),
    )

    assert decision.stage == "hybrid"


def test_the_check_config_can_veto_the_production_choice():
    decision = decide(
        table(hybrid=(0.70, 0.8), anchor=(0.70, 0.8), graph=(0.80, 0.9)),
        check_retrieval=table(hybrid=(0.90, 1.0), anchor=(0.90, 1.0), graph=(0.85, 1.0)),
    )

    assert decision.stage == "hybrid"
    assert any("section" in step or "check" in step for step in decision.steps)


def test_the_decision_is_written_out_with_its_reasons():
    decision = decide(table(hybrid=(0.70, 0.8), anchor=(0.75, 0.8), graph=(0.80, 0.9)))

    text = render_decision(decision)

    assert "## Decision" in text and "**graph**" in text
    assert "0.04" in text and "0.02" in text
