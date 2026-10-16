"""AIENG-29 to AIENG-33: compare retrieval stages on the eval set, one table per chunking config.

    uv run python -m incidentops_ai.retrieval.stack_benchmark

Every stage is a `Retriever`, so one loop scores them all against the same 30 questions. Writes
evals/retrieval/runs/<date>-<name>.md (tables, questions that moved, every miss) and .json (every
question's result). The analysis of *why* things moved is written by hand next to the report; the
stages, the configs and the rule that picks a stack were fixed in docs/roadmap-w04.md before the
first run, and this module only produces the evidence.
"""

import argparse
import asyncio
import json
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from incidentops_ai.config import Settings
from incidentops_ai.retrieval.benchmark import (
    TOP_K,
    BenchmarkFiles,
    BenchmarkResult,
    percentile,
    render_misses,
    run_benchmark,
    summary_data,
)
from incidentops_ai.retrieval.bm25 import Bm25Retriever
from incidentops_ai.retrieval.chunking import chunker_from_name
from incidentops_ai.retrieval.documents import Document, load_corpus
from incidentops_ai.retrieval.embeddings import CachingEmbedder, Embedder, FakeEmbedder
from incidentops_ai.retrieval.evalset import Question, load_questions
from incidentops_ai.retrieval.experiment import ExperimentConfig
from incidentops_ai.retrieval.factory import build_embedder
from incidentops_ai.retrieval.hybrid import HybridRetriever
from incidentops_ai.retrieval.memory import InMemoryDenseRetriever
from incidentops_ai.retrieval.retriever import Retriever

STAGES = ("dense", "bm25", "hybrid")
# The two configs the stack is chosen on; anything else in a report is exploratory.
DECISION_CONFIGS = ("fixed-2000-200", "section-1500+ctx")
DEFAULT_CONFIGS = (*DECISION_CONFIGS, "section-1500")
# Which earlier stages a stage's regressions are read against.
COMPARED_TO = {"bm25": ("dense",), "hybrid": ("dense", "bm25"), "hybrid-rerank": ("hybrid",)}
# Stages that send the question to the embedding API.
EMBEDS_QUERY = frozenset({"dense", "hybrid", "hybrid-rerank"})

# List price of text-embedding-3-small per million tokens; an estimate, check the current price.
EMBEDDING_USD_PER_MILLION_TOKENS = 0.02
CHARS_PER_TOKEN = 4


@dataclass(frozen=True)
class StageRun:
    stage: str
    config: str
    result: BenchmarkResult
    mean_chars_read_at_5: float  # text a reader (or a prompt) takes in for the top five
    # Measurements specific to a stage, such as the estimated query cost or the reranker load time.
    extras: dict[str, float] = field(default_factory=dict)


def parse_config(name: str) -> ExperimentConfig:
    """`fixed-2000-200`, or `section-1500+ctx` for the context-header variant."""
    return ExperimentConfig(chunker_from_name(name.removesuffix("+ctx")), name.endswith("+ctx"))


class _SplitEmbedder:
    """Chunk texts go to `chunks` (usually a cache), question texts to `queries` (a live call).

    Corpus vectors can then be cached while the query path is timed for real; otherwise the cache
    would make the dense stage look free.
    """

    def __init__(self, chunks: Embedder, queries: Embedder, question_texts: set[str]) -> None:
        self.model = chunks.model
        self.dimensions = chunks.dimensions
        self._chunks = chunks
        self._queries = queries
        self._question_texts = question_texts

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        is_query = bool(texts) and set(texts) <= self._question_texts
        return await (self._queries if is_query else self._chunks).embed(texts)


def _query_cost_per_1000(stage: str, questions: Sequence[Question]) -> float:
    if stage not in EMBEDS_QUERY:
        return 0.0
    tokens = sum(len(q.text) for q in questions) / len(questions) / CHARS_PER_TOKEN
    return tokens * 1000 * EMBEDDING_USD_PER_MILLION_TOKENS / 1_000_000


