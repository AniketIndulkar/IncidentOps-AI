"""The workflow evaluation (AIENG-48), against the hypotheses in docs/roadmap-w07.md.

    uv run python -m incidentops_ai.workflow.benchmark             # 12 cases, real models (costs)
    uv run python -m incidentops_ai.workflow.benchmark --overhead  # scripted models, free

The real run creates each case as an incident (with its attachments, extracted as at upload), runs
the workflow twice on it, once with the concurrent layout and once with the sequential one, in
alternating order, and scores task success: the expected route and, for a recommendation, at least
one expected document cited. It then crashes one run before `assess` and resumes it with a new
checkpointer connection, counting model calls. `--overhead` times the same steps as a plain async
function and as the LangGraph graph (no checkpointer, in memory, PostgreSQL) with scripted models.

Both modes need INCIDENTOPS_DATABASE_URL pointing at a database with the corpus ingested and, for
the real run, indexed (W03), and they write incidents into it: use a scratch database.
"""

import argparse
import asyncio
import json
import statistics
import time
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from incidentops_ai.config import Settings
from incidentops_ai.db import create_engine, create_session_factory
from incidentops_ai.diagnosis.models import ModelDiagnosis
from incidentops_ai.errors import WorkflowFailedError
from incidentops_ai.extraction.models import IncidentExtraction
from incidentops_ai.graph.catalogue import load_catalogue
from incidentops_ai.graph.memory import InMemoryGraphStore
from incidentops_ai.incidents.models import IncidentCreate
from incidentops_ai.incidents.repository import IncidentRepository
from incidentops_ai.llm.factory import build_model_config, build_provider
from incidentops_ai.llm.fake_provider import FakeProvider
from incidentops_ai.llm.provider import LLMProvider, ModelConfig
from incidentops_ai.multimodal.attachments import AttachmentProcessor, AttachmentRepository
from incidentops_ai.multimodal.extract import (
    OcrExtractor,
    PdfTextExtractor,
    VlmTranscriber,
    detect_media_type,
)
from incidentops_ai.retrieval.bm25 import Bm25Retriever
from incidentops_ai.retrieval.chunking import chunker_from_name
from incidentops_ai.retrieval.documents import load_corpus
from incidentops_ai.retrieval.factory import build_embedder
from incidentops_ai.retrieval.ingest import DEFAULT_CHUNKER
from incidentops_ai.retrieval.stack import build_retriever
from incidentops_ai.workflow.graph import WorkflowDeps, build_workflow, initial_state, nodes
from incidentops_ai.workflow.runner import WorkflowRunner, checkpoint_url

CASES = Path("evals/workflow/cases.jsonl")
CORPUS = Path("data/corpus")
CATALOGUE = Path("data/graph/catalogue.toml")
ASSETS = Path("evals/multimodal/assets")
ASSUMED_USD_PER_M_INPUT = 2.50  # the W06 assumption; the provider does not publish this price
ASSUMED_USD_PER_M_OUTPUT = 10.00

Mode = Literal["parallel", "sequential"]


class WorkflowCase(BaseModel):
    id: str
    title: str
    description: str
    service: str
    severity: str
    environment: str
    attachments: list[str]
    expected_route: Literal["recommend", "review"]
    expected_docs: list[str]
    note: str


class RunResult(BaseModel):
    case_id: str
    mode: Mode
    route: str | None
    reasons: list[str]
    cited: list[str]
    success: bool
    seconds: float
    model_calls: int
    input_tokens: int
    output_tokens: int
    steps: list[dict]


def load_cases(path: Path = CASES) -> list[WorkflowCase]:
    return [
        WorkflowCase.model_validate_json(line)
        for line in path.read_text().splitlines()
        if line.strip()
    ]


