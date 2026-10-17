"""AIENG-38: does graph context improve retrieval, and where? The comparison and its fixed rule.

    uv run python -m incidentops_ai.graph.benchmark

Five stages on two question sets (the 12 graph-sensitive questions and the original 30) and two
chunkings. The stages, the rule and the hypotheses were written in docs/roadmap-w05.md before the
first run; `apply_rule` applies that rule and nothing else.
"""

import argparse
import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from incidentops_ai.config import Settings
from incidentops_ai.graph.catalogue import load_catalogue
from incidentops_ai.graph.memory import InMemoryGraphStore
from incidentops_ai.graph.retriever import AnchoredRetriever, GraphNeighbourhood, OwnDocuments
from incidentops_ai.graph.store import GraphStore
from incidentops_ai.retrieval.benchmark import TOP_K, BenchmarkFiles, run_benchmark
from incidentops_ai.retrieval.bm25 import Bm25Retriever
from incidentops_ai.retrieval.documents import Document, load_corpus
from incidentops_ai.retrieval.embeddings import CachingEmbedder, Embedder, FakeEmbedder
from incidentops_ai.retrieval.evalset import Question, load_questions
from incidentops_ai.retrieval.experiment import ExperimentConfig
from incidentops_ai.retrieval.factory import build_embedder
from incidentops_ai.retrieval.hybrid import HybridRetriever
from incidentops_ai.retrieval.memory import InMemoryDenseRetriever
from incidentops_ai.retrieval.stack_benchmark import (
    ReportStyle,
    SplitEmbedder,
    StageRun,
    parse_config,
    query_cost_per_1000,
    write_stack_report,
)

GRAPH_STAGES = ("hybrid", "hard-filter", "anchor", "graph", "graph-0hop")
ANCHORED = frozenset({"anchor", "graph", "graph-0hop"})  # these search twice per question
COMPARED_TO = {
    "hard-filter": ("hybrid",),
    "anchor": ("hybrid",),
    "graph": ("hybrid", "anchor"),
    "graph-0hop": ("anchor",),
}
PRODUCTION_CONFIG = "fixed-2000-200"
CHECK_CONFIG = "section-1500+ctx"
QUESTION_SETS = ("graph", "retrieval")

# Fixed before the run (docs/roadmap-w05.md, "How the stage is chosen").
MRR_GAIN = 0.04  # on the 12 graph questions: half a question
MRR_LOSS = 0.02  # allowed on the 30 retrieval questions
_EPS = 1e-9


async def run_graph_stages(
    config: ExperimentConfig,
    documents: Sequence[Document],
    questions: Sequence[Question],
    embedder: Embedder,
    store: GraphStore,
    *,
    k: int = TOP_K,
    query_embedder: Embedder | None = None,
) -> list[StageRun]:
    chunks = [chunk for document in documents for chunk in config.chunker.chunk(document)]
    texts = [q.text for q in questions]
    if query_embedder is None:
        dense_embedder = embedder
        await embedder.embed(texts)  # one batch, so a cache warms in one request
    else:
        dense_embedder = SplitEmbedder(embedder, query_embedder, set(texts))
    dense = await InMemoryDenseRetriever.build(
        chunks, dense_embedder, with_context=config.with_context
    )
    hybrid = HybridRetriever(dense, Bm25Retriever(chunks, with_context=config.with_context))
    stages = {
        "hybrid": (hybrid, {}),
        "hard-filter": (hybrid, {"use_service_filter": True}),
        "anchor": (AnchoredRetriever(hybrid, OwnDocuments()), {"use_service_hint": True}),
        "graph": (
            AnchoredRetriever(hybrid, GraphNeighbourhood(store, hops=1)),
            {"use_service_hint": True},
        ),
        "graph-0hop": (
            AnchoredRetriever(hybrid, GraphNeighbourhood(store, hops=0)),
            {"use_service_hint": True},
        ),
    }
    runs = []
    for name in GRAPH_STAGES:
        retriever, options = stages[name]
        result = await run_benchmark(retriever, documents, questions, k=k, **options)
        calls = 2 if name in ANCHORED else 1
        runs.append(
            StageRun(
                stage=name,
                config=config.name,
                result=result,
                mean_chars_read_at_5=sum(
                    sum(len(r.chunk.text) for r in ranked[:5])
                    for ranked in result.rankings.values()
                )
                / len(result.rankings),
                extras={"usd_per_1000_queries": calls * query_cost_per_1000("dense", questions)},
            )
        )
    return runs


@dataclass(frozen=True)
class Decision:
    stage: str
    steps: list[str] = field(default_factory=list)


def _passes(
    candidate: str, current: str, runs: Mapping[str, Mapping[str, StageRun]], steps: list[str]
) -> bool:
    new_g, old_g = runs["graph"][candidate].result.summary, runs["graph"][current].result.summary
    new_r = runs["retrieval"][candidate].result.summary
    old_r = runs["retrieval"][current].result.summary
    gain = new_g.mrr - old_g.mrr
    recall_ok = new_g.recall[5] >= old_g.recall[5] - _EPS
    loss = old_r.mrr - new_r.mrr
    ok = gain >= MRR_GAIN - _EPS and recall_ok and loss <= MRR_LOSS + _EPS
    steps.append(
        f"{candidate} vs {current}: graph-set MRR {old_g.mrr:.3f} → {new_g.mrr:.3f} "
        f"({gain:+.3f}, needs +{MRR_GAIN}), Recall@5 {old_g.recall[5]:.3f} → "
        f"{new_g.recall[5]:.3f}{'' if recall_ok else ' (falls)'}, 30-question MRR "
        f"{old_r.mrr:.3f} → {new_r.mrr:.3f} (may fall by {MRR_LOSS}): "
        f"{'adopted' if ok else 'not adopted'}"
    )
    return ok