async def run_stages(
    config: ExperimentConfig,
    documents: Sequence[Document],
    questions: Sequence[Question],
    embedder: Embedder,
    *,
    stages: Sequence[str],
    k: int = TOP_K,
    query_embedder: Embedder | None = None,
) -> list[StageRun]:
    """Score each stage on the same chunks. `query_embedder`, when given, embeds the questions
    (timed for real); `embedder` then only embeds the chunks."""
    for stage in stages:
        if stage not in STAGES:
            raise ValueError(f"unknown stage {stage!r}; valid stages: {', '.join(STAGES)}")
    chunks = [chunk for document in documents for chunk in config.chunker.chunk(document)]
    question_texts = [q.text for q in questions]
    if query_embedder is None:
        dense_embedder = embedder
        if any(stage in EMBEDS_QUERY for stage in stages):
            await embedder.embed(question_texts)  # one batch, so a cache warms in one request
    else:
        dense_embedder = _SplitEmbedder(embedder, query_embedder, set(question_texts))

    retrievers: dict[str, Retriever] = {}

    async def retriever_for(stage: str) -> Retriever:
        if stage not in retrievers:
            if stage == "dense":
                retrievers[stage] = await InMemoryDenseRetriever.build(
                    chunks, dense_embedder, with_context=config.with_context
                )
            elif stage == "bm25":
                retrievers[stage] = Bm25Retriever(chunks, with_context=config.with_context)
            else:
                retrievers[stage] = HybridRetriever(
                    await retriever_for("dense"), await retriever_for("bm25")
                )
        return retrievers[stage]

    runs: list[StageRun] = []
    for stage in stages:
        result = await run_benchmark(await retriever_for(stage), documents, questions, k=k)
        runs.append(
            StageRun(
                stage=stage,
                config=config.name,
                result=result,
                mean_chars_read_at_5=sum(
                    sum(len(r.chunk.text) for r in ranked[:5])
                    for ranked in result.rankings.values()
                )
                / len(result.rankings),
                extras={"usd_per_1000_queries": _query_cost_per_1000(stage, questions)},
            )
        )
    return runs


def questions_moved(before: StageRun, after: StageRun) -> tuple[list[str], list[str]]:
    """Ids of questions that got better and worse from `before` to `after`.

    Better means more of the evidence in the top five, or the same evidence found sooner. This is
    the list to read: a table average can hide a question that went from found to lost.
    """
    earlier = {s.question_id: (s.recall[5], s.reciprocal_rank) for s in before.result.scores}
    improved: list[str] = []
    worse: list[str] = []
    for s in after.result.scores:
        now, then = (s.recall[5], s.reciprocal_rank), earlier[s.question_id]
        if now > then:
            improved.append(s.question_id)
        elif now < then:
            worse.append(s.question_id)
    return improved, worse


def _ids(ids: Sequence[str]) -> str:
    return f"{len(ids)} ({', '.join(ids)})" if ids else "0"


def _stage_row(run: StageRun) -> str:
    summary, latency = run.result.summary, run.result.latencies_ms
    cells = [
        run.stage,
        f"{summary.recall[1]:.3f}",
        f"{summary.recall[5]:.3f}",
        f"{summary.precision[5]:.3f}",
        f"{summary.hit[1]:.3f}",
        f"{summary.mrr:.3f}",
        f"{run.mean_chars_read_at_5:.0f}",
        f"{statistics.fmean(latency):.0f}",
        f"{percentile(latency, 0.5):.0f}",
        f"{percentile(latency, 0.95):.0f}",
        f"{run.extras.get('usd_per_1000_queries', 0.0):.4f}",
    ]
    return "| " + " | ".join(cells) + " |"


def render_stack_report(
    runs_by_config: Mapping[str, Sequence[StageRun]], *, embedding_model: str, run_date: str
) -> str:
    sample = next(iter(next(iter(runs_by_config.values())))).result
    lines = [f"# Retrieval stack comparison, {run_date}", ""]
    if embedding_model.startswith("fake"):
        lines += [
            (
                "> **OFFLINE WIRING CHECK.** These vectors are hashed words, not a language model. "
                "The numbers show that the pipeline runs and say nothing about semantic retrieval."
            ),
            "",
        ]
    lines += [
        f"- Embedding model: `{embedding_model}`",
        (
            f"- Questions: {sample.summary.n}; top {sample.k}; a chunk is relevant when it holds "
            "at least half of an evidence span"
        ),
        "- Reproduce: `uv run python -m incidentops_ai.retrieval.stack_benchmark`",
        (
            "- Latency is end to end per question. Dense stages include one query-embedding call "
            "(live when the run used the API, so it reflects the network)."
        ),
        (
            "- Cost is an *estimate* of query-embedding API spend per 1,000 queries "
            f"(${EMBEDDING_USD_PER_MILLION_TOKENS} per million tokens, {CHARS_PER_TOKEN} characters "
            "per token). BM25 and a local reranker cost no API money."
        ),
        "",
    ]
    for config, runs in runs_by_config.items():
        label = "decision config" if config in DECISION_CONFIGS else "exploratory"
        by_stage = {run.stage: run for run in runs}
        header = [
            "Stage",
            "Recall@1",
            "Recall@5",
            "Precision@5",
            "Hit@1",
            "MRR@10",
            "Chars read@5",
            "mean ms",
            "p50 ms",
            "p95 ms",
            "est. $/1,000 queries",
        ]
        lines += [
            f"## {config} ({label})",
            "",
            "| " + " | ".join(header) + " |",
            "|" + "---|" * len(header),
            *[_stage_row(run) for run in runs],
            "",
        ]
        moved = [
            (run.stage, base)
            for run in runs
            for base in COMPARED_TO.get(run.stage, ())
            if base in by_stage
        ]
        if moved:
            lines += ["### Questions moved", ""]
            for stage, base in moved:
                improved, worse = questions_moved(by_stage[base], by_stage[stage])
                lines.append(f"- {stage} vs {base}: improved {_ids(improved)}; worse {_ids(worse)}")
            lines.append("")
        categories = list(runs[0].result.by_category)
        lines += [
            "### MRR by question style",
            "",
            "| Style | n | " + " | ".join(run.stage for run in runs) + " |",
            "|" + "---|" * (len(runs) + 2),
            *[
                f"| {name} | {runs[0].result.by_category[name].n} | "
                + " | ".join(f"{run.result.by_category[name].mrr:.3f}" for run in runs)
                + " |"
                for name in categories
            ],
            "",
        ]
        for run in runs:
            lines += [
                (
                    f"### Misses: {run.stage} "
                    f"({len(run.result.misses)} of {run.result.summary.n} questions)"
                ),
                "",
                "Questions whose evidence is not all inside the top 5.",
                "",
                *render_misses(run.result, heading="####"),
            ]
    return "\n".join(lines)


