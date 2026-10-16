from dataclasses import dataclass

import pytest

from incidentops_ai.retrieval.evalset import GoldSpan
from incidentops_ai.retrieval.metrics import (
    covers,
    score_question,
    summarize,
    summarize_by_category,
)


@dataclass(frozen=True)
class Hit:
    doc_id: str
    start: int
    end: int


GOLD = GoldSpan("a", 100, 200)  # 100 characters


@pytest.mark.parametrize(
    "hit,expected",
    [
        (Hit("a", 0, 1000), True),  # contains the whole span
        (Hit("a", 100, 200), True),
        (Hit("a", 150, 400), True),  # exactly half of it
        (Hit("a", 151, 400), False),  # just under half
        (Hit("a", 0, 140), False),  # 40%
        (Hit("a", 300, 400), False),  # no overlap
        (Hit("b", 0, 1000), False),  # wrong document
    ],
)
def test_a_chunk_covers_a_span_when_it_holds_at_least_half_of_it(hit, expected):
    assert covers(hit, GOLD) is expected


def test_scores_for_one_question():
    gold = [GoldSpan("a", 100, 200), GoldSpan("b", 0, 50)]
    ranked = [Hit("c", 0, 10), Hit("a", 90, 210), Hit("c", 50, 60), Hit("b", 0, 50), Hit("c", 0, 5)]

    score = score_question("q1", "multi", ranked, gold, ks=(1, 3, 5))

    assert score.recall == {1: 0.0, 3: 0.5, 5: 1.0}
    assert score.precision == {1: 0.0, 3: 1 / 3, 5: 2 / 5}
    assert score.hit == {1: 0.0, 3: 1.0, 5: 1.0}
    assert score.first_relevant_rank == 2
    assert score.reciprocal_rank == 0.5


def test_a_chunk_covering_two_spans_counts_once_for_precision():
    gold = [GoldSpan("a", 0, 40), GoldSpan("a", 50, 90)]

    score = score_question("q1", "multi", [Hit("a", 0, 100)], gold, ks=(1,))

    assert score.recall == {1: 1.0}
    assert score.precision == {1: 1.0}


def test_nothing_relevant_scores_zero_everywhere():
    score = score_question("q1", "lookup", [Hit("z", 0, 9)] * 5, [GOLD], ks=(1, 5))

    assert score.recall == {1: 0.0, 5: 0.0}
    assert score.reciprocal_rank == 0.0
    assert score.first_relevant_rank is None


def test_a_hit_beyond_the_largest_k_does_not_earn_reciprocal_rank():
    ranked = [Hit("z", 0, 9)] * 5 + [Hit("a", 100, 200)]

    score = score_question("q1", "lookup", ranked, [GOLD], ks=(1, 5))

    assert score.reciprocal_rank == 0.0
    assert score.first_relevant_rank is None


def test_fewer_results_than_k_still_divide_precision_by_k():
    score = score_question("q1", "lookup", [Hit("a", 100, 200)], [GOLD], ks=(5,))

    assert score.precision == {5: 0.2}


def test_summary_is_the_mean_over_questions():
    perfect = score_question("q1", "lookup", [Hit("a", 100, 200)], [GOLD], ks=(1, 3))
    miss = score_question("q2", "lookup", [Hit("z", 0, 1)] * 3, [GOLD], ks=(1, 3))

    summary = summarize([perfect, miss])

    assert summary.n == 2
    assert summary.recall == {1: 0.5, 3: 0.5}
    assert summary.mrr == 0.5


def test_summary_by_category_groups_questions():
    hit = score_question("q1", "lookup", [Hit("a", 100, 200)], [GOLD], ks=(1,))
    miss = score_question("q2", "identifier", [Hit("z", 0, 1)], [GOLD], ks=(1,))

    by_category = summarize_by_category([hit, miss])

    assert by_category["lookup"].mrr == 1.0
    assert by_category["identifier"].mrr == 0.0
    assert list(by_category) == ["identifier", "lookup"]  # stable, alphabetical


def test_summarising_nothing_is_an_error():
    with pytest.raises(ValueError):
        summarize([])