def cited_docs(state: dict) -> list[str]:
    """The documents the diagnosis cites, in order of first citation."""
    diagnosis = state.get("diagnosis") or {}
    docs: list[str] = []
    for cause in diagnosis.get("causes", []):
        for citation in cause["citations"]:
            if citation["doc_id"] not in docs:
                docs.append(citation["doc_id"])
    return docs


def task_success(case: WorkflowCase, state: dict) -> bool:
    """The expected route and, for a recommendation, at least one expected document cited."""
    if state.get("route") != case.expected_route:
        return False
    return case.expected_route == "review" or bool(set(cited_docs(state)) & set(case.expected_docs))


def summarise(results: Sequence[RunResult]) -> dict:
    by_case: dict[str, dict[str, RunResult]] = {}
    for r in results:
        by_case.setdefault(r.case_id, {})[r.mode] = r
    agree = sum(
        1
        for runs in by_case.values()
        if len(runs) == 2
        and runs["parallel"].route == runs["sequential"].route
        and set(runs["parallel"].cited) == set(runs["sequential"].cited)
    )
    modes: tuple[Mode, ...] = ("parallel", "sequential")
    median = {
        m: statistics.median(r.seconds for r in results if r.mode == m)
        for m in modes
        if any(r.mode == m for r in results)
    }
    return {
        "success": {m: sum(r.success for r in results if r.mode == m) for m in modes},
        "agree": agree,
        "median_seconds": median,
        "reduction": 1 - median["parallel"] / median["sequential"] if len(median) == 2 else None,
        "model_calls": sum(r.model_calls for r in results),
        "input_tokens": sum(r.input_tokens for r in results),
        "output_tokens": sum(r.output_tokens for r in results),
    }


def corpus_bm25(root: Path = CORPUS) -> Bm25Retriever:
    """BM25 over the corpus in memory: retrieval with no database and no embedding calls."""
    chunker = chunker_from_name(DEFAULT_CHUNKER)
    return Bm25Retriever([chunk for doc in load_corpus(root) for chunk in chunker.chunk(doc)])


async def run_graph(
    deps: WorkflowDeps, incident_id: uuid.UUID, *, checkpointer=None, parallel: bool = True
) -> dict:
    run_id = str(uuid.uuid4())
    workflow = build_workflow(deps, checkpointer=checkpointer, parallel=parallel)
    return await workflow.ainvoke(
        initial_state(incident_id, run_id, deps.tenant_id),
        {"configurable": {"thread_id": run_id}},
    )


async def run_plain(deps: WorkflowDeps, incident_id: uuid.UUID) -> dict:
    """The same steps as a plain async function, concurrent where the graph is (H28's baseline)."""
    step = nodes(deps)
    state = initial_state(incident_id, str(uuid.uuid4()), deps.tenant_id)

    def merge(*updates: dict) -> None:
        for update in updates:
            state["steps"] = state["steps"] + update.pop("steps", [])
            state.update(update)

    merge(await step["intake"](state))
    merge(*await asyncio.gather(step["retrieve"](state), step["graph"](state)))
    merge(*await asyncio.gather(step["extract"](state), step["diagnose"](state)))
    merge(await step["assess"](state))
    merge(await step[state["route"]](state))
    return state


class Counting:
    """Passes calls through to a provider, counting them and their tokens."""

    def __init__(self, inner: LLMProvider) -> None:
        self._inner = inner
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0

    async def generate_structured(self, *, prompt, schema, config):
        result = await self._inner.generate_structured(prompt=prompt, schema=schema, config=config)
        self.calls += 1
        self.input_tokens += result.metadata.input_tokens or 0
        self.output_tokens += result.metadata.output_tokens or 0
        return result

    async def aclose(self) -> None:
        return None


