"""AIENG-45/47: the workflow's nodes, deterministic routing, and the concurrent branches."""

import asyncio
import time
import uuid

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from test_analysis_api import EXTRACTION
from test_diagnosis import POOL, StaticRetriever, cause, chunks_of, model_says, retrieved

from incidentops_ai.diagnosis.models import ModelDiagnosis
from incidentops_ai.errors import IncidentNotFoundError
from incidentops_ai.extraction.models import IncidentExtraction
from incidentops_ai.graph.store import GraphUnavailableError
from incidentops_ai.incidents.models import IncidentCreate
from incidentops_ai.incidents.repository import IncidentRepository
from incidentops_ai.llm.fake_provider import FakeProvider
from incidentops_ai.llm.provider import LLMStatus, ModelConfig
from incidentops_ai.retrieval.embeddings import EmbeddingError
from incidentops_ai.workflow.graph import WorkflowDeps, build_workflow, initial_state

CONFIG = ModelConfig(model="fake-model")
GOOD = ("E1", "exhausts it within minutes")
VAGUE = EXTRACTION.model_copy(
    update={
        "service": EXTRACTION.service.model_copy(update={"name": None, "stated_explicitly": False})
    }
)


class ByType:
    """A scripted model that answers by schema, so concurrent calls get the right reply."""

    def __init__(self, extraction, diagnosis, *, delay: float = 0.0) -> None:
        self._replies = {IncidentExtraction: extraction, ModelDiagnosis: diagnosis}
        self._delay = delay
        self.calls: list[str] = []

    async def generate_structured(self, *, prompt, schema, config):
        self.calls.append(schema.__name__)
        await asyncio.sleep(self._delay)
        return await FakeProvider([self._replies[schema]]).generate_structured(
            prompt=prompt, schema=schema, config=config
        )

    async def aclose(self):
        return None


class Graph:
    def __init__(self, *, down: bool = False) -> None:
        self.down = down

    async def service_context(self, service, *, tenant):
        if self.down:
            raise GraphUnavailableError("neo4j unreachable")
        from incidentops_ai.graph.store import ServiceContext

        return ServiceContext(service, "Commerce Platform", "Amara Okafor", ("orders-db",), (), ())

    async def open_fixes_upstream(self, service, *, tenant, max_hops=2):
        if self.down:
            raise GraphUnavailableError("neo4j unreachable")
        return []


class FailingRetriever(StaticRetriever):
    async def retrieve(self, query, *, k, filters, hints=None):
        raise EmbeddingError("embedding request timed out")


async def incident(session_factory, *, severity: str = "SEV2") -> uuid.UUID:
    async with session_factory() as session:
        created = await IncidentRepository(session).create(
            IncidentCreate(
                title="Orders API timing out",
                description="Writes wait for a pool connection and time out after 30 seconds.",
                service="orders-api",
                severity=severity,
                environment="production",
            )
        )
        return created.incident.id


def deps(session_factory, provider, *, retriever=None, graph=None, fault=None) -> WorkflowDeps:
    return WorkflowDeps(
        sessions=session_factory,
        provider=provider,
        config=CONFIG,
        retriever=retriever or StaticRetriever(retrieved(*chunks_of("rb-orders-db-pool", POOL))),
        graph=graph if graph is not None else Graph(),
        fault=fault,
    )


async def run(workflow_deps, incident_id, *, parallel: bool = True) -> dict:
    workflow = build_workflow(workflow_deps, checkpointer=InMemorySaver(), parallel=parallel)
    run_id = str(uuid.uuid4())
    return await workflow.ainvoke(
        initial_state(incident_id, run_id), {"configurable": {"thread_id": run_id}}
    )