def apply_rule(
    *,
    production: Mapping[str, Mapping[str, StageRun]],
    check: Mapping[str, Mapping[str, StageRun]],
) -> Decision:
    """`production` and `check` map a question set ("graph", "retrieval") to its runs by stage."""
    steps: list[str] = []
    adopted = ["hybrid"]
    for candidate in ("anchor", "graph"):
        if _passes(candidate, adopted[-1], production, steps):
            adopted.append(candidate)
    while adopted[-1] != "hybrid":
        stage = adopted[-1]
        losses = {
            name: check[name]["hybrid"].result.summary.mrr - check[name][stage].result.summary.mrr
            for name in QUESTION_SETS
        }
        worst = max(losses, key=losses.get)
        if losses[worst] <= MRR_LOSS + _EPS:
            steps.append(f"{CHECK_CONFIG} check: {stage} loses at most {losses[worst]:.3f}, kept")
            break
        steps.append(
            f"{CHECK_CONFIG} check: {stage} loses {losses[worst]:.3f} MRR on the {worst} set "
            f"against hybrid (more than {MRR_LOSS}), vetoed"
        )
        adopted.pop()
    return Decision(adopted[-1], steps)


def render_decision(decision: Decision) -> str:
    lines = [
        "## Decision",
        "",
        (
            "By the rule fixed in `docs/roadmap-w05.md`: a stage replaces the current one if MRR on "
            f"the graph set rises by at least {MRR_GAIN} without lowering Recall@5, and MRR on the "
            f"30 retrieval questions falls by no more than {MRR_LOSS}; on {CHECK_CONFIG} the "
            f"adopted stage may not lose more than {MRR_LOSS} against hybrid."
        ),
        "",
        f"Adopted: **{decision.stage}**",
        "",
        *[f"- {step}" for step in decision.steps],
        "",
    ]
    return "\n".join(lines)


async def _run(args: argparse.Namespace) -> BenchmarkFiles:
    settings = Settings()
    live: Embedder | None = None
    if args.fake:
        embedder: Embedder = FakeEmbedder(settings.embedding_dimensions)
    else:
        live = build_embedder(settings)
        embedder = CachingEmbedder(live, args.cache_dir)
    documents = load_corpus(args.corpus)
    store = InMemoryGraphStore([load_catalogue(args.catalogue)])
    question_sets = {
        "graph": load_questions(args.graph_questions),
        "retrieval": load_questions(args.retrieval_questions),
    }
    runs: dict[str, dict[str, dict[str, StageRun]]] = {}
    try:
        for config_name in (PRODUCTION_CONFIG, CHECK_CONFIG):
            runs[config_name] = {}
            for set_name, questions in question_sets.items():
                stage_runs = await run_graph_stages(
                    parse_config(config_name),
                    documents,
                    questions,
                    embedder,
                    store,
                    query_embedder=live,
                )
                runs[config_name][set_name] = {r.stage: r for r in stage_runs}
    finally:
        if live is not None and hasattr(live, "aclose"):
            await live.aclose()

    decision = apply_rule(production=runs[PRODUCTION_CONFIG], check=runs[CHECK_CONFIG])
    by_label = {
        f"{config} · {set_name} questions": list(stages.values())
        for config, sets in runs.items()
        for set_name, stages in sets.items()
    }
    style = ReportStyle(
        title="Graph-assisted retrieval comparison",
        reproduce="uv run python -m incidentops_ai.graph.benchmark",
        compared_to=COMPARED_TO,
        decision_configs=tuple(
            label for label in by_label if label.startswith(f"{PRODUCTION_CONFIG} ")
        ),
    )
    files = write_stack_report(
        by_label,
        args.out_dir,
        embedding_model=embedder.model,
        run_date=args.date,
        name=args.name,
        style=style,
        extra_markdown="\n" + render_decision(decision),
    )
    print(render_decision(decision))
    print(f"Wrote {files.report_path} and {files.data_path}")
    return files


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Compare graph-assisted retrieval (AIENG-38).")
    parser.add_argument("--corpus", type=Path, default=Path("data/corpus"))
    parser.add_argument("--catalogue", type=Path, default=Path("data/graph/catalogue.toml"))
    parser.add_argument("--graph-questions", type=Path, default=Path("evals/graph/questions.jsonl"))
    parser.add_argument(
        "--retrieval-questions", type=Path, default=Path("evals/retrieval/questions.jsonl")
    )
    parser.add_argument("--out-dir", type=Path, default=Path("evals/graph/runs"))
    parser.add_argument("--cache-dir", type=Path, default=Path("evals/retrieval/.cache"))
    parser.add_argument("--date", default=datetime.now(UTC).date().isoformat())
    parser.add_argument("--name", default="graph-comparison")
    parser.add_argument(
        "--fake", action="store_true", help="offline hashed embeddings (wiring check)"
    )
    asyncio.run(_run(parser.parse_args(argv)))


if __name__ == "__main__":
    main()
