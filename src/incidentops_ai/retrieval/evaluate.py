"""Run any Retriever over the eval set: the one scoring loop used by experiments and benchmarks."""

import time
from collections.abc import Sequence
from dataclasses import dataclass

from incidentops_ai.retrieval.documents import Document
from incidentops_ai.retrieval.evalset import Question, resolve_question
from incidentops_ai.retrieval.metrics import DEFAULT_KS, QuestionScore, score_question
from incidentops_ai.retrieval.retriever import (
    QueryHints,
    RetrievalFilter,
    RetrievedChunk,
    Retriever,
)


@dataclass(frozen=True)
class Evaluation:
    scores: list[QuestionScore]
    rankings: dict[str, list[RetrievedChunk]]  # question id -> top k
    latencies_ms: list[float]  # per question, in eval-set order: query embedding plus search


async def evaluate_retriever(
    retriever: Retriever,
    documents: Sequence[Document],
    questions: Sequence[Question],
    *,
    k: int,
    use_service_filter: bool = False,
    use_service_hint: bool = False,
) -> Evaluation:
    """`use_service_filter` restricts each question to its service's documents; `use_service_hint`
    only tells the retriever the service (W05), the way an incident would."""
    if not documents or not questions:
        raise ValueError("an evaluation needs documents and questions")
    tenant_id = documents[0].tenant_id
    by_id = {document.id: document for document in documents}
    ks = tuple(x for x in DEFAULT_KS if x <= k) or (k,)

    scores: list[QuestionScore] = []
    rankings: dict[str, list[RetrievedChunk]] = {}
    latencies: list[float] = []
    for question in questions:
        services = (question.service,) if use_service_filter and question.service else None
        filters = RetrievalFilter(tenant_id=tenant_id, services=services)
        hints = (
            QueryHints(services=(question.service,))
            if use_service_hint and question.service
            else None
        )
        started = time.perf_counter()
        ranked = await retriever.retrieve(question.text, k=k, filters=filters, hints=hints)
        latencies.append((time.perf_counter() - started) * 1000)
        rankings[question.id] = ranked
        gold = resolve_question(question, by_id)
        scores.append(score_question(question.id, question.category, ranked, gold, ks))
    return Evaluation(scores, rankings, latencies)
