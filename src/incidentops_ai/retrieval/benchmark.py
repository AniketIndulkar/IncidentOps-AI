"""AIENG-28: benchmark a retriever on the eval set and write a repeatable report.

    uv run python -m incidentops_ai.retrieval.index        # embed the stored chunks
    uv run python -m incidentops_ai.retrieval.benchmark    # score dense retrieval

The report lists the metrics, query latency, results by question style and every miss. The
analysis of *why* things were missed is written by hand next to the report; this module only
produces the evidence.
"""

import argparse
import asyncio
import json
import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from incidentops_ai.config import Settings
from incidentops_ai.db import create_engine, create_session_factory
from incidentops_ai.retrieval.documents import Document, load_corpus
from incidentops_ai.retrieval.embeddings import Embedder, FakeEmbedder
from incidentops_ai.retrieval.evalset import Evidence, Question, load_questions, resolve_question
from incidentops_ai.retrieval.evaluate import evaluate_retriever
from incidentops_ai.retrieval.factory import build_embedder
from incidentops_ai.retrieval.metrics import (
    QuestionScore,
    Summary,
    covers,
    summarize,
    summarize_by_category,
)
from incidentops_ai.retrieval.retriever import RetrievedChunk, Retriever
from incidentops_ai.retrieval.store import PgVectorRetriever

TOP_K = 10
MISS_K = 5  # a question is a miss when its evidence is not all inside the top 5
SNIPPET_CHARS = 110


@dataclass(frozen=True)
class Miss:
    question: Question
    missing: tuple[Evidence, ...]  # evidence not covered by any of the top 5
    top: tuple[RetrievedChunk, ...]  # what was retrieved instead (first three)


@dataclass(frozen=True)
class BenchmarkResult:
    retriever: str
    k: int
    scores: list[QuestionScore]
    rankings: dict[str, list[RetrievedChunk]]
    latencies_ms: list[float]
    summary: Summary
    by_category: dict[str, Summary]
    misses: list[Miss]


@dataclass(frozen=True)
class BenchmarkFiles:
    report_path: Path
    data_path: Path


async def run_benchmark(
    retriever: Retriever,
    documents: Sequence[Document],
    questions: Sequence[Question],
    *,
    k: int = TOP_K,
    use_service_filter: bool = False,
    use_service_hint: bool = False,
) -> BenchmarkResult:
    evaluation = await evaluate_retriever(
        retriever,
        documents,
        questions,
        k=k,
        use_service_filter=use_service_filter,
        use_service_hint=use_service_hint,
    )
    by_id = {document.id: document for document in documents}
    misses: list[Miss] = []
    for question, score in zip(questions, evaluation.scores, strict=True):
        if score.recall[MISS_K] >= 1.0:
            continue
        ranked = evaluation.rankings[question.id]
        gold = resolve_question(question, by_id)
        missing = tuple(
            evidence
            for evidence, span in zip(question.evidence, gold, strict=True)
            if not any(covers(chunk, span) for chunk in ranked[:MISS_K])
        )
        misses.append(Miss(question, missing, tuple(ranked[:3])))
    return BenchmarkResult(
        retriever=retriever.name,
        k=k,
        scores=evaluation.scores,
        rankings=evaluation.rankings,
        latencies_ms=evaluation.latencies_ms,
        summary=summarize(evaluation.scores),
        by_category=summarize_by_category(evaluation.scores),
        misses=misses,
    )


def percentile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[max(math.ceil(fraction * len(ordered)) - 1, 0)]


