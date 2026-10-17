"""AIENG-40/41/43: scoring, the per-kind decision and the router, fixed in docs/roadmap-w06.md."""

from pathlib import Path

import pytest

from incidentops_ai.multimodal.benchmark import (
    ScriptedModel,
    answer_correct,
    load_cases,
    pick_path,
    render_report,
    route,
    run_benchmark,
)
from incidentops_ai.multimodal.extract import Extraction, TextLine

ROOT = Path(__file__).resolve().parent.parent
CASES = load_cases(ROOT / "evals" / "multimodal" / "cases.jsonl")


@pytest.mark.parametrize(
    ("answer", "expected", "correct"),
    [
        ("The eu-west-1 region spikes.", ["eu-west-1"], True),
        ("us-east-1", ["eu-west-1"], False),
        ("11 restarts", ["11"], True),
        ("111", ["11"], False),
        ("about nine seconds", ["9|nine"], True),
        ("0.9 s", ["9|nine"], False),
        ("nginx 1.25.3", ["nginx", "1.25.3"], True),
        ("nginx", ["nginx", "1.25.3"], False),
        ("idle in transaction", ["idle in transaction"], True),
        ("IdleInTransaction", ["idle in transaction"], True),
        ("unknown", ["unknown"], False),
    ],
)
def test_an_answer_is_correct_when_it_holds_every_expected_term(answer, expected, correct):
    assert answer_correct(answer, expected) is correct


def test_the_cases_load_with_their_files():
    assert len(CASES) == 14 and sum(len(c.questions) for c in CASES) == 28
    assert all(c.path.is_file() for c in CASES)


def test_a_costlier_path_must_win_by_a_whole_question():
    assert pick_path({"ocr": 3, "vlm-transcript": 4}) == "vlm-transcript"
    assert pick_path({"ocr": 4, "vlm-transcript": 4}) == "ocr"
    assert pick_path({"pdf-text": 4, "ocr": 4, "vlm-transcript": 4}) == "pdf-text"
    assert pick_path({"pdf-text": 0, "ocr": 3, "vlm-transcript": 3}) == "ocr"
    assert pick_path({"pdf-text": 0, "ocr": 1, "vlm-transcript": 3}) == "vlm-transcript"


def extraction(method, *, chars=(), coverage=()):
    return Extraction(method, (TextLine("x", 1, None),), 1, chars, coverage)


def test_the_router_follows_the_rule_fixed_in_advance():
    pdf_with_text = extraction("pdf-text", chars=(640,))
    no_text = extraction("pdf-text", chars=(0,))

    assert route("application/pdf", pdf=pdf_with_text, ocr=None, vlm_available=True) == "pdf-text"
    assert (
        route(
            "application/pdf",
            pdf=no_text,
            ocr=extraction("ocr", coverage=(0.2,)),
            vlm_available=True,
        )
        == "ocr"
    )
    assert (
        route("image/png", pdf=None, ocr=extraction("ocr", coverage=(0.03,)), vlm_available=True)
        == "vlm-transcript"
    )
    assert (
        route("image/png", pdf=None, ocr=extraction("ocr", coverage=(0.03,)), vlm_available=False)
        == "ocr"
    )


class StaticOcr:
    method = "ocr"

    async def extract(self, data):
        return Extraction(
            "ocr", (TextLine("eu-west-1 9.1 s", 1, (0, 0, 10, 10)),), 1, coverage=(0.2,)
        )


async def test_a_run_scores_every_question_on_every_path_and_summarises_by_kind():
    model = ScriptedModel(answer="eu-west-1", transcript=["eu-west-1 spikes to 9 s"])
    cases = [c for c in CASES if c.id in {"s04", "c01"}]

    result = await run_benchmark(cases, ocr=StaticOcr(), model=model, config_model="fake-model")

    rows = {(r.question_id, r.path) for r in result.questions}
    assert ("s04-q1", "ocr") in rows and ("c01-q1", "vlm-direct") in rows
    assert ("s04-q1", "pdf-text") not in rows  # text-layer extraction only applies to PDFs
    s04_q1 = next(r for r in result.questions if (r.question_id, r.path) == ("s04-q1", "ocr"))
    assert s04_q1.correct and s04_q1.in_text
    assert result.summary["screenshot"]["ocr"].correct == 1
    assert result.routes["s04"] == "ocr" and result.routes["c01"] == "ocr"


async def test_the_report_has_the_decision_the_routes_and_every_answer():
    model = ScriptedModel(answer="unknown", transcript=[])
    result = await run_benchmark(
        [c for c in CASES if c.id == "p01"], ocr=StaticOcr(), model=model, config_model="m"
    )

    report = render_report(result, run_date="d")

    for expected in ("## By kind", "## Decision", "## Router", "## Every answer", "p01-q1"):
        assert expected in report
    assert "assumed" in report  # cost is an estimate at an assumed price, and says so
