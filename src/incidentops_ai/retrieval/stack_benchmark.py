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
import os
import statistics
import subprocess
import time
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
from incidentops_ai.retrieval.rerank import (
    DEFAULT_BATCH_SIZE,
    RERANK_DEPTH,
    TOKEN_LIMIT,
    CrossEncoderReranker,
    FakeReranker,
    Reranker,
    RerankingRetriever,
)
from incidentops_ai.retrieval.retriever import Retriever

STAGES = ("dense", "bm25", "hybrid", "hybrid-rerank")
# The two configs the stack is chosen on; anything else in a report is exploratory.
DECISION_CONFIGS = ("fixed-2000-200", "section-1500+ctx")
DEFAULT_CONFIGS = (*DECISION_CONFIGS, "section-1500")
# Which earlier stages a stage's regressions are read against.
COMPARED_TO = {"bm25": ("dense",), "hybrid": ("dense", "bm25"), "hybrid-rerank": ("hybrid",)}
# Stages that send the question to the embedding API.
EMBEDS_QUERY = frozenset({"dense", "hybrid", "hybrid-rerank"})

# "Too heavy to run locally" bars, fixed before the first run (docs/roadmap-w04.md).
RERANK_MAX_P95_MS = 300.0  # added by the reranker for 20 candidates
RERANK_MAX_ADDED_MB = 500.0  # memory the reranker adds
END_TO_END_P95_BUDGET_MS = 1000.0  # whole stack, per question

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


class _TimedReranker:
    """Passes through to a reranker and records how long each call took."""

    def __init__(self, inner: Reranker) -> None:
        self.name = inner.name
        self.inner = inner
        self.elapsed_ms: list[float] = []

    async def score(self, query: str, passages: Sequence[str]) -> list[float]:
        started = time.perf_counter()
        try:
            return await self.inner.score(query, passages)
        finally:
            self.elapsed_ms.append((time.perf_counter() - started) * 1000)


def _current_rss_mb() -> float:
    """Resident memory of this process now (not the high-water mark, which hides growth below an
    earlier peak). 0.0 if `ps` is unavailable."""
    try:
        out = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(os.getpid())], capture_output=True, text=True, check=True
        )
        return int(out.stdout.strip()) / 1024  # ps reports kilobytes on macOS and Linux
    except (OSError, ValueError, subprocess.CalledProcessError):
        return 0.0