async def test_a_clear_incident_is_recommended_with_its_cited_runbook(session_factory):
    provider = ByType(EXTRACTION, model_says(cause("A leak exhausts the pool.", GOOD)))

    state = await run(deps(session_factory, provider), await incident(session_factory))

    assert (state["route"], state["status"], state["reasons"]) == ("recommend", "recommended", [])
    assert state["recommendation"]["runbooks"][0]["doc_id"] == "rb-orders-db-pool"
    assert state["recommendation"]["on_call"] == "Amara Okafor"
    nodes = {step["node"]: step for step in state["steps"]}
    assert set(nodes) == {
        "intake",
        "extract",
        "retrieve",
        "graph",
        "diagnose",
        "assess",
        "recommend",
    }
    assert nodes["extract"]["kind"] == nodes["diagnose"]["kind"] == "model"
    assert nodes["assess"]["kind"] == nodes["retrieve"]["kind"] == "deterministic"
    assert sorted(provider.calls) == ["IncidentExtraction", "ModelDiagnosis"]


@pytest.mark.parametrize(
    ("extraction", "diagnosis", "severity", "reason"),
    [
        (EXTRACTION, model_says(cause("Leak.", GOOD)), "SEV1", "sev1"),
        (EXTRACTION, model_says(status="insufficient_evidence"), "SEV2", "no_answer"),
        (LLMStatus.TIMED_OUT, model_says(cause("Leak.", GOOD)), "SEV2", "model_failed"),
        (EXTRACTION, LLMStatus.INVALID_OUTPUT, "SEV2", "model_failed"),
        (
            EXTRACTION,
            model_says(cause("Leak.", GOOD), cause("Invented.", ("E1", "nothing like this text"))),
            "SEV2",
            "unsupported_causes",
        ),
        (VAGUE, model_says(cause("Leak.", GOOD)), "SEV2", "extraction_needs_review"),
    ],
    ids=["sev1", "no-answer", "extraction-failed", "diagnosis-failed", "unsupported", "vague"],
)
async def test_each_review_reason_routes_to_review(
    session_factory, extraction, diagnosis, severity, reason
):
    state = await run(
        deps(session_factory, ByType(extraction, diagnosis)),
        await incident(session_factory, severity=severity),
    )

    assert (state["route"], state["status"]) == ("review", "needs_review")
    assert reason in state["reasons"]
    assert "recommendation" not in state or state["recommendation"] is None


async def test_a_graph_outage_is_recorded_and_does_not_change_the_route(session_factory):
    provider = ByType(EXTRACTION, model_says(cause("Leak.", GOOD)))

    state = await run(
        deps(session_factory, provider, graph=Graph(down=True)), await incident(session_factory)
    )

    assert state["graph_status"] == "unavailable" and state["route"] == "recommend"
    assert state["recommendation"]["on_call"] is None


async def test_a_retrieval_failure_leaves_no_corpus_evidence_and_ends_in_review(session_factory):
    provider = ByType(EXTRACTION, model_says(cause("Leak.", GOOD)))

    state = await run(
        deps(session_factory, provider, retriever=FailingRetriever([])),
        await incident(session_factory),
    )

    assert state["retrieval_error"] == "EmbeddingError"
    assert state["diagnosis"]["no_answer_reason"] == "no_evidence"
    assert state["route"] == "review" and "no_answer" in state["reasons"]
    assert provider.calls == ["IncidentExtraction"]  # no evidence: the diagnosis model is not asked


async def test_an_unknown_incident_fails_the_run(session_factory):
    with pytest.raises(IncidentNotFoundError):
        await run(deps(session_factory, ByType(EXTRACTION, model_says())), uuid.uuid4())


async def test_parallel_and_sequential_runs_agree_and_parallel_is_faster(session_factory):
    incident_id = await incident(session_factory)
    timings, results = {}, {}
    for parallel in (True, False):
        provider = ByType(EXTRACTION, model_says(cause("Leak.", GOOD)), delay=0.3)
        started = time.perf_counter()
        results[parallel] = await run(
            deps(session_factory, provider), incident_id, parallel=parallel
        )
        timings[parallel] = time.perf_counter() - started

    for key in ("route", "reasons", "diagnosis", "recommendation"):
        assert results[True][key] == results[False][key], key
    assert timings[True] < 0.8 * timings[False]