class Scripted:
    """Answers by schema with fixed replies, at no cost and no delay (the overhead runs)."""

    EXTRACTION = IncidentExtraction.model_validate(
        {
            "title": "Notifications stopped",
            "service": {"name": "notification-worker", "stated_explicitly": True},
            "symptoms": [{"description": "No emails since 03:21", "started_at": "03:21"}],
            "error_codes": [],
            "environment": "production",
            "severity_candidate": {"level": "SEV2", "rationale": "Partial outage."},
            "entities": [],
            "missing_information": [],
        }
    )

    async def generate_structured(self, *, prompt, schema, config):
        reply = (
            self.EXTRACTION
            if schema is IncidentExtraction
            else ModelDiagnosis(answer_status="insufficient_evidence", causes=[])
        )
        return await FakeProvider([reply]).generate_structured(
            prompt=prompt, schema=schema, config=config
        )

    async def aclose(self) -> None:
        return None


async def create_incident(
    sessions: async_sessionmaker[AsyncSession],
    case: WorkflowCase,
    processor: AttachmentProcessor | None,
    *,
    tenant_id: str,
) -> uuid.UUID:
    """The case as an incident, its files extracted as the upload route would."""
    async with sessions() as session:
        created = await IncidentRepository(session).create(
            IncidentCreate(
                title=case.title,
                description=case.description,
                service=case.service,
                severity=case.severity,
                environment=case.environment,
            )
        )
        incident_id = created.incident.id
    for name in case.attachments:
        assert processor is not None, f"{case.id} has attachments and no processor was given"
        data = (ASSETS / name).read_bytes()
        extraction = await processor.process(data)
        async with sessions() as session:
            await AttachmentRepository(session).add(
                tenant_id=tenant_id,
                incident_id=incident_id,
                filename=name,
                media_type=detect_media_type(data),
                data=data,
                extraction=extraction,
            )
            await session.commit()
    return incident_id


def _milliseconds(samples: Sequence[float]) -> dict:
    ordered = sorted(samples)
    return {
        "median_ms": round(statistics.median(ordered) * 1000, 1),
        "p95_ms": round(ordered[max(0, round(0.95 * len(ordered)) - 1)] * 1000, 1),
    }


async def overhead(settings: Settings, *, runs: int = 30) -> dict:
    """Time the same steps as a plain function and as the graph, scripted models (H28)."""
    engine = create_engine(settings)
    sessions = create_session_factory(engine)
    case = load_cases()[0]
    deps = WorkflowDeps(
        sessions,
        Scripted(),
        ModelConfig(model="scripted"),
        corpus_bm25(),
        InMemoryGraphStore([load_catalogue(CATALOGUE)]),
    )
    try:
        incident_id = await create_incident(sessions, case, None, tenant_id=deps.tenant_id)
        async with AsyncPostgresSaver.from_conn_string(
            checkpoint_url(settings.database_url)
        ) as saver:
            await saver.setup()
            variants = {
                "plain function": lambda: run_plain(deps, incident_id),
                "graph, no checkpointer": lambda: run_graph(deps, incident_id),
                "graph, in-memory checkpoints": lambda: run_graph(
                    deps, incident_id, checkpointer=InMemorySaver()
                ),
                "graph, PostgreSQL checkpoints": lambda: run_graph(
                    deps, incident_id, checkpointer=saver
                ),
            }
            samples: dict[str, list[float]] = {name: [] for name in variants}
            for name, run in variants.items():  # warm up every path before timing any
                for _ in range(3):
                    await run()
            for _ in range(runs):  # interleaved, so drift hits every variant alike
                for name, run in variants.items():
                    started = time.perf_counter()
                    await run()
                    samples[name].append(time.perf_counter() - started)
    finally:
        await engine.dispose()
    timings = {name: _milliseconds(values) for name, values in samples.items()}
    base = timings["plain function"]["median_ms"]
    for values in timings.values():
        values["overhead_ms"] = round(values["median_ms"] - base, 1)
    return {"runs": runs, "timings": timings}


