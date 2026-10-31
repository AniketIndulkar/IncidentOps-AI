"""Run any Retriever over the eval set: the one scoring loop used by experiments and benchmarks."""

import time
from collections.abc import Sequence
from dataclasses import dataclass

from incidentops_ai.retrieval.documents import Document
from incidentops_ai.retrieval.evalset import Question, resolve_question
from incidentops_ai.retrieval.metrics import DEFAULT_KS, QuestionScore, score_question
from incidentops_ai.retrieval.retriever import RetrievalFilter, RetrievedChunk, Retriever


@dataclass(frozen=True)
class Evaluation:
    """Raw results of one evaluation run; `metrics.summarize` turns `scores` into averages."""

    scores: list[QuestionScore]  # one per question, in eval-set order
    rankings: dict[str, list[RetrievedChunk]]  # question id -> top k
    latencies_ms: list[float]  # per question, in eval-set order: query embedding plus search


async def evaluate_retriever(
    retriever: Retriever,
    documents: Sequence[Document],
    questions: Sequence[Question],
    *,
    k: int,
    use_service_filter: bool = False,
) -> Evaluation:
    """Ask `retriever` every question, time each call, and score the results.

    Args:
        retriever: any `Retriever`; this loop is the same for all of them.
        documents: the corpus, needed to locate each question's evidence quotes.
        questions: the eval set.
        k: how many results to request per question; metrics are reported at every
            `DEFAULT_KS` cut-off up to `k`.
        use_service_filter: restrict each question to its `service`, when it names one.
    """
    if not documents or not questions:
        raise ValueError("an evaluation needs documents and questions")
    tenant_id = documents[0].tenant_id  # the eval corpus belongs to a single tenant
    by_id = {document.id: document for document in documents}
    # Cut-offs that fit within k, e.g. k=5 -> (1, 3, 5). `(k,)` only applies if k is below
    # every default cut-off.
    ks = tuple(x for x in DEFAULT_KS if x <= k) or (k,)

    scores: list[QuestionScore] = []
    rankings: dict[str, list[RetrievedChunk]] = {}
    latencies: list[float] = []
    for question in questions:
        services = (question.service,) if use_service_filter and question.service else None
        filters = RetrievalFilter(tenant_id=tenant_id, services=services)
        # perf_counter is a monotonic high-resolution clock, meant for measuring durations.
        started = time.perf_counter()
        ranked = await retriever.retrieve(question.text, k=k, filters=filters)
        latencies.append((time.perf_counter() - started) * 1000)
        rankings[question.id] = ranked
        gold = resolve_question(question, by_id)
        scores.append(score_question(question.id, question.category, ranked, gold, ks))
    return Evaluation(scores, rankings, latencies)