def snippet(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= SNIPPET_CHARS else flat[: SNIPPET_CHARS - 1] + "…"


def _metrics_table(summary: Summary) -> list[str]:
    ks = list(summary.recall)
    return [
        "| Metric | " + " | ".join(f"K={k}" for k in ks) + " |",
        "|" + "---|" * (len(ks) + 1),
        "| Recall | " + " | ".join(f"{summary.recall[k]:.3f}" for k in ks) + " |",
        "| Precision | " + " | ".join(f"{summary.precision[k]:.3f}" for k in ks) + " |",
        "| Hit rate | " + " | ".join(f"{summary.hit[k]:.3f}" for k in ks) + " |",
    ]


def render_benchmark_report(
    result: BenchmarkResult,
    *,
    embedding_model: str,
    run_date: str,
    filtered: BenchmarkResult | None = None,
) -> str:
    latency = result.latencies_ms
    lines = [f"# Dense retrieval baseline, {run_date}", ""]
    if embedding_model.startswith("fake"):
        banner = (
            "> **OFFLINE WIRING CHECK.** These vectors are hashed words, not a language model. "
            "The numbers show that the pipeline runs and say nothing about semantic retrieval."
        )
        lines += [banner, ""]
    chunker = next(iter(result.rankings.values()))[0].chunk.chunker
    lines += [
        f"- Retriever: `{result.retriever}`, exact cosine scan, tenant-filtered",
        f"- Embedding model: `{embedding_model}`; chunking: `{chunker}`",
        (
            f"- Questions: {result.summary.n}; top {result.k}; a chunk is relevant when it holds "
            "at least half of an evidence span"
        ),
        "- Reproduce: `uv run python -m incidentops_ai.retrieval.benchmark`",
        "",
        "## Results",
        "",
        *_metrics_table(result.summary),
        "",
        f"**MRR@{result.k}: {result.summary.mrr:.3f}**",
        "",
        (
            "Precision is capped: most questions have one or two evidence spans, so Precision@5 "
            "cannot exceed about 0.4. Use it to compare configurations, not as an absolute score."
        ),
        "",
        "## Latency",
        "",
        "Per question, query embedding plus database search.",
        "",
        (
            f"mean {statistics.fmean(latency):.0f} ms, p50 {percentile(latency, 0.5):.0f} ms, "
            f"p95 {percentile(latency, 0.95):.0f} ms, max {max(latency):.0f} ms"
        ),
        "",
        "## By question style",
        "",
        "| Style | n | Recall@5 | Precision@5 | MRR |",
        "|---|---|---|---|---|",
        *[
            f"| {name} | {s.n} | {s.recall[5]:.3f} | {s.precision[5]:.3f} | {s.mrr:.3f} |"
            for name, s in result.by_category.items()
        ],
        "",
    ]
    if filtered is not None:
        lines += [
            "## Service filter",
            "",
            "Same retriever, restricted to the service named in the question where there is one.",
            "",
            "| | Recall@5 | Precision@5 | MRR |",
            "|---|---|---|---|",
            _filter_row("no filter", result.summary),
            _filter_row("service filter", filtered.summary),
            "",
        ]
    lines += [
        f"## Misses ({len(result.misses)} of {result.summary.n} questions)",
        "",
        f"Questions whose evidence is not all inside the top {MISS_K}.",
        "",
    ]
    lines += render_misses(result)
    return "\n".join(lines)


def render_misses(result: BenchmarkResult, *, heading: str = "###") -> list[str]:
    """One entry per missed question: what was not found and what came back instead."""
    lines: list[str] = []
    for miss in result.misses:
        score = next(s for s in result.scores if s.question_id == miss.question.id)
        found = (
            f"first relevant at rank {score.first_relevant_rank}"
            if score.first_relevant_rank
            else "no relevant chunk in the top 10"
        )
        lines += [
            f"{heading} {miss.question.id} ({miss.question.category}): {miss.question.text}",
            "",
        ]
        lines += [f"- {found}; Recall@5 {score.recall[5]:.2f}"]
        for evidence in miss.missing:
            lines.append(f"- not found: `{evidence.doc_id}`: “{snippet(evidence.quote)}”")
        for r in miss.top:
            lines.append(
                f"- returned #{r.rank} `{r.chunk.doc_id}` › {r.chunk.section or '(no section)'} "
                f"(score {r.score:.3f}): {snippet(r.chunk.text)}"
            )
        lines.append("")
    return lines


def write_benchmark(
    result: BenchmarkResult,
    out_dir: Path,
    *,
    embedding_model: str,
    run_date: str,
    filtered: BenchmarkResult | None = None,
    name: str = "dense-baseline",
) -> BenchmarkFiles:
    out_dir.mkdir(parents=True, exist_ok=True)
    files = BenchmarkFiles(out_dir / f"{run_date}-{name}.md", out_dir / f"{run_date}-{name}.json")
    files.report_path.write_text(
        render_benchmark_report(
            result, embedding_model=embedding_model, run_date=run_date, filtered=filtered
        ),
        encoding="utf-8",
    )
    data = {
        "date": run_date,
        "retriever": result.retriever,
        "embedding_model": embedding_model,
        "k": result.k,
        "summary": summary_data(result.summary),
        "by_category": {n: summary_data(s) for n, s in result.by_category.items()},
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
        "latency_ms": [round(ms, 1) for ms in result.latencies_ms],
    }
    files.data_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return files


def _filter_row(label: str, summary: Summary) -> str:
    return f"| {label} | {summary.recall[5]:.3f} | {summary.precision[5]:.3f} | {summary.mrr:.3f} |"


def summary_data(summary: Summary) -> dict:
    return {
        "n": summary.n,
        "mrr": round(summary.mrr, 4),
        "recall": {str(k): round(v, 4) for k, v in summary.recall.items()},
        "precision": {str(k): round(v, 4) for k, v in summary.precision.items()},
        "hit": {str(k): round(v, 4) for k, v in summary.hit.items()},
    }


async def _run(args: argparse.Namespace) -> BenchmarkFiles:
    settings = Settings()
    embedder: Embedder = (
        FakeEmbedder(settings.embedding_dimensions) if args.fake else build_embedder(settings)
    )
    documents = load_corpus(args.corpus)
    questions = load_questions(args.questions)
    engine = create_engine(settings)
    try:
        retriever = PgVectorRetriever(create_session_factory(engine), embedder)
        result = await run_benchmark(retriever, documents, questions)
        filtered = await run_benchmark(retriever, documents, questions, use_service_filter=True)
    finally:
        await engine.dispose()
    files = write_benchmark(
        result,
        args.out_dir,
        embedding_model=embedder.model,
        run_date=args.date,
        filtered=filtered,
    )
    print("\n".join(_metrics_table(result.summary)))
    print(f"MRR@{result.k}: {result.summary.mrr:.3f}; {len(result.misses)} misses")
    print(f"Wrote {files.report_path} and {files.data_path}")
    return files


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Benchmark dense retrieval (AIENG-28).")
    parser.add_argument("--corpus", type=Path, default=Path("data/corpus"))
    parser.add_argument("--questions", type=Path, default=Path("evals/retrieval/questions.jsonl"))
    parser.add_argument("--out-dir", type=Path, default=Path("evals/retrieval/runs"))
    parser.add_argument("--date", default=datetime.now(UTC).date().isoformat())
    parser.add_argument(
        "--fake", action="store_true", help="offline hashed embeddings (wiring check)"
    )
    asyncio.run(_run(parser.parse_args(argv)))


if __name__ == "__main__":
    main()