async def evaluate(settings: Settings, cases: Sequence[WorkflowCase]) -> dict:
    """The real run: every case in both layouts, then one crash and resume (H25 to H27)."""
    engine = create_engine(settings)
    sessions = create_session_factory(engine)
    provider = build_provider(settings)
    embedder = build_embedder(settings)
    config = build_model_config(settings)
    retriever = build_retriever(settings.retrieval_stack, sessions, embedder, None)
    graph = InMemoryGraphStore([load_catalogue(CATALOGUE)])
    processor = AttachmentProcessor(
        pdf=PdfTextExtractor(), ocr=OcrExtractor(), vlm=VlmTranscriber(provider, config)
    )
    results: list[RunResult] = []
    try:
        url = checkpoint_url(settings.database_url)
        async with AsyncPostgresSaver.from_conn_string(url) as saver:
            await saver.setup()
            for index, case in enumerate(cases):
                incident_id = await create_incident(sessions, case, processor, tenant_id="default")
                order: tuple[Mode, ...] = (
                    ("parallel", "sequential") if index % 2 == 0 else ("sequential", "parallel")
                )
                for mode in order:
                    counting = Counting(provider)
                    deps = WorkflowDeps(sessions, counting, config, retriever, graph)
                    started = time.perf_counter()
                    state = await run_graph(
                        deps, incident_id, checkpointer=saver, parallel=mode == "parallel"
                    )
                    results.append(
                        RunResult(
                            case_id=case.id,
                            mode=mode,
                            route=state.get("route"),
                            reasons=state.get("reasons", []),
                            cited=cited_docs(state),
                            success=task_success(case, state),
                            seconds=round(time.perf_counter() - started, 3),
                            model_calls=counting.calls,
                            input_tokens=counting.input_tokens,
                            output_tokens=counting.output_tokens,
                            steps=state["steps"],
                        )
                    )
                    print(f"{case.id} {mode}: {results[-1].route} {results[-1].seconds}s")
            resume = await _crash_and_resume(
                settings, sessions, provider, config, retriever, graph, cases[0], saver
            )
    finally:
        await provider.aclose()
        await embedder.aclose()
        await engine.dispose()
    return {
        "stack": retriever.name,
        "model": config.model,
        "results": [r.model_dump() for r in results],
        "summary": summarise(results),
        "resume": resume,
    }


async def _crash_and_resume(
    settings, sessions, provider, config, retriever, graph, case, saver
) -> dict:
    """Crash a run just before `assess`, then resume it with a new runner and connection."""
    crashed = False

    def crash_once(node: str) -> None:
        nonlocal crashed
        if node == "assess" and not crashed:
            crashed = True
            raise RuntimeError("simulated crash before assess")

    first = Counting(provider)
    deps = WorkflowDeps(sessions, first, config, retriever, graph, fault=crash_once)
    incident_id = await create_incident(sessions, case, None, tenant_id=deps.tenant_id)
    try:
        await WorkflowRunner(deps, saver).start(incident_id)
        raise AssertionError("the run was meant to crash")
    except WorkflowFailedError as failed:
        run_id = failed.run_id
    second = Counting(provider)
    async with AsyncPostgresSaver.from_conn_string(checkpoint_url(settings.database_url)) as fresh:
        run = await WorkflowRunner(
            WorkflowDeps(sessions, second, config, retriever, graph), fresh
        ).resume(run_id)
    return {
        "case_id": case.id,
        "calls_before_crash": first.calls,
        "calls_on_resume": second.calls,
        "status": run.status,
        "route": run.route,
    }


