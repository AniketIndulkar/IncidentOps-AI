"""AIENG-34: the graph-sensitive question set, committed before any graph-retrieval code."""

from collections import Counter
from pathlib import Path

from incidentops_ai.retrieval.documents import load_corpus
from incidentops_ai.retrieval.evalset import load_questions, resolve_question

ROOT = Path(__file__).resolve().parent.parent
DOCUMENTS = {d.id: d for d in load_corpus(ROOT / "data" / "corpus")}
QUESTIONS = load_questions(ROOT / "evals" / "graph" / "questions.jsonl")


def test_every_graph_question_points_at_one_exact_quote_in_the_corpus():
    for question in QUESTIONS:
        [span] = resolve_question(question, DOCUMENTS)
        assert span.end > span.start, question.id


def test_the_set_has_the_categories_written_in_the_design():
    assert Counter(q.category for q in QUESTIONS) == {
        "dependency": 5,
        "dependent": 2,
        "history": 3,
        "same-service": 2,
    }


def test_every_question_carries_an_anchor_service():
    assert all(q.service for q in QUESTIONS)