def _stage_data(run: StageRun) -> dict:
    result = run.result
    return {
        "retriever": result.retriever,
        "summary": summary_data(result.summary),
        "by_category": {n: summary_data(s) for n, s in result.by_category.items()},
        "mean_chars_read_at_5": round(run.mean_chars_read_at_5, 1),
        "extras": {key: round(value, 6) for key, value in run.extras.items()},
        "latency_ms": [round(ms, 1) for ms in result.latencies_ms],
        "questions": {
            s.question_id: {
                "category": s.category,
                "reciprocal_rank": round(s.reciprocal_rank, 4),
                "first_relevant_rank": s.first_relevant_rank,
                "recall": {str(k): round(v, 4) for k, v in s.recall.items()},
                "top": [
                    {
                        "rank": r.rank,
                        "chunk_id": str(r.chunk.id),
                        "doc_id": r.chunk.doc_id,
                        "section": r.chunk.section,
                        "score": round(r.score, 6),
                    }
                    for r in result.rankings[s.question_id]
                ],
            }
            for s in result.scores
        },
    }


def write_stack_report(
    runs_by_config: Mapping[str, Sequence[StageRun]],
    out_dir: Path,
    *,
    embedding_model: str,
    run_date: str,
    name: str = "stack-comparison",
) -> BenchmarkFiles:
    out_dir.mkdir(parents=True, exist_ok=True)
    files = BenchmarkFiles(out_dir / f"{run_date}-{name}.md", out_dir / f"{run_date}-{name}.json")
    files.report_path.write_text(
        render_stack_report(runs_by_config, embedding_model=embedding_model, run_date=run_date),
        encoding="utf-8",
    )
    data = {
        "date": run_date,
        "embedding_model": embedding_model,
        "configs": {
            config: {run.stage: _stage_data(run) for run in runs}
            for config, runs in runs_by_config.items()
        },
    }
    files.data_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return files


async def _run(args: argparse.Namespace) -> BenchmarkFiles:
    settings = Settings()
    live: Embedder | None = None
    if args.fake:
        embedder: Embedder = FakeEmbedder(settings.embedding_dimensions)
    else:
        live = build_embedder(settings)
        embedder = CachingEmbedder(live, args.cache_dir)
    documents = load_corpus(args.corpus)
    questions = load_questions(args.questions)
    stages = tuple(args.stages.split(","))
    try:
        runs_by_config = {
            name: await run_stages(
                parse_config(name),
                documents,
                questions,
                embedder,
                stages=stages,
                query_embedder=live,
            )
            for name in args.configs.split(",")
        }
    finally:
        if live is not None and hasattr(live, "aclose"):
            await live.aclose()
    files = write_stack_report(
        runs_by_config,
        args.out_dir,
        embedding_model=embedder.model,
        run_date=args.date,
        name=args.name,
    )
    for config, runs in runs_by_config.items():
        print(config)
        print("\n".join(_stage_row(run) for run in runs))
    print(f"Wrote {files.report_path} and {files.data_path}")
    return files


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Compare retrieval stages (AIENG-29 to 33).")
    parser.add_argument("--corpus", type=Path, default=Path("data/corpus"))
    parser.add_argument("--questions", type=Path, default=Path("evals/retrieval/questions.jsonl"))
    parser.add_argument("--out-dir", type=Path, default=Path("evals/retrieval/runs"))
    parser.add_argument("--cache-dir", type=Path, default=Path("evals/retrieval/.cache"))
    parser.add_argument("--date", default=datetime.now(UTC).date().isoformat())
    parser.add_argument("--name", default="stack-comparison")
    parser.add_argument("--stages", default=",".join(STAGES), help=f"from: {', '.join(STAGES)}")
    parser.add_argument("--configs", default=",".join(DEFAULT_CONFIGS))
    parser.add_argument(
        "--fake", action="store_true", help="offline hashed embeddings (wiring check)"
    )
    asyncio.run(_run(parser.parse_args(argv)))


if __name__ == "__main__":
    main()
