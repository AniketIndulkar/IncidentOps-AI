import json
from collections import Counter
from pathlib import Path

import pytest

from incidentops_ai.retrieval.documents import Document, load_corpus, parse_document
from incidentops_ai.retrieval.evalset import (
    EvalSetError,
    Evidence,
    Question,
    load_questions,
    resolve_question,
)

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "data" / "corpus"
QUESTIONS = ROOT / "evals" / "retrieval" / "questions.jsonl"


def doc(doc_id: str, body: str, service: str = "demo-api") -> Document:
    raw = f"---\nid: {doc_id}\ntype: runbook\ntitle: T\nservice: {service}\n---\n{body}"
    return parse_document(raw, source_path=f"{doc_id}.md")


def question(*evidence: Evidence, qid: str = "q1") -> Question:
    return Question(id=qid, category="lookup", text="?", service=None, evidence=evidence)


DOCS = {"a": doc("a", "Restart the pod\nand wait for it.\n\nSecond paragraph, unique.\n")}


def test_a_quote_resolves_to_its_character_span():
    spans = resolve_question(question(Evidence("a", "Second paragraph, unique.")), DOCS)

    assert [(s.doc_id, DOCS["a"].text[s.start : s.end]) for s in spans] == [
        ("a", "Second paragraph, unique.")
    ]


def test_matching_ignores_line_wrapping_and_runs_of_spaces():
    spans = resolve_question(question(Evidence("a", "Restart  the pod and wait")), DOCS)

    assert DOCS["a"].text[spans[0].start : spans[0].end] == "Restart the pod\nand wait"


def test_a_quote_that_is_not_in_the_document_is_an_error():
    with pytest.raises(EvalSetError, match="q1.*not found"):
        resolve_question(question(Evidence("a", "never written")), DOCS)


def test_a_quote_that_appears_twice_is_an_error():
    docs = {"a": doc("a", "same words here. Then same words here.")}

    with pytest.raises(EvalSetError, match="q1.*2 times"):
        resolve_question(question(Evidence("a", "same words here")), docs)


def test_an_unknown_document_is_an_error():
    with pytest.raises(EvalSetError, match="q1.*unknown document 'zzz'"):
        resolve_question(question(Evidence("zzz", "x")), DOCS)


def test_load_questions_reads_jsonl(tmp_path: Path):
    path = tmp_path / "q.jsonl"
    path.write_text(
        json.dumps(
            {
                "id": "q1",
                "category": "lookup",
                "question": "What?",
                "service": "demo-api",
                "relevant": [{"doc": "a", "evidence": "Restart"}],
            }
        )
        + "\n"
    )

    [loaded] = load_questions(path)

    assert loaded == Question("q1", "lookup", "What?", "demo-api", (Evidence("a", "Restart"),))


@pytest.mark.parametrize(
    "mutate,message",
    [
        (lambda d: d.pop("question"), "question"),
        (lambda d: d.update(relevant=[]), "at least one"),
        (lambda d: d.update(relevant=[{"doc": "a"}]), "evidence"),
        (lambda d: d.update(category=""), "category"),
    ],
)
def test_malformed_questions_are_rejected(tmp_path: Path, mutate, message):
    record = {
        "id": "q1",
        "category": "lookup",
        "question": "What?",
        "service": None,
        "relevant": [{"doc": "a", "evidence": "Restart"}],
    }
    mutate(record)
    path = tmp_path / "q.jsonl"
    path.write_text(json.dumps(record) + "\n")

    with pytest.raises(EvalSetError, match=message):
        load_questions(path)


def test_duplicate_question_ids_are_rejected(tmp_path: Path):
    record = json.dumps(
        {"id": "q1", "category": "c", "question": "?", "relevant": [{"doc": "a", "evidence": "x"}]}
    )
    path = tmp_path / "q.jsonl"
    path.write_text(f"{record}\n{record}\n")

    with pytest.raises(EvalSetError, match="duplicate.*q1"):
        load_questions(path)


# --- the committed eval set -----------------------------------------------------------------


@pytest.fixture(scope="module")
def corpus() -> dict[str, Document]:
    return {d.id: d for d in load_corpus(CORPUS)}


@pytest.fixture(scope="module")
def questions() -> list[Question]:
    return load_questions(QUESTIONS)


def test_the_eval_set_has_thirty_questions(questions: list[Question]):
    assert [q.id for q in questions] == [f"q{i:02d}" for i in range(1, 31)]


def test_every_quote_in_the_eval_set_resolves_to_exactly_one_place(
    questions: list[Question], corpus: dict[str, Document]
):
    for q in questions:
        spans = resolve_question(q, corpus)
        assert len(spans) == len(q.evidence)
        assert all(s.end - s.start >= 20 for s in spans), q.id  # a quote, not a stray word


def test_the_eval_set_covers_every_question_style(questions: list[Question]):
    counts = Counter(q.category for q in questions)

    assert set(counts) == {"lookup", "identifier", "paraphrase", "procedure", "multi"}
    assert min(counts.values()) >= 4


def test_service_hints_name_a_service_of_the_evidence(
    questions: list[Question], corpus: dict[str, Document]
):
    for q in questions:
        if q.service is not None:
            services = {corpus[e.doc_id].service for e in q.evidence}
            assert q.service in services, q.id


def test_the_corpus_has_runbooks_and_postmortems_and_unique_documents(corpus: dict[str, Document]):
    types = Counter(d.type.value for d in corpus.values())

    assert types == {"runbook": 11, "postmortem": 7}
    assert len({d.source_path for d in corpus.values()}) == 18
