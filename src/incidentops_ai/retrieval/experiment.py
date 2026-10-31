"""Run a chunking/retrieval configuration against the eval set and report the metrics.

Each configuration is chunked and embedded into an `InMemoryDenseRetriever` (no database), so
many configurations can be compared quickly. `chunking_experiment.py` drives this module.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from incidentops_ai.retrieval.chunking import Chunker
from incidentops_ai.retrieval.documents import Document
from incidentops_ai.retrieval.embeddings import Embedder
from incidentops_ai.retrieval.evalset import Question
from incidentops_ai.retrieval.evaluate import evaluate_retriever
from incidentops_ai.retrieval.memory import InMemoryDenseRetriever
from incidentops_ai.retrieval.metrics import (
    DEFAULT_KS,
    QuestionScore,
    Summary,
    summarize,
    summarize_by_category,
)
from incidentops_ai.retrieval.retriever import RetrievedChunk


@dataclass(frozen=True)
class ExperimentConfig:
    """One setting to evaluate: how to chunk, and what to embed."""

    chunker: Chunker
    with_context: bool = False  # embed "title > section" together with the chunk text

    @property
    def name(self) -> str:
        """Label used in reports, e.g. "fixed-2000-200" or "fixed-2000-200+ctx"."""
        return f"{self.chunker.name}+ctx" if self.with_context else self.chunker.name


@dataclass(frozen=True)
class ConfigResult:
    """Everything measured for one configuration."""

    name: str  # `ExperimentConfig.name`
    chunk_count: int  # chunks produced over the whole corpus
    mean_chunk_chars: float  # average chunk length
    mean_chars_read_at_5: float  # cost: text a reader (or a prompt) takes in for the top 5
    scores: list[QuestionScore]
    rankings: dict[str, list[RetrievedChunk]]  # question id -> top-k, for inspecting misses
    summary: Summary
    by_category: dict[str, Summary]


async def run_config(
    config: ExperimentConfig,
    documents: Sequence[Document],
    questions: Sequence[Question],
    embedder: Embedder,
    *,
    k: int = max(DEFAULT_KS),
    use_service_filter: bool = False,
) -> ConfigResult:
    """Chunk the corpus with `config`, embed it, and score retrieval on every question.

    Args:
        config: the chunking and embedding setting to test.
        documents: the corpus.
        questions: the eval set.
        embedder: makes chunk and query vectors (usually a `CachingEmbedder`).
        k: results requested per question.
        use_service_filter: restrict each question to the service it names.
    """
    if not documents or not questions:
        raise ValueError("an experiment needs documents and questions")

    # All chunks of all documents, in corpus order.
    chunks = [chunk for document in documents for chunk in config.chunker.chunk(document)]
    retriever = await InMemoryDenseRetriever.build(
        chunks, embedder, with_context=config.with_context
    )
    await embedder.embed([q.text for q in questions])  # one batch, so a cache warms in one request
    evaluation = await evaluate_retriever(
        retriever, documents, questions, k=k, use_service_filter=use_service_filter
    )
    scores, rankings = evaluation.scores, evaluation.rankings

    return ConfigResult(
        name=config.name,
        chunk_count=len(chunks),
        mean_chunk_chars=sum(len(c.text) for c in chunks) / len(chunks),
        mean_chars_read_at_5=sum(
            sum(len(r.chunk.text) for r in ranked[:5]) for ranked in rankings.values()
        )
        / len(rankings),
        scores=scores,
        rankings=rankings,
        summary=summarize(scores),
        by_category=summarize_by_category(scores),
    )


def render_comparison(results: Sequence[ConfigResult]) -> str:
    """Markdown table, one row per configuration."""
    ks = list(results[0].summary.recall)
    header = [
        "Config",
        "Chunks",
        "Mean chars",
        *[f"Recall@{k}" for k in ks],
        "Precision@5",
        "MRR",
        "Chars read@5",
    ]
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for r in results:
        precision = r.summary.precision.get(5, float("nan"))  # "nan" if the run used k < 5
        cells = [
            r.name,
            str(r.chunk_count),
            f"{r.mean_chunk_chars:.0f}",
            *[f"{r.summary.recall[k]:.3f}" for k in ks],
            f"{precision:.3f}",
            f"{r.summary.mrr:.3f}",
            f"{r.mean_chars_read_at_5:.0f}",
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


# Fixed before the experiment ran (docs/roadmap-w03.md, "How the chunking config is chosen").
# MRR differences smaller than this are treated as noise on a 30-question eval set.
MRR_TIE_MARGIN = 0.02


def pick_winner(results: Sequence[ConfigResult]) -> ConfigResult:
    """Highest MRR; configs within 0.02 of it are decided by Recall@5, then by smaller chunks.

    Writing the rule down before the run keeps the choice from being made after seeing the table.
    """
    if not results:
        raise ValueError("no results to choose from")
    best = max(r.summary.mrr for r in results)
    # 1e-9 absorbs float rounding, so a config exactly 0.02 behind still counts as close.
    close = [r for r in results if r.summary.mrr >= best - MRR_TIE_MARGIN - 1e-9]
    # min over (-recall, size): highest Recall@5 first, then the smaller mean chunk.
    return min(close, key=lambda r: (-r.summary.recall[5], r.mean_chunk_chars))
