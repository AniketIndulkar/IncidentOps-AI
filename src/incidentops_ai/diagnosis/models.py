"""Diagnosis types: what the model must return, and what the API shows after the server checked it.

Model-facing types follow the strict-output rules of the extraction schemas (every field required,
no extras). The response types cannot be built without a citation per cause, so "every shown cause
has provenance" is a property of the type, not only of the code that fills it.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelCitation(_Strict):
    evidence_id: str  # "E1", "E2"...: the numbers we gave the evidence
    quote: str  # copied from that evidence; checked by the server before it is shown


class ModelCause(_Strict):
    claim: str
    citations: list[ModelCitation]


class ModelDiagnosis(_Strict):
    answer_status: Literal["answered", "insufficient_evidence"]
    causes: list[ModelCause]


class EvidenceItem(BaseModel):
    """A retrieved chunk the model was shown. Every field comes from our stored chunk."""

    evidence_id: str
    doc_id: str
    title: str
    source_path: str
    section: str | None
    page: int
    page_end: int
    char_start: int  # span of the whole chunk in the document text
    char_end: int
    extraction: str | None = None  # attachments: how the text was obtained (W06)


class Citation(BaseModel):
    """A checked quote. `quote` is the source's own text, and the span says where it sits."""

    evidence_id: str
    doc_id: str
    title: str
    source_path: str
    section: str | None
    page: int
    page_end: int
    quote: str
    quote_start: int  # character offsets of the quote in the document text
    quote_end: int
    # Attachments (W06): the pixel box of the quoted lines in the page image (OCR only), and the
    # extraction that produced the text, so a reader can check it against the picture.
    region: tuple[int, int, int, int] | None = None
    extraction: str | None = None


class Cause(BaseModel):
    claim: str
    citations: list[Citation] = Field(min_length=1)


NoAnswerReason = Literal["no_evidence", "insufficient_evidence", "no_supported_cause"]


class Diagnosis(BaseModel):
    status: Literal["answered", "no_answer"]
    no_answer_reason: NoAnswerReason | None
    causes: list[Cause]
    dropped_causes: int  # causes the model offered that were not shown (no valid citation)
    evidence: list[EvidenceItem]  # what the model was shown, whether or not it was used
