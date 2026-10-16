"""AIENG-26: compare chunking configurations on the eval set and pick one by a fixed rule.

    uv run python -m incidentops_ai.retrieval.chunking_experiment

Writes evals/retrieval/runs/<date>-chunking.md (the tables) and .json (every question's result).
The configurations and the selection rule were written down before the first run
(docs/roadmap-w03.md); this module only applies them.
"""

import argparse
import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from incidentops_ai.config import Settings
from incidentops_ai.retrieval.chunking import chunker_from_name
from incidentops_ai.retrieval.documents import Document, load_corpus
from incidentops_ai.retrieval.embeddings import CachingEmbedder, Embedder, FakeEmbedder
from incidentops_ai.retrieval.evalset import Question, load_questions
from incidentops_ai.retrieval.experiment import (
    ConfigResult,
    ExperimentConfig,
    pick_winner,
    render_comparison,
    run_config,
)
from incidentops_ai.retrieval.factory import build_embedder

PRIMARY_CONFIGS = (
    "fixed-500-50",
    "fixed-1000-100",
    "fixed-2000-200",
    "paragraph-1000",
    "section-1500",
)
TOP_K = 10


@dataclass(frozen=True)
class ChunkingExperiment:
    primary: list[ConfigResult]
    winner: ConfigResult
    with_context: list[ConfigResult]
    winner_filtered: ConfigResult
    report_path: Path
    data_path: Path


async def run_chunking_experiment(
    documents: Sequence[Document],
    questions: Sequence[Question],
    embedder: Embedder,
    *,
    out_dir: Path,
    run_date: str,
) -> ChunkingExperiment:
    async def run(name: str, *, with_context: bool = False, use_service_filter: bool = False):
        config = ExperimentConfig(chunker_from_name(name), with_context=with_context)
        return await run_config(
            config, documents, questions, embedder, k=TOP_K, use_service_filter=use_service_filter
        )

    primary = [await run(name) for name in PRIMARY_CONFIGS]
    winner = pick_winner(primary)
    with_context = [await run(name, with_context=True) for name in PRIMARY_CONFIGS]
    filtered = await run(winner.name, use_service_filter=True)
    winner_filtered = replace(filtered, name=f"{winner.name} (service filter)")

    out_dir.mkdir(parents=True, exist_ok=True)
    experiment = ChunkingExperiment(
        primary=primary,
        winner=winner,
        with_context=with_context,
        winner_filtered=winner_filtered,
        report_path=out_dir / f"{run_date}-chunking.md",
        data_path=out_dir / f"{run_date}-chunking.json",
    )
    experiment.report_path.write_text(
        _render_report(experiment, documents, questions, embedder, run_date), encoding="utf-8"
    )
    experiment.data_path.write_text(_render_data(experiment, embedder, run_date), encoding="utf-8")
    return experiment


