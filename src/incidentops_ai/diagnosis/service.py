"""Diagnose an incident from retrieved evidence, with citations the server has checked."""

import time
from collections.abc import Sequence
from dataclasses import dataclass

from incidentops_ai.diagnosis.evidence import (
    Evidence,
    no_answer,
    number_evidence,
    render_evidence,
    verify,
)
from incidentops_ai.diagnosis.models import Diagnosis, ModelDiagnosis
from incidentops_ai.llm.prompts import load_prompt
from incidentops_ai.llm.provider import LLMProvider, LLMStatus, ModelConfig, RunMetadata
from incidentops_ai.retrieval.retriever import QueryHints, RetrievalFilter, Retriever

DIAGNOSE_PROMPT = "diagnose_incident"
DIAGNOSE_PROMPT_VERSION = "v3"  # v2 (W06): attachments A1..An; v3 (W08): memory notes M1..Mn
EVIDENCE_COUNT = 5  # the top five: the same K the retrieval benchmark calls a hit


@dataclass(frozen=True)
class DiagnosisResult:
    status: LLMStatus
    diagnosis: Diagnosis | None  # None unless the status is SUCCEEDED
    metadata: RunMetadata
    error_detail: str | None
    retry_after_s: float | None
    stack: str  # name of the retriever that supplied the evidence
    retrieval_ms: float


def _model_input(query: str, evidence_text: str) -> str:
    return f"Incident report:\n{query}\n\nEvidence (cite by id):\n\n{evidence_text}"


async def diagnose_from_evidence(
    provider: LLMProvider,
    *,
    query: str,
    evidence: Sequence[Evidence],
    config: ModelConfig,
    stack: str = "given",
    retrieval_ms: float = 0.0,
) -> DiagnosisResult:
    """Ask the model about evidence that was already gathered, then check its citations. With no
    evidence the model is not called. Never raises for LLM failures: check `status`. (W07: the
    workflow retrieves in its own step and calls this.)"""
    if not evidence:
        return DiagnosisResult(
            status=LLMStatus.SUCCEEDED,
            diagnosis=no_answer("no_evidence", evidence),
            metadata=RunMetadata(
                provider="none",
                model="none",
                prompt_name=DIAGNOSE_PROMPT,
                prompt_version=DIAGNOSE_PROMPT_VERSION,
                attempts=0,
                latency_ms=round(retrieval_ms),
            ),
            error_detail=None,
            retry_after_s=None,
            stack=stack,
            retrieval_ms=retrieval_ms,
        )

    prompt = load_prompt(
        DIAGNOSE_PROMPT,
        DIAGNOSE_PROMPT_VERSION,
        input_text=_model_input(query, render_evidence(evidence)),
    )
    reply = await provider.generate_structured(prompt=prompt, schema=ModelDiagnosis, config=config)
    succeeded = reply.status is LLMStatus.SUCCEEDED and reply.value is not None
    return DiagnosisResult(
        status=reply.status,
        diagnosis=verify(reply.value, evidence) if succeeded and reply.value else None,
        metadata=reply.metadata,
        error_detail=reply.error_detail,
        retry_after_s=reply.retry_after_s,
        stack=stack,
        retrieval_ms=retrieval_ms,
    )


async def diagnose(
    provider: LLMProvider,
    retriever: Retriever,
    *,
    query: str,
    tenant_id: str,
    config: ModelConfig,
    k: int = EVIDENCE_COUNT,
    hints: QueryHints | None = None,
    attachments: Sequence[Evidence] = (),
) -> DiagnosisResult:
    """Retrieve, ask the model, check its citations. Never raises for LLM failures: check
    `status`. With nothing retrieved the model is not called at all."""
    started = time.perf_counter()
    results = await retriever.retrieve(
        query, k=k, filters=RetrievalFilter(tenant_id=tenant_id), hints=hints
    )
    retrieval_ms = (time.perf_counter() - started) * 1000
    # The incident's own files (W06) come after the retrieved excerpts, already numbered A1..An.
    return await diagnose_from_evidence(
        provider,
        query=query,
        evidence=[*number_evidence(results), *attachments],
        config=config,
        stack=retriever.name,
        retrieval_ms=retrieval_ms,
    )
