"""AIENG-67 / H48: one run can be rebuilt from its trace; the Langfuse adapter sends what Langfuse
expects; tracing never breaks a request."""

import json

import httpx
import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import text
from test_analysis_api import EXTRACTION
from test_diagnosis import cause, model_says
from test_workflow import GOOD, ByType, deps, incident

from incidentops_ai.llm.gateway import Gateway
from incidentops_ai.observability.langfuse import LangfuseExporter
from incidentops_ai.observability.report import load, reconstruct, summary
from incidentops_ai.observability.tracing import (
    JsonlExporter,
    MemoryExporter,
    Tracer,
    get_tracer,
    set_tracer,
)
from incidentops_ai.workflow.runner import Decision, WorkflowRunner, checkpoint_url


@pytest.fixture
def traces(tmp_path):
    exporter = JsonlExporter(tmp_path / "traces.jsonl")
    set_tracer(Tracer([exporter]))
    yield exporter.path
    set_tracer(Tracer())


async def test_one_run_is_rebuilt_from_its_trace(traces, settings, session_factory):
    model = Gateway(ByType(EXTRACTION, model_says(cause("A leak exhausts the pool.", GOOD))))
    async with AsyncPostgresSaver.from_conn_string(checkpoint_url(settings.database_url)) as saver:
        await saver.setup()
        runner = WorkflowRunner(deps(session_factory, model), saver)
        run = await runner.start(await incident(session_factory))
        await runner.decide(run.id, Decision(decision="approve", reviewer="Amara Okafor"))

    spans = load(traces)
    mine = [s for s in spans if s.trace_id == run.id.hex]
    roots = [s for s in mine if s.parent_id is None]
    assert [r.attributes["phase"] for r in roots] == ["start", "decide"]
    steps = {s.attributes["node"] for s in mine if s.kind == "step"}
    assert {"intake", "retrieve", "extract", "diagnose", "assess", "approval", "act"} <= steps
    approval = [s for s in mine if s.name == "step.approval"]
    assert approval[0].status == "ok" and approval[0].attributes.get("paused")  # paused, not failed
    models = [s for s in mine if s.kind == "model"]
    async with session_factory() as session:
        recorded = await session.scalar(text("SELECT count(*) FROM llm_runs"))
    assert len(models) == recorded == 2
    for m in models:  # tokens when the provider reports them (the fake does not), always these:
        assert {"model", "prompt", "outcome", "cost_usd"} <= set(m.attributes)
    assert {m.attributes["prompt"] for m in models} == {
        "extract_incident/v2",
        "diagnose_incident/v3",
    }
    ids = {s.span_id for s in mine}
    assert all(s.parent_id in ids for s in mine if s.parent_id)  # no orphan spans
    text_ = reconstruct(spans, run.id.hex)
    assert "workflow.run [workflow]" in text_ and "model.diagnosis [model]" in text_
    assert "model calls 2" in text_


def test_the_summary_gives_latency_tokens_and_cost_per_span(tmp_path):
    exporter = MemoryExporter()
    tracer = Tracer([exporter])
    for tokens in (100, 300):
        with tracer.span(
            "model.diagnosis", kind="model", input_tokens=tokens, output_tokens=10, cost_usd=0.001
        ):
            pass
    with pytest.raises(RuntimeError), tracer.span("step.act", kind="step"):
        raise RuntimeError("boom")

    rows = {r["name"]: r for r in summary(exporter.spans)}

    assert rows["model.diagnosis"]["count"] == 2 and rows["model.diagnosis"]["input_tokens"] == 400
    assert rows["step.act"]["error_rate"] == 1.0
    assert exporter.spans[-1].error == "RuntimeError"  # the type, never the message


def test_a_failing_exporter_never_breaks_the_request():
    class Broken:
        def export(self, span):
            raise OSError("disk full")

    tracer = Tracer([Broken()])

    with tracer.span("step.x", kind="step"):
        value = 42

    assert value == 42


def test_the_langfuse_adapter_sends_traces_spans_and_generations():
    received = []

    def ingestion(request: httpx.Request) -> httpx.Response:
        received.append((request.headers["authorization"], json.loads(request.content)))
        return httpx.Response(207, json={"successes": [], "errors": []})

    exporter = LangfuseExporter(
        "https://langfuse.test", "pk-1", "sk-1", transport=httpx.MockTransport(ingestion)
    )
    tracer = Tracer([exporter])
    with (
        tracer.span("workflow.run", kind="workflow", trace_id="t" * 32),
        tracer.span("model.diagnosis", kind="model", model="m", input_tokens=10, output_tokens=2),
    ):
        pass

    auth, body = received[0]
    assert auth.startswith("Basic ")
    types = [e["type"] for e in body["batch"]]
    assert types == ["generation-create", "trace-create", "span-create"]
    generation = body["batch"][0]["body"]
    assert generation["traceId"] == "t" * 32 and generation["usage"]["input"] == 10
    assert generation["parentObservationId"] == body["batch"][2]["body"]["id"]


def test_langfuse_errors_are_counted_not_raised():
    def refuse(request):
        return httpx.Response(207, json={"successes": [], "errors": [{"id": "x", "status": 400}]})

    exporter = LangfuseExporter(
        "https://lf.test", "pk", "sk", transport=httpx.MockTransport(refuse)
    )
    with Tracer([exporter]).span("workflow.run", kind="workflow"):
        pass

    assert exporter.failed_events == 1


def test_the_default_tracer_is_a_no_op():
    assert not get_tracer().enabled
