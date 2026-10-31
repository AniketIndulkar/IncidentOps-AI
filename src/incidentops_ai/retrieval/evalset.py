"""The retrieval eval set: questions whose ground truth is an exact quote from a document.

Ground truth is a quote, not a chunk id, so the same questions judge every chunking, every
embedding model and every retriever. A quote is turned into a character span of the cleaned
document text; line wrapping and runs of spaces in the quote do not matter.

The file is JSON Lines (one question per line), for example:

    {"id": "q01", "category": "lookup", "question": "How many ...?", "service": "orders-api",
     "relevant": [{"doc": "rb-orders-db-pool", "evidence": "orders-api opens at most 15 ..."}]}
"""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from incidentops_ai.retrieval.documents import Document


class EvalSetError(ValueError):
    """The eval set is malformed or no longer matches the corpus."""


@dataclass(frozen=True, slots=True)
class Evidence:
    """A passage a correct retrieval must find, as written in the eval file."""

    doc_id: str  # document the quote comes from
    quote: str  # exact text from that document (whitespace may differ)


@dataclass(frozen=True, slots=True)
class Question:
    """One eval question and the evidence needed to answer it."""

    id: str  # e.g. "q01"
    category: str  # question style (lookup, causal, ...), for per-style results
    text: str  # the question as a user would ask it
    service: str | None  # what the incident would tell us; used by the filter experiment
    evidence: tuple[Evidence, ...]  # all of it is needed for full recall


@dataclass(frozen=True, slots=True)
class GoldSpan:
    """`Evidence` located in the document: characters [start, end) of `Document.text`."""

    doc_id: str
    start: int
    end: int  # exclusive


def load_questions(path: Path) -> list[Question]:
    """Read and validate the JSON Lines eval file. Blank lines are allowed; duplicate ids fail."""
    questions: list[Question] = []
    seen: set[str] = set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        question = _parse_question(line, f"{path.name}:{number}")
        if question.id in seen:
            raise EvalSetError(f"duplicate question id {question.id!r}")
        seen.add(question.id)
        questions.append(question)
    return questions


def resolve_question(question: Question, documents: Mapping[str, Document]) -> list[GoldSpan]:
    """Gold spans of a question. Every quote must occur exactly once in its document.

    Requiring exactly one match makes a drifted label fail loudly: if a document is edited and a
    quote disappears or becomes ambiguous, the eval stops instead of scoring against nothing.
    """
    spans: list[GoldSpan] = []
    for evidence in question.evidence:
        document = documents.get(evidence.doc_id)
        if document is None:
            raise EvalSetError(f"{question.id}: unknown document {evidence.doc_id!r}")
        # Match the quote word by word with any whitespace between words, so a quote copied on one
        # line still matches text that wraps across lines in the document.
        pattern = r"\s+".join(re.escape(token) for token in evidence.quote.split())
        matches = list(re.finditer(pattern, document.text))
        if not matches:
            raise EvalSetError(
                f"{question.id}: evidence not found in {evidence.doc_id}: {evidence.quote[:60]!r}"
            )
        if len(matches) > 1:
            raise EvalSetError(
                f"{question.id}: evidence appears {len(matches)} times in {evidence.doc_id} "
                f"(must be exactly once): {evidence.quote[:60]!r}"
            )
        spans.append(GoldSpan(evidence.doc_id, matches[0].start(), matches[0].end()))
    return spans


def _parse_question(line: str, where: str) -> Question:
    """Parse one JSON line into a `Question`. `where` ("file:line") prefixes error messages."""
    try:
        record = json.loads(line)
    except json.JSONDecodeError as exc:
        raise EvalSetError(f"{where}: not valid JSON ({exc.msg})") from exc
    qid = record.get("id")
    for field in ("id", "category", "question"):
        if not isinstance(record.get(field), str) or not record[field].strip():
            raise EvalSetError(f"{where}: needs a non-empty {field!r}")
    relevant = record.get("relevant")
    if not isinstance(relevant, list) or not relevant:
        raise EvalSetError(f"{qid}: needs at least one entry in 'relevant'")
    evidence = []
    for item in relevant:
        if not isinstance(item, dict) or not item.get("doc") or not item.get("evidence"):
            raise EvalSetError(f"{qid}: each 'relevant' entry needs 'doc' and 'evidence'")
        evidence.append(Evidence(item["doc"], item["evidence"]))
    return Question(
        qid, record["category"], record["question"], record.get("service"), tuple(evidence)
    )
