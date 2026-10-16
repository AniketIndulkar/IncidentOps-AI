"""Retrieval metrics against evidence spans.

A retrieved chunk is *relevant* when it holds at least half of a gold evidence span. Because the
gold is a span of the document and not a chunk id, the same questions score any chunking.

- Recall@K: fraction of the question's gold spans covered by a chunk in the top K.
- Precision@K: relevant chunks in the top K, divided by K (also when fewer came back).
- Hit@K: 1 if any chunk in the top K is relevant.
- Reciprocal rank: 1 / rank of the first relevant chunk, 0 beyond the largest K (so MRR is MRR@K).
"""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from incidentops_ai.retrieval.evalset import GoldSpan

DEFAULT_KS = (1, 3, 5, 10)
MIN_COVERED_FRACTION = 0.5


class Located(Protocol):
    """Anything that points at a span of a document: a chunk, or a retrieval result."""

    @property
    def doc_id(self) -> str: ...
    @property
    def start(self) -> int: ...
    @property
    def end(self) -> int: ...


@dataclass(frozen=True)
class QuestionScore:
    question_id: str
    category: str
    recall: dict[int, float]
    precision: dict[int, float]
    hit: dict[int, float]
    reciprocal_rank: float
    first_relevant_rank: int | None


@dataclass(frozen=True)
class Summary:
    n: int
    recall: dict[int, float]
    precision: dict[int, float]
    hit: dict[int, float]
    mrr: float


def covers(chunk: Located, gold: GoldSpan, min_fraction: float = MIN_COVERED_FRACTION) -> bool:
    if chunk.doc_id != gold.doc_id:
        return False
    overlap = min(chunk.end, gold.end) - max(chunk.start, gold.start)
    return overlap >= min_fraction * (gold.end - gold.start)


def score_question(
    question_id: str,
    category: str,
    ranked: Sequence[Located],
    gold: Sequence[GoldSpan],
    ks: Sequence[int] = DEFAULT_KS,
) -> QuestionScore:
    deepest = max(ks)
    # relevant[i][j]: does the i-th result cover the j-th gold span?
    relevant = [[covers(chunk, span) for span in gold] for chunk in ranked[:deepest]]

    recall: dict[int, float] = {}
    precision: dict[int, float] = {}
    hit: dict[int, float] = {}
    for k in ks:
        top = relevant[:k]
        found = sum(any(row[j] for row in top) for j in range(len(gold)))
        useful = sum(any(row) for row in top)
        recall[k] = found / len(gold)
        precision[k] = useful / k
        hit[k] = 1.0 if useful else 0.0

    first = next((i + 1 for i, row in enumerate(relevant) if any(row)), None)
    return QuestionScore(
        question_id=question_id,
        category=category,
        recall=recall,
        precision=precision,
        hit=hit,
        reciprocal_rank=1 / first if first else 0.0,
        first_relevant_rank=first,
    )


def summarize(scores: Sequence[QuestionScore]) -> Summary:
    if not scores:
        raise ValueError("nothing to summarise")
    n = len(scores)
    ks = list(scores[0].recall)
    return Summary(
        n=n,
        recall={k: sum(s.recall[k] for s in scores) / n for k in ks},
        precision={k: sum(s.precision[k] for s in scores) / n for k in ks},
        hit={k: sum(s.hit[k] for s in scores) / n for k in ks},
        mrr=sum(s.reciprocal_rank for s in scores) / n,
    )


def summarize_by_category(scores: Sequence[QuestionScore]) -> dict[str, Summary]:
    groups: dict[str, list[QuestionScore]] = defaultdict(list)
    for score in scores:
        groups[score.category].append(score)
    return {category: summarize(groups[category]) for category in sorted(groups)}