def rerank_verdict(p95_ms: float, added_mb: float) -> tuple[bool, str]:
    """Whether the reranker is too heavy to run on this machine, by the bars set in advance."""
    problems = []
    if p95_ms > RERANK_MAX_P95_MS:
        problems.append(f"p95 {p95_ms:.0f} ms is above the {RERANK_MAX_P95_MS:.0f} ms bar")
    if added_mb > RERANK_MAX_ADDED_MB:
        problems.append(
            f"memory added {added_mb:.0f} MB is above the {RERANK_MAX_ADDED_MB:.0f} MB bar"
        )
    if problems:
        bars = f"Bars: p95 {RERANK_MAX_P95_MS:.0f} ms, memory added {RERANK_MAX_ADDED_MB:.0f} MB."
        return True, "**Too heavy locally:** " + "; ".join(problems) + f". {bars}"
    return False, (
        f"Within the local bars: p95 {p95_ms:.0f} ms (bar {RERANK_MAX_P95_MS:.0f} ms), "
        f"memory added {added_mb:.0f} MB (bar {RERANK_MAX_ADDED_MB:.0f} MB)."
    )


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
    reranker: Reranker | None = None,
    rerank_depth: int = RERANK_DEPTH,
) -> list[StageRun]:
    """Score each stage on the same chunks. `query_embedder`, when given, embeds the questions
    (timed for real); `embedder` then only embeds the chunks."""
    for stage in stages:
        if stage not in STAGES:
            raise ValueError(f"unknown stage {stage!r}; valid stages: {', '.join(STAGES)}")
    if "hybrid-rerank" in stages and reranker is None:
        raise ValueError("stage 'hybrid-rerank' needs a reranker")
    timed = _TimedReranker(reranker) if reranker is not None else None
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
            elif stage == "hybrid":
                retrievers[stage] = HybridRetriever(
                    await retriever_for("dense"), await retriever_for("bm25")
                )
            else:
                assert timed is not None  # checked above
                retrievers[stage] = RerankingRetriever(
                    await retriever_for("hybrid"), timed, depth=rerank_depth
                )
        return retrievers[stage]

    runs: list[StageRun] = []
    for stage in stages:
        retriever = await retriever_for(stage)
        extras = {"usd_per_1000_queries": _query_cost_per_1000(stage, questions)}
        reranking = stage == "hybrid-rerank"
        if reranking:
            assert timed is not None  # checked above
            rss_before = _current_rss_mb()
            started = time.perf_counter()
            await timed.score("warm up", ["warm up"])  # loads the model; not part of query latency
            extras["load_seconds"] = time.perf_counter() - started
            timed.elapsed_ms.clear()
            truncated_before = getattr(reranker, "truncated", 0)
        result = await run_benchmark(retriever, documents, questions, k=k)
        if reranking:
            assert timed is not None
            extras.update(
                depth=float(rerank_depth),
                rerank_mean_ms=statistics.fmean(timed.elapsed_ms),
                rerank_p95_ms=percentile(timed.elapsed_ms, 0.95),
                rss_added_mb=max(_current_rss_mb() - rss_before, 0.0),
                truncated=float(getattr(reranker, "truncated", 0) - truncated_before),
            )
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
                extras=extras,
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
        if "hybrid-rerank" in by_stage:
            lines += _reranker_section(
                by_stage["hybrid-rerank"], int(by_stage["hybrid-rerank"].extras["depth"])
            )
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


def _reranker_section(run: StageRun, rerank_depth: int) -> list[str]:
    extra = run.extras
    _, verdict = rerank_verdict(extra["rerank_p95_ms"], extra["rss_added_mb"])
    end_to_end = percentile(run.result.latencies_ms, 0.95)
    within = "within" if end_to_end < END_TO_END_P95_BUDGET_MS else "OVER"
    return [
        "### Reranker, local cost",
        "",
        (
            f"- Load and first call: {extra['load_seconds']:.1f} s. Reranking {rerank_depth} "
            f"candidates: mean {extra['rerank_mean_ms']:.0f} ms, p95 {extra['rerank_p95_ms']:.0f} ms."
        ),
        (
            f"- Memory added: {extra['rss_added_mb']:.0f} MB. Candidates cut at the "
            f"{TOKEN_LIMIT}-token limit: {extra['truncated']:.0f} of "
            f"{rerank_depth * run.result.summary.n} reranked."
        ),
        f"- {verdict}",
        (
            f"- Whole stack, end to end p95: {end_to_end:.0f} ms, {within} the "
            f"{END_TO_END_P95_BUDGET_MS:.0f} ms budget."
        ),
        "",
    ]


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
    reranker: Reranker | None = None
    if "hybrid-rerank" in stages:
        reranker = (
            FakeReranker() if args.fake else CrossEncoderReranker(batch_size=args.rerank_batch_size)
        )
    try:
        runs_by_config = {
            name: await run_stages(
                parse_config(name),
                documents,
                questions,
                embedder,
                stages=stages,
                query_embedder=live,
                reranker=reranker,
                rerank_depth=args.rerank_depth,
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
    parser.add_argument("--rerank-depth", type=int, default=RERANK_DEPTH)
    parser.add_argument("--rerank-batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument(
        "--fake", action="store_true", help="offline hashed embeddings (wiring check)"
    )
    asyncio.run(_run(parser.parse_args(argv)))


if __name__ == "__main__":
    main()