def _render_report(
    experiment: ChunkingExperiment,
    documents: Sequence[Document],
    questions: Sequence[Question],
    embedder: Embedder,
    run_date: str,
) -> str:
    spans = sum(len(q.evidence) for q in questions)
    categories = list(experiment.primary[0].by_category)
    style_rows = [
        "| Style | n | " + " | ".join(r.name for r in experiment.primary) + " |",
        "|" + "---|" * (len(experiment.primary) + 2),
    ]
    for category in categories:
        n = experiment.primary[0].by_category[category].n
        cells = [f"{r.by_category[category].mrr:.3f}" for r in experiment.primary]
        style_rows.append(f"| {category} | {n} | " + " | ".join(cells) + " |")

    unfiltered = replace(experiment.winner, name=f"{experiment.winner.name} (no filter)")
    return "\n".join(
        [
            f"# Chunking experiment, {run_date}",
            "",
            f"- Embedding model: `{embedder.model}` ({embedder.dimensions} dimensions)",
            f"- Corpus: {len(documents)} documents, {sum(len(d.text) for d in documents)} characters",
            (
                f"- Questions: {len(questions)} ({spans} evidence spans); a chunk is relevant "
                "when it holds at least half of a span"
            ),
            f"- Retrieval: dense, cosine, exact scan, top {TOP_K}, no filters unless stated",
            "- Reproduce: `uv run python -m incidentops_ai.retrieval.chunking_experiment`",
            "",
            "## Primary configurations",
            "",
            render_comparison(experiment.primary),
            (
                f"Selected: **{experiment.winner.name}**, by the rule fixed in "
                "`docs/roadmap-w03.md` (highest MRR; configs within 0.02 of it are ordered by "
                "Recall@5, then by smaller chunks)."
            ),
            "",
            "## MRR by question style",
            "",
            "\n".join(style_rows),
            "",
            "## Context header (exploratory)",
            "",
            "The same chunks, but each is embedded together with `document title > section`.",
            "",
            render_comparison([*experiment.primary, *experiment.with_context]),
            "## Service filter (exploratory)",
            "",
            (
                "The selected config, with and without restricting the search to the service "
                "named in the question (what extraction would supply). Questions with no "
                "service are unrestricted in both rows."
            ),
            "",
            render_comparison([unfiltered, experiment.winner_filtered]),
        ]
    )


def _render_data(experiment: ChunkingExperiment, embedder: Embedder, run_date: str) -> str:
    def entry(result: ConfigResult) -> dict:
        return {
            "chunks": result.chunk_count,
            "mean_chunk_chars": round(result.mean_chunk_chars, 1),
            "mrr": round(result.summary.mrr, 4),
            "recall": {str(k): round(v, 4) for k, v in result.summary.recall.items()},
            "precision": {str(k): round(v, 4) for k, v in result.summary.precision.items()},
            "questions": {
                s.question_id: {
                    "reciprocal_rank": round(s.reciprocal_rank, 4),
                    "first_relevant_rank": s.first_relevant_rank,
                    "recall@5": round(s.recall[5], 4),
                }
                for s in result.scores
            },
        }

    everything = [
        *experiment.primary,
        *experiment.with_context,
        experiment.winner_filtered,
    ]
    data = {
        "date": run_date,
        "embedding_model": embedder.model,
        "dimensions": embedder.dimensions,
        "winner": experiment.winner.name,
        "configs": {r.name: entry(r) for r in everything},
    }
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Compare chunking configurations (AIENG-26).")
    parser.add_argument("--corpus", type=Path, default=Path("data/corpus"))
    parser.add_argument("--questions", type=Path, default=Path("evals/retrieval/questions.jsonl"))
    parser.add_argument("--out-dir", type=Path, default=Path("evals/retrieval/runs"))
    parser.add_argument("--cache-dir", type=Path, default=Path("evals/retrieval/.cache"))
    parser.add_argument("--date", default=datetime.now(UTC).date().isoformat())
    parser.add_argument("--model", help="embedding model (default: INCIDENTOPS_EMBEDDING_MODEL)")
    parser.add_argument(
        "--fake", action="store_true", help="offline hashed embeddings, no API calls"
    )
    args = parser.parse_args(argv)

    settings = Settings()
    if args.model:
        settings = settings.model_copy(update={"embedding_model": args.model})
    embedder: Embedder = (
        FakeEmbedder(settings.embedding_dimensions)
        if args.fake
        else CachingEmbedder(build_embedder(settings), args.cache_dir)
    )
    experiment = asyncio.run(
        run_chunking_experiment(
            load_corpus(args.corpus),
            load_questions(args.questions),
            embedder,
            out_dir=args.out_dir,
            run_date=args.date,
        )
    )
    print(render_comparison(experiment.primary))
    print(f"Selected: {experiment.winner.name}")
    print(f"Wrote {experiment.report_path} and {experiment.data_path}")


if __name__ == "__main__":
    main()
