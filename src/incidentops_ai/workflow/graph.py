"""The incident workflow as a LangGraph state machine (AIENG-45, AIENG-47).

    intake ─┬─ retrieve ─┬─▶ extract (model) ──┬─▶ assess ─┬─▶ recommend
            └─ graph ────┴─▶ diagnose (model) ─┘            └─▶ review

LangGraph runs in super-steps: every node of a step must finish before the next step starts, so
branches of different lengths do not overlap. The two slow model calls are therefore placed in the
same step; extraction waits for the (fast) retrieval it does not need, and in exchange runs beside
the diagnosis.

Two steps call a model (extract, diagnose); everything else, including the route, is code. The
state holds plain JSON-friendly values so a checkpoint can be written after every step and a run
resumed in another process. Every node appends one entry to the step log: name, kind, time, outcome.
"""

import operator
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from incidentops_ai.diagnosis.evidence import number_evidence
from incidentops_ai.diagnosis.service import diagnose_from_evidence
from incidentops_ai.extraction.review import assess as review_extraction
from incidentops_ai.extraction.service import RecordedFields, extract_incident
from incidentops_ai.graph.store import GraphStore, GraphUnavailableError
from incidentops_ai.incidents.repository import IncidentRepository
from incidentops_ai.llm.provider import LLMProvider, LLMStatus, ModelConfig
from incidentops_ai.llm.runs import LLMRunRepository
from incidentops_ai.multimodal.attachments import (
    AttachmentRepository,
    AttachmentText,
    attachment_evidence,
)
from incidentops_ai.multimodal.extract import TextLine
from incidentops_ai.retrieval.chunking import Chunk
from incidentops_ai.retrieval.documents import DEFAULT_TENANT, DocType
from incidentops_ai.retrieval.embeddings import EmbeddingError
from incidentops_ai.retrieval.rerank import RerankerError
from incidentops_ai.retrieval.retriever import (
    QueryHints,
    RetrievalFilter,
    RetrievedChunk,
    Retriever,
)
from incidentops_ai.workflow.assess import review_reasons

EVIDENCE_COUNT = 5
MODEL_STEPS = frozenset({"extract", "diagnose"})


class WorkflowState(TypedDict, total=False):
    run_id: str
    incident_id: str
    tenant_id: str
    incident: dict
    attachments: list[dict]
    extraction: dict | None
    extraction_status: str
    extraction_review: dict | None
    evidence: list[dict]
    retrieval_error: str | None
    service_context: dict | None
    graph_status: str
    diagnosis: dict | None
    diagnosis_status: str
    route: str
    reasons: list[str]
    recommendation: dict | None
    status: str
    steps: Annotated[list[dict], operator.add]  # concurrent branches append without conflict


@dataclass
class WorkflowDeps:
    sessions: async_sessionmaker[AsyncSession]
    provider: LLMProvider
    config: ModelConfig
    retriever: Retriever
    graph: GraphStore | None
    tenant_id: str = DEFAULT_TENANT
    # Called with the node name before each node runs: tests use it to crash a run on purpose.
    fault: Callable[[str], None] | None = None


def initial_state(
    incident_id: uuid.UUID | str, run_id: str, tenant_id: str = DEFAULT_TENANT
) -> dict:
    return {
        "run_id": run_id,
        "incident_id": str(incident_id),
        "tenant_id": tenant_id,
        "steps": [],
        "reasons": [],
    }


def _chunk_dict(chunk: Chunk) -> dict:
    data = asdict(chunk)
    data["doc_type"] = chunk.doc_type.value
    return data


def _chunk(data: dict) -> Chunk:
    return Chunk(**{**data, "doc_type": DocType(data["doc_type"])})


def _attachment(data: dict) -> AttachmentText:
    lines = tuple(
        TextLine(l["text"], l["page"], tuple(l["box"]) if l["box"] else None, l["confidence"])
        for l in data["lines"]
    )
    return AttachmentText(data["id"], data["filename"], data["method"], lines)


def _query(state: WorkflowState) -> str:
    incident = state["incident"]
    return f"{incident['title']}\n\n{incident['description']}"


