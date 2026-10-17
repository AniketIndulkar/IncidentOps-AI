"""Numbering the retrieved chunks for the model, and checking what it cites.

The model never sees chunk ids, only `E1`..`En` that we assign. Everything shown to the user is
rebuilt from our own chunk data: a citation is accepted only if the quote really occurs in the cited
chunk, and its text and position come from the source, never from the model's words.
"""

import re
import secrets
from collections.abc import Sequence
from dataclasses import dataclass

from incidentops_ai.diagnosis.models import (
    Cause,
    Citation,
    Diagnosis,
    EvidenceItem,
    ModelCitation,
    ModelDiagnosis,
    NoAnswerReason,
)
from incidentops_ai.retrieval.chunking import Chunk
from incidentops_ai.retrieval.retriever import RetrievedChunk

# A quote shorter than this ("the pool") can sit inside almost any text and supports nothing.
MIN_QUOTE_CHARS = 12


@dataclass(frozen=True, slots=True)
class LineRegion:
    """Where a line of an attachment's text sits: offsets in the chunk text, page, pixel box."""

    start: int
    end: int
    page: int
    box: tuple[int, int, int, int] | None


@dataclass(frozen=True, slots=True)
class Evidence:
    id: str
    chunk: Chunk
    regions: tuple[LineRegion, ...] = ()  # attachments: one per extracted line (W06)
    extraction: str | None = None  # attachments: pdf-text | ocr | vlm-transcript


def _region(item: Evidence, start: int, end: int) -> tuple[int, int, int, int] | None:
    """The pixel box around the lines a quote spans, when the extractor recorded boxes."""
    boxes = [r.box for r in item.regions if r.box and r.start < end and r.end > start]
    if not boxes:
        return None
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def number_evidence(results: Sequence[RetrievedChunk]) -> list[Evidence]:
    return [Evidence(f"E{i}", result.chunk) for i, result in enumerate(results, start=1)]


def render_evidence(evidence: Sequence[Evidence], *, nonce: str | None = None) -> str:
    """The prompt text for the evidence. Each excerpt sits between lines carrying a random nonce
    that text written into a document cannot know, so it cannot fake the end of its own block."""
    nonce = nonce or secrets.token_hex(4)
    blocks = []
    for item in evidence:
        chunk = item.chunk
        section = f" > {chunk.section}" if chunk.section else ""
        blocks.append(
            f"[{item.id}] {chunk.title}{section} (page {chunk.page}, {chunk.doc_type.value})\n"
            f"-----BEGIN EXCERPT {nonce}-----\n{chunk.text}\n-----END EXCERPT {nonce}-----"
        )
    return "\n\n".join(blocks)


def evidence_item(item: Evidence) -> EvidenceItem:
    chunk = item.chunk
    return EvidenceItem(
        evidence_id=item.id,
        doc_id=chunk.doc_id,
        title=chunk.title,
        source_path=chunk.source_path,
        section=chunk.section,
        page=chunk.page,
        page_end=chunk.page_end,
        char_start=chunk.start,
        char_end=chunk.end,
        extraction=item.extraction,
    )


def _check(citation: ModelCitation, by_id: dict[str, Evidence]) -> Citation | None:
    item = by_id.get(citation.evidence_id.strip())
    words = citation.quote.split()
    if item is None or len(" ".join(words)) < MIN_QUOTE_CHARS:
        return None
    # Words match exactly (case included); runs of spaces and line breaks may differ.
    found = re.search(r"\s+".join(re.escape(word) for word in words), item.chunk.text)
    if found is None:
        return None
    chunk = item.chunk
    return Citation(
        evidence_id=item.id,
        doc_id=chunk.doc_id,
        title=chunk.title,
        source_path=chunk.source_path,
        section=chunk.section,
        page=chunk.page,
        page_end=chunk.page_end,
        quote=found.group(),
        quote_start=chunk.start + found.start(),
        quote_end=chunk.start + found.end(),
        region=_region(item, found.start(), found.end()),
        extraction=item.extraction,
    )


def no_answer(
    reason: NoAnswerReason, evidence: Sequence[Evidence], *, dropped: int = 0
) -> Diagnosis:
    return Diagnosis(
        status="no_answer",
        no_answer_reason=reason,
        causes=[],
        dropped_causes=dropped,
        evidence=[evidence_item(item) for item in evidence],
    )


def verify(model: ModelDiagnosis, evidence: Sequence[Evidence]) -> Diagnosis:
    """What the user is shown: only causes with at least one citation that checks out."""
    if model.answer_status == "insufficient_evidence":
        return no_answer("insufficient_evidence", evidence, dropped=len(model.causes))

    by_id = {item.id: item for item in evidence}
    causes: list[Cause] = []
    dropped = 0
    for proposed in model.causes:
        checked: list[Citation] = []
        for citation in (_check(c, by_id) for c in proposed.citations):
            seen = {(c.evidence_id, c.quote_start) for c in checked}
            if citation and (citation.evidence_id, citation.quote_start) not in seen:
                checked.append(citation)
        if checked and proposed.claim.strip():
            causes.append(Cause(claim=proposed.claim.strip(), citations=checked))
        else:
            dropped += 1
    if not causes:
        return no_answer("no_supported_cause", evidence, dropped=dropped)
    return Diagnosis(
        status="answered",
        no_answer_reason=None,
        causes=causes,
        dropped_causes=dropped,
        evidence=[evidence_item(item) for item in evidence],
    )
