"""AIENG-48: the workflow benchmark's scoring, its plain-function baseline, and H24 (scripted)."""

import re

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from test_analysis_api import EXTRACTION
from test_diagnosis import cause, model_says
from test_workflow import CONFIG, GOOD, ByType, Graph, incident

from incidentops_ai.diagnosis.models import ModelDiagnosis
from incidentops_ai.extraction.models import IncidentExtraction
from incidentops_ai.graph.catalogue import load_catalogue
from incidentops_ai.graph.memory import InMemoryGraphStore
from incidentops_ai.incidents.models import IncidentCreate
from incidentops_ai.incidents.repository import IncidentRepository
from incidentops_ai.llm.fake_provider import FakeProvider
from incidentops_ai.retrieval.documents import load_corpus
from incidentops_ai.workflow.benchmark import (
    CATALOGUE,
    CORPUS,
    RunResult,
    WorkflowCase,
    cited_docs,
    corpus_bm25,
    load_cases,
    run_graph,
    run_plain,
    summarise,
    task_success,
)
from incidentops_ai.workflow.graph import WorkflowDeps

BLOCK = re.compile(
    r"\[(?P<id>[EA]\d+)\] (?P<title>.+?)(?: > [^\n]*)? \(page [^\n]*\n"
    r"-----BEGIN EXCERPT (?P<nonce>\w+)-----\n(?P<text>.*?)\n-----END EXCERPT (?P=nonce)-----",
    re.DOTALL,
)


def recommended(state: dict, docs: list[str]) -> dict:
    citation = {"doc_id": docs[0], "title": "t", "source_path": "p"}
    return {
        **state,
        "route": "recommend",
        "diagnosis": {"causes": [{"claim": "c", "citations": [citation]}]},
    }


def test_cases_file_has_twelve_cases_seven_to_recommend():
    cases = load_cases()

    assert len(cases) == 12
    assert sum(c.expected_route == "recommend" for c in cases) == 7


@pytest.mark.parametrize(
    ("route", "docs", "success"),
    [
        ("recommend", ["rb-orders-db-pool"], True),
        ("recommend", ["rb-checkout-502"], False),  # right route, wrong document
        ("review", [], False),
    ],
)
def test_a_recommendation_succeeds_only_with_an_expected_document_cited(route, docs, success):
    case = WorkflowCase.model_validate(
        {
            "id": "x",
            "title": "t",
            "description": "d",
            "service": "orders-api",
            "severity": "SEV2",
            "environment": "production",
            "attachments": [],
            "expected_route": "recommend",
            "expected_docs": ["rb-orders-db-pool", "pm-2026-05-orders-pool"],
            "note": "",
        }
    )
    state = recommended({}, docs) if route == "recommend" else {"route": "review"}

    assert task_success(case, state) is success


def test_a_review_case_succeeds_on_its_route_alone():
    case = next(c for c in load_cases() if c.id == "w10")

    assert task_success(case, {"route": "review", "diagnosis": None})
    assert not task_success(case, recommended({}, ["rb-orders-db-pool"]))
    assert cited_docs(recommended({}, ["a"])) == ["a"]


def result(case_id, mode, seconds, *, route="recommend", cited=("a",), success=True):
    return RunResult(
        case_id=case_id,
        mode=mode,
        route=route,
        reasons=[] if route == "recommend" else ["no_answer"],
        cited=list(cited),
        success=success,
        seconds=seconds,
        model_calls=2,
        input_tokens=100,
        output_tokens=10,
        steps=[],
    )


def test_the_summary_counts_success_agreement_and_the_median_saving():
    summary = summarise(
        [
            result("w1", "parallel", 2.0),
            result("w1", "sequential", 4.0),
            result("w2", "parallel", 3.0, route="review", cited=(), success=False),
            result("w2", "sequential", 5.0),
        ]
    )

    assert summary["success"] == {"parallel": 1, "sequential": 2}
    assert summary["agree"] == 1  # w2's two runs took different routes
    assert summary["median_seconds"] == {"parallel": 2.5, "sequential": 4.5}
    assert summary["reduction"] == pytest.approx(1 - 2.5 / 4.5)
    assert summary["input_tokens"] == 400 and summary["model_calls"] == 8


async def test_the_plain_baseline_produces_the_same_result_as_the_graph(session_factory):
    incident_id = await incident(session_factory)
    results = []
    for runner in ("plain", "graph"):
        provider = ByType(EXTRACTION, model_says(cause("A leak exhausts the pool.", GOOD)))
        deps = WorkflowDeps(session_factory, provider, CONFIG, corpus_bm25(), Graph())
        run = run_plain if runner == "plain" else run_graph
        results.append(await run(deps, incident_id))

    plain, graph = results
    for key in ("route", "reasons", "diagnosis", "recommendation", "status"):
        assert plain[key] == graph[key], key
    assert plain["steps"][0]["node"] == "intake"
    assert sorted(s["node"] for s in plain["steps"]) == sorted(s["node"] for s in graph["steps"])


class Behaving:
    """A scripted model that does what a correct model would for each case (H24): it extracts the
    recorded service, cites an excerpt of an expected document when one was retrieved, and says
    insufficient evidence when none was. The vague case gets an extraction with no service."""

    def __init__(self, case: WorkflowCase, titles: dict[str, str]) -> None:
        self._case = case
        self._titles = titles  # title -> doc id

    async def generate_structured(self, *, prompt, schema, config):
        if schema is IncidentExtraction:
            vague = self._case.id == "w12"
            service = {
                "name": None if vague else self._case.service,
                "stated_explicitly": not vague,
            }
            reply = EXTRACTION.model_copy(
                update={"service": EXTRACTION.service.model_copy(update=service)}
            )
        else:
            assert schema is ModelDiagnosis
            reply = model_says(status="insufficient_evidence")
            for block in BLOCK.finditer(prompt.input):
                if self._titles.get(block["title"]) in self._case.expected_docs:
                    words = block["text"].split()
                    quote = " ".join(words[: min(len(words), 10)])
                    reply = model_says(cause("Per the documented cause.", (block["id"], quote)))
                    break
        return await FakeProvider([reply]).generate_structured(
            prompt=prompt, schema=schema, config=config
        )

    async def aclose(self):
        return None


@pytest.mark.parametrize("case", load_cases(), ids=lambda c: c.id)
async def test_h24_with_behaving_models_every_case_takes_its_expected_route(session_factory, case):
    titles = {d.title: d.id for d in load_corpus(CORPUS)}
    graph = InMemoryGraphStore([load_catalogue(CATALOGUE)])
    deps = WorkflowDeps(session_factory, Behaving(case, titles), CONFIG, corpus_bm25(), graph)
    async with session_factory() as session:
        created = await IncidentRepository(session).create(
            IncidentCreate(
                title=case.title,
                description=case.description,
                service=case.service,
                severity=case.severity,
                environment=case.environment,
            )
        )

    state = await run_graph(deps, created.incident.id, checkpointer=InMemorySaver())

    assert state["route"] == case.expected_route, state["reasons"]
    assert task_success(case, state)