def render(report: dict, date: str) -> str:
    s = report.get("summary")
    lines = [f"# Workflow evaluation ({date})", ""]
    if s:
        n = len({r["case_id"] for r in report["results"]})
        median = s["median_seconds"]
        lines += [
            f"Model `{report['model']}`, retrieval stack `{report['stack']}`, {n} cases, each run",
            "once with the concurrent layout and once with the sequential one (alternating order).",
            "",
            "| | Concurrent | Sequential |",
            "|---|---|---|",
            f"| Task success | {s['success']['parallel']}/{n} | {s['success']['sequential']}/{n} |",
            f"| Median end-to-end | {median['parallel']:.2f} s | {median['sequential']:.2f} s |",
            "",
            (
                f"- Median reduction: **{s['reduction']:.0%}**. Same route and cited documents in both "
                f"layouts: {s['agree']}/{n}."
            ),
            (
                f"- {s['model_calls']} model calls, {s['input_tokens']:,} input and "
                f"{s['output_tokens']:,} output tokens; estimate "
                f"${_usd(s['input_tokens'], s['output_tokens']):.2f} at an assumed price "
                f"(${ASSUMED_USD_PER_M_INPUT}/M input, ${ASSUMED_USD_PER_M_OUTPUT}/M output). "
                "Attachment extraction at upload is not included."
            ),
            "",
            "| Case | Expected | Concurrent | Sequential | Cited (concurrent) | Success | Seconds |",
            "|---|---|---|---|---|---|---|",
        ]
        cases = {c.id: c for c in load_cases()}
        runs: dict[str, dict[str, dict]] = {}
        for r in report["results"]:
            runs.setdefault(r["case_id"], {})[r["mode"]] = r
        for case_id, pair in runs.items():
            par, seq = pair["parallel"], pair["sequential"]

            def route(r: dict) -> str:
                return r["route"] + (f" ({', '.join(r['reasons'])})" if r["reasons"] else "")

            lines.append(
                f"| {case_id} | {cases[case_id].expected_route} | {route(par)} | {route(seq)} | "
                f"{', '.join(par['cited']) or '–'} | "
                f"{'yes' if par['success'] else 'no'} / {'yes' if seq['success'] else 'no'} | "
                f"{par['seconds']:.1f} / {seq['seconds']:.1f} |"
            )
    if resume := report.get("resume"):
        lines += [
            "",
            (
                f"**Crash and resume** ({resume['case_id']}): {resume['calls_before_crash']} model calls "
                f"before the crash, **{resume['calls_on_resume']} on resume**; the resumed run ended "
                f"`{resume['status']}` ({resume['route']})."
            ),
        ]
    if over := report.get("overhead"):
        lines += [
            "",
            f"**Overhead** (scripted models, {over['runs']} interleaved runs per variant):",
            "",
            "| Variant | Median | p95 | Over the plain function |",
            "|---|---|---|---|",
        ]
        for name, t in over["timings"].items():
            lines.append(
                f"| {name} | {t['median_ms']} ms | {t['p95_ms']} ms | {t['overhead_ms']:+} ms |"
            )
    return "\n".join(lines) + "\n"


def _usd(input_tokens: int, output_tokens: int) -> float:
    return (input_tokens * ASSUMED_USD_PER_M_INPUT + output_tokens * ASSUMED_USD_PER_M_OUTPUT) / 1e6


async def _run(args: argparse.Namespace) -> None:
    settings = Settings()
    report: dict = {} if args.overhead else await evaluate(settings, load_cases(args.cases))
    report["overhead"] = await overhead(settings, runs=args.runs)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    stem = args.out_dir / f"{args.date}-{args.name}"
    stem.with_suffix(".json").write_text(json.dumps(report, indent=2) + "\n")
    stem.with_suffix(".md").write_text(render(report, args.date))
    print(render(report, args.date))


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Evaluate the incident workflow (AIENG-48).")
    parser.add_argument("--cases", type=Path, default=CASES)
    parser.add_argument("--out-dir", type=Path, default=Path("evals/workflow/runs"))
    parser.add_argument("--date", default=datetime.now(UTC).date().isoformat())
    parser.add_argument("--name", default="workflow-benchmark")
    parser.add_argument("--runs", type=int, default=30, help="timed runs per overhead variant")
    parser.add_argument("--overhead", action="store_true", help="scripted overhead runs only")
    asyncio.run(_run(parser.parse_args(argv)))


if __name__ == "__main__":
    main()
