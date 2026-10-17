"""AIENG-65: the generation metrics and the judge calibration are computed correctly."""

import pytest

from incidentops_ai.evals.generation import GoldenCase, aggregate, calibrate, case_metrics, kappa


def case(expect="answer", relevant=("rb-a",)):
    return GoldenCase(
        id="c",
        kind="answerable",
        title="t",
        description="d",
        service="s",
        severity="SEV2",
        expect=expect,
        relevant_docs=list(relevant),
    )


def record(status="answered", retrieved=("rb-a", "rb-b"), cited=("rb-a",)):
    causes = [{"claim": "x", "citations": [{"doc_id": d, "quote": "q"} for d in cited]}]
    return {"status": status, "retrieved_docs": list(retrieved), "causes": causes}


def test_metrics_for_an_answered_case():
    m = case_metrics(case(), record(cited=("rb-a", "rb-b")))

    assert m == {
        "correct_answering": True,
        "context_recall": True,
        "context_precision": 0.5,
        "citation_precision": 0.5,
    }


def test_an_off_corpus_case_is_correct_only_when_it_declines():
    off = case(expect="no_answer", relevant=())

    assert case_metrics(off, record(status="no_answer", cited=()))["correct_answering"]
    assert not case_metrics(off, record())["correct_answering"]
    assert case_metrics(off, record())["context_recall"] is None  # nothing relevant to recall


def test_aggregate_averages_what_applies():
    cases = [case(), case(expect="no_answer", relevant=())]
    cases[1] = cases[1].model_copy(update={"id": "d"})
    records = {"c": record(), "d": record(status="no_answer", cited=())}

    totals = aggregate(cases, records)

    assert totals["answering_accuracy"] == 1.0 and totals["context_recall"] == 1.0


@pytest.mark.parametrize(
    ("pairs", "expected"),
    [
        ([(True, True), (False, False)], 1.0),
        ([(True, False), (False, True)], -1.0),
        ([(True, True), (True, True), (True, False), (False, False)], 0.5),
    ],
)
def test_kappa(pairs, expected):
    assert kappa(pairs) == expected


def test_calibration_lists_every_disagreement():
    verdicts = {
        "a#0": {"supported": True, "relevant": True},
        "b#0": {"supported": True, "relevant": False},
    }
    labels = {
        "a#0": {"supported": True, "relevant": True},
        "b#0": {"supported": False, "relevant": False, "note": "adds a number"},
    }

    report = calibrate(verdicts, labels)

    assert report["supported"]["agreement"] == 0.5
    assert report["supported"]["disagreements"] == [
        {"cause": "b#0", "judge": True, "label": False, "label_note": "adds a number"}
    ]
    assert report["relevant"]["agreement"] == 1.0