Node = Callable[[WorkflowState], Awaitable[dict]]


def _step(name: str, deps: WorkflowDeps, body: Node) -> Node:
    async def node(state: WorkflowState) -> dict:
        if deps.fault is not None:
            deps.fault(name)
        started = time.perf_counter()
        update = await body(state)
        outcome = update.pop("_outcome", "ok")
        kind = "model" if name in MODEL_STEPS else "deterministic"
        entry = {
            "node": name,
            "kind": kind,
            "ms": round((time.perf_counter() - started) * 1000, 1),
            "outcome": outcome,
        }
        return {**update, "steps": [entry]}

    return node


def _nodes(deps: WorkflowDeps) -> dict[str, Node]:
    async def intake(state: WorkflowState) -> dict:
        async with deps.sessions() as session:
            incident = await IncidentRepository(session).get(uuid.UUID(state["incident_id"]))
            sources = await AttachmentRepository(session).texts_for_incident(incident.id)
        return {
            "incident": incident.model_dump(
                mode="json",
                include={"id", "title", "description", "service", "severity", "environment"},
            ),
            "attachments": [
                {
                    "id": s.id,
                    "filename": s.filename,
                    "method": s.method,
                    "lines": [
                        {
                            "text": l.text,
                            "page": l.page,
                            "box": list(l.box) if l.box else None,
                            "confidence": l.confidence,
                        }
                        for l in s.lines
                    ],
                }
                for s in sources
            ],
            "status": "running",
        }

    async def record(
        operation: str, state: WorkflowState, result: Any, output: dict | None
    ) -> None:
        async with deps.sessions() as session:
            await LLMRunRepository(session).record(
                operation=operation,
                incident_id=uuid.UUID(state["incident_id"]),
                status=result.status,
                metadata=result.metadata,
                error_detail=result.error_detail,
                output=output,
            )

    async def extract(state: WorkflowState) -> dict:
        incident = state["incident"]
        recorded = RecordedFields(
            incident["service"], incident["severity"], incident["environment"]
        )
        result = await extract_incident(
            deps.provider, _query(state), deps.config, recorded=recorded
        )
        update: dict = {"extraction_status": result.status.value, "_outcome": result.status.value}
        output = None
        if result.status is LLMStatus.SUCCEEDED and result.value is not None:
            update["extraction"] = result.value.model_dump(mode="json")
            update["extraction_review"] = review_extraction(result.value).model_dump(mode="json")
            output = {"extraction": update["extraction"], "review": update["extraction_review"]}
        await record("workflow_extract", state, result, output)
        return update

    async def retrieve(state: WorkflowState) -> dict:
        try:
            results = await deps.retriever.retrieve(
                _query(state),
                k=EVIDENCE_COUNT,
                filters=RetrievalFilter(tenant_id=state["tenant_id"]),
                hints=QueryHints(services=(state["incident"]["service"],)),
            )
        except (EmbeddingError, RerankerError) as error:
            name = type(error).__name__
            return {"evidence": [], "retrieval_error": name, "_outcome": name}
        evidence = [
            {"chunk": _chunk_dict(r.chunk), "score": r.score, "rank": r.rank} for r in results
        ]
        return {
            "evidence": evidence,
            "retrieval_error": None,
            "_outcome": f"{len(evidence)} chunks",
        }

    async def graph(state: WorkflowState) -> dict:
        if deps.graph is None:
            return {"service_context": None, "graph_status": "off", "_outcome": "off"}
        try:
            context = await deps.graph.service_context(
                state["incident"]["service"], tenant=state["tenant_id"]
            )
        except GraphUnavailableError:
            return {
                "service_context": None,
                "graph_status": "unavailable",
                "_outcome": "unavailable",
            }
        return {
            "service_context": asdict(context) if context else None,
            "graph_status": "ok",
            "_outcome": "ok" if context else "unknown service",
        }

    async def diagnose(state: WorkflowState) -> dict:
        retrieved = [
            RetrievedChunk(chunk=_chunk(e["chunk"]), score=e["score"], rank=e["rank"])
            for e in state.get("evidence", [])
        ]
        attachments = await attachment_evidence(
            _query(state),
            [_attachment(a) for a in state.get("attachments", [])],
            tenant_id=state["tenant_id"],
        )
        result = await diagnose_from_evidence(
            deps.provider,
            query=_query(state),
            evidence=[*number_evidence(retrieved), *attachments],
            config=deps.config,
            stack=deps.retriever.name,
        )
        diagnosis = result.diagnosis.model_dump(mode="json") if result.diagnosis else None
        await record(
            "workflow_diagnose", state, result, {"diagnosis": diagnosis} if diagnosis else None
        )
        return {
            "diagnosis": diagnosis,
            "diagnosis_status": result.status.value,
            "_outcome": diagnosis["status"] if diagnosis else result.status.value,
        }

    async def assess(state: WorkflowState) -> dict:
        reasons = review_reasons(state)
        route = "review" if reasons else "recommend"
        return {"reasons": reasons, "route": route, "_outcome": route}

    async def recommend(state: WorkflowState) -> dict:
        types = {e["chunk"]["doc_id"]: e["chunk"]["doc_type"] for e in state.get("evidence", [])}
        runbooks: dict[str, dict] = {}
        for cause in state["diagnosis"]["causes"]:
            for citation in cause["citations"]:
                if types.get(citation["doc_id"]) == DocType.RUNBOOK.value:
                    runbooks.setdefault(
                        citation["doc_id"],
                        {
                            "doc_id": citation["doc_id"],
                            "title": citation["title"],
                            "source_path": citation["source_path"],
                            "why": cause["claim"],
                        },
                    )
        context = state.get("service_context") or {}
        open_fixes: list[dict] = []
        if deps.graph is not None and state.get("graph_status") == "ok":
            try:
                fixes = await deps.graph.open_fixes_upstream(
                    state["incident"]["service"], tenant=state["tenant_id"], max_hops=1
                )
                open_fixes = [
                    {"id": f.id, "service": f.service, "description": f.description} for f in fixes
                ]
            except GraphUnavailableError:
                open_fixes = []
        return {
            "recommendation": {
                "causes": [c["claim"] for c in state["diagnosis"]["causes"]],
                "runbooks": list(runbooks.values()),
                "team": context.get("team_name"),
                "on_call": context.get("on_call"),
                "open_fixes": open_fixes,
            },
            "status": "recommended",
            "_outcome": f"{len(runbooks)} runbooks",
        }

    async def review(state: WorkflowState) -> dict:
        return {
            "recommendation": None,
            "status": "needs_review",
            "_outcome": ",".join(state["reasons"]),
        }

    bodies = {
        "intake": intake,
        "extract": extract,
        "retrieve": retrieve,
        "graph": graph,
        "diagnose": diagnose,
        "assess": assess,
        "recommend": recommend,
        "review": review,
    }
    return {name: _step(name, deps, body) for name, body in bodies.items()}


def build_workflow(deps: WorkflowDeps, *, checkpointer=None, parallel: bool = True):
    """`parallel=False` runs the same nodes one after another (the AIENG-47 comparison)."""
    builder = StateGraph(WorkflowState)
    for name, node in _nodes(deps).items():
        builder.add_node(name, node)
    builder.add_edge(START, "intake")
    if parallel:
        builder.add_edge("intake", "retrieve")
        builder.add_edge("intake", "graph")
        builder.add_edge(["retrieve", "graph"], "extract")
        builder.add_edge(["retrieve", "graph"], "diagnose")
        builder.add_edge(["extract", "diagnose"], "assess")
    else:
        for source, target in [
            ("intake", "extract"),
            ("extract", "retrieve"),
            ("retrieve", "graph"),
            ("graph", "diagnose"),
            ("diagnose", "assess"),
        ]:
            builder.add_edge(source, target)
    builder.add_conditional_edges(
        "assess", lambda state: state["route"], {"recommend": "recommend", "review": "review"}
    )
    builder.add_edge("recommend", END)
    builder.add_edge("review", END)
    return builder.compile(checkpointer=checkpointer)
