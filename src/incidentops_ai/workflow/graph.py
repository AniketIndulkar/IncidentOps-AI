"""The incident workflow as a LangGraph state machine (AIENG-45, AIENG-47; the gate: AIENG-49).

    intake ─┬─ retrieve ─┬─▶ extract (model) ──┬─▶ assess ─┬─▶ recommend ─┬─▶ approval ─┬─▶ act
            └─ graph ────┴─▶ diagnose (model) ─┘            └─▶ review ────┘  (a person) └─▶ rejected

LangGraph runs in super-steps: every node of a step must finish before the next step starts, so
branches of different lengths do not overlap. The two slow model calls are therefore placed in the
same step; extraction waits for the (fast) retrieval it does not need, and in exchange runs beside
the diagnosis.

Two steps call a model (extract, diagnose); everything else, including the route, is code. The
state holds plain JSON-friendly values so a checkpoint can be written after every step and a run
resumed in another process. Every node appends one entry to the step log: name, kind, time, outcome.

`approval` is a LangGraph interrupt: the run stops there, checkpointed, until a person's decision
resumes it (W08). Nothing after it runs without one; re-invoking the paused run stops there again.
"""

import asyncio
import operator
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from typing import Annotated, Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from incidentops_ai.diagnosis.evidence import number_evidence
from incidentops_ai.diagnosis.service import diagnose_from_evidence
from incidentops_ai.extraction.review import assess as review_extraction
from incidentops_ai.extraction.service import RecordedFields, extract_incident
from incidentops_ai.graph.store import GraphStore, GraphUnavailableError
from incidentops_ai.incidents.repository import IncidentRepository
from incidentops_ai.llm.provider import LLMProvider, LLMStatus, ModelConfig
from incidentops_ai.llm.runs import LLMRunRepository
from incidentops_ai.memory.repository import RECALL_LIMIT, MemoryRepository, memory_evidence
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
HUMAN_STEPS = frozenset({"approval"})
MAX_INVALID_ANSWERS = 3  # malformed answers at the gate before the run fails


class Decision(BaseModel):
    """A person's decision at the approval gate, as the API accepts it."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    decision: Literal["approve", "reject"]
    reviewer: str = Field(min_length=1, max_length=128)
    comment: str | None = Field(default=None, max_length=2000)
    memory: str | None = Field(default=None, max_length=2000)  # a note for the service (AIENG-50)


class RecordedDecision(Decision):
    """The decision as the workflow receives it: with the id of its audit event."""

    decision_id: str


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
    memories: list[dict]  # the notes recalled for this run (W08), as they were when recalled
    cmdb_status: str  # ok | unknown_service | unavailable | invalid | off (W09)
    cmdb_record: dict | None
    graph_status: str
    diagnosis: dict | None
    diagnosis_status: str
    route: str
    reasons: list[str]
    recommendation: dict | None
    proposed_action: dict | None  # the ticket a person is asked to approve
    decision: dict | None  # their decision, with its audit event id
    action_result: dict | None  # what act did (the ticket), when it did anything
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
    tickets: Any = None  # TicketActions (AIENG-52): what act writes to; None: nothing is written
    cmdb: Any = None  # CmdbClient (W09): the legacy system of record for ownership; None: off
    # Called with the node name before each node runs: tests use it to crash a run on purpose.
    fault: Callable[[str], None] | None = None
    # Model-call records still being written (W08): the runner waits for them before it records a
    # failure, so a call whose step was cancelled is still on record.
    pending: set[asyncio.Task] = field(default_factory=set)


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
        kind = (
            "model" if name in MODEL_STEPS else "human" if name in HUMAN_STEPS else "deterministic"
        )
        entry = {
            "node": name,
            "kind": kind,
            "ms": round((time.perf_counter() - started) * 1000, 1),
            "outcome": outcome,
        }
        return {**update, "steps": [entry]}

    return node


def nodes(deps: WorkflowDeps) -> dict[str, Node]:
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
        """Record a model call that happened. Shielded: when a sibling step fails, LangGraph
        cancels this step, and the call was made (and paid for) whether or not the step finishes."""

        async def write() -> None:
            async with deps.sessions() as session:
                await LLMRunRepository(session).record(
                    operation=operation,
                    incident_id=uuid.UUID(state["incident_id"]),
                    status=result.status,
                    metadata=result.metadata,
                    error_detail=result.error_detail,
                    output=output,
                )

        task = asyncio.ensure_future(write())
        deps.pending.add(task)
        task.add_done_callback(deps.pending.discard)
        await asyncio.shield(task)

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

    async def recall(state: WorkflowState) -> dict:
        async with deps.sessions() as session:
            notes = await MemoryRepository(session).current(
                tenant_id=state["tenant_id"],
                service=state["incident"]["service"],
                limit=RECALL_LIMIT,
            )
        memories = [
            {
                "id": str(n.id),
                "service": n.service,
                "text": n.text,
                "author": n.author,
                "created_at": n.created_at.isoformat(),
            }
            for n in notes
        ]
        return {"memories": memories, "_outcome": f"{len(memories)} notes"}

    async def cmdb(state: WorkflowState) -> dict:
        if deps.cmdb is None:
            return {"cmdb_status": "off", "cmdb_record": None, "_outcome": "off"}
        lookup = await deps.cmdb.service_record(state["incident"]["service"])
        record = lookup.record.model_dump() if lookup.record else None
        return {"cmdb_status": lookup.status, "cmdb_record": record, "_outcome": lookup.status}

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
            evidence=[
                *number_evidence(retrieved),
                *attachments,
                *memory_evidence(state.get("memories", []), tenant_id=state["tenant_id"]),
            ],
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
        recommendation = {
            "causes": [c["claim"] for c in state["diagnosis"]["causes"]],
            "runbooks": list(runbooks.values()),
            "team": context.get("team_name"),
            "on_call": context.get("on_call"),
            "open_fixes": open_fixes,
        }
        return {
            "recommendation": recommendation,
            "proposed_action": _proposed_ticket(state, recommendation),
            "_outcome": f"{len(runbooks)} runbooks",
        }

    async def review(state: WorkflowState) -> dict:
        return {
            "recommendation": None,
            "proposed_action": _proposed_ticket(state, None),
            "_outcome": ",".join(state["reasons"]),
        }

    async def approval(state: WorkflowState) -> dict:
        # Everything before interrupt() runs again on resume, so nothing here has side effects.
        question = {
            "route": state["route"],
            "reasons": state["reasons"],
            "proposed_action": state["proposed_action"],
        }
        value = interrupt(question)
        # LangGraph stores each answer in the checkpoint and replays it when the node runs
        # again, so raising on a bad answer would replay it forever. Ask again instead.
        for _ in range(MAX_INVALID_ANSWERS):
            try:
                decision = RecordedDecision.model_validate(value)
                break
            except ValidationError:
                value = interrupt({**question, "error": "not a valid decision; send another"})
        else:
            raise ValueError(f"{MAX_INVALID_ANSWERS} invalid answers at the approval gate")
        return {"decision": decision.model_dump(), "_outcome": decision.decision}

    async def act(state: WorkflowState) -> dict:
        if deps.tickets is None:
            return {"action_result": None, "status": "approved", "_outcome": "no ticket system"}
        # Keyed on the run id: a retry after a timeout returns the ticket already created.
        ticket = await deps.tickets.create(
            state["proposed_action"], run_id=state["run_id"], decision=state["decision"]
        )
        outcome = ticket["id"] if ticket["created"] else f"{ticket['id']} (already created)"
        return {"action_result": ticket, "status": "actioned", "_outcome": outcome}

    async def rejected(state: WorkflowState) -> dict:
        return {"action_result": None, "status": "rejected", "_outcome": "rejected"}

    bodies = {
        "intake": intake,
        "extract": extract,
        "retrieve": retrieve,
        "graph": graph,
        "recall": recall,
        "cmdb": cmdb,
        "diagnose": diagnose,
        "assess": assess,
        "recommend": recommend,
        "review": review,
        "approval": approval,
        "act": act,
        "rejected": rejected,
    }
    return {name: _step(name, deps, body) for name, body in bodies.items()}


def build_workflow(deps: WorkflowDeps, *, checkpointer=None, parallel: bool = True):
    """`parallel=False` runs the same nodes one after another (the AIENG-47 comparison)."""
    builder = StateGraph(WorkflowState)
    for name, node in nodes(deps).items():
        builder.add_node(name, node)
    builder.add_edge(START, "intake")
    if parallel:
        builder.add_edge("intake", "retrieve")
        builder.add_edge("intake", "graph")
        builder.add_edge("intake", "recall")
        builder.add_edge("intake", "cmdb")
        builder.add_edge(["retrieve", "graph", "recall", "cmdb"], "extract")
        builder.add_edge(["retrieve", "graph", "recall", "cmdb"], "diagnose")
        builder.add_edge(["extract", "diagnose"], "assess")
    else:
        for source, target in [
            ("intake", "extract"),
            ("extract", "retrieve"),
            ("retrieve", "graph"),
            ("graph", "recall"),
            ("recall", "cmdb"),
            ("cmdb", "diagnose"),
            ("diagnose", "assess"),
        ]:
            builder.add_edge(source, target)
    builder.add_conditional_edges(
        "assess", lambda state: state["route"], {"recommend": "recommend", "review": "review"}
    )
    builder.add_edge("recommend", "approval")
    builder.add_edge("review", "approval")
    builder.add_conditional_edges(
        "approval",
        lambda state: state["decision"]["decision"],
        {"approve": "act", "reject": "rejected"},
    )
    builder.add_edge("act", END)
    builder.add_edge("rejected", END)
    return builder.compile(checkpointer=checkpointer)


def _proposed_ticket(state: WorkflowState, recommendation: dict | None) -> dict:
    """The ticket a person is asked to approve: what to do, and the evidence it rests on."""
    incident = state["incident"]
    cited = []
    for cause in (state.get("diagnosis") or {}).get("causes", []):
        for citation in cause["citations"]:
            line = f"- {citation['doc_id']}: {citation['title']} ({citation['source_path']})"
            if line not in cited:
                cited.append(line)
    if recommendation is not None:
        title = f"Apply runbook: {incident['title']}"
        body = ["Likely causes:", *(f"- {c}" for c in recommendation["causes"])]
        if recommendation["runbooks"]:
            body += ["", "Runbooks:"]
            body += [f"- {r['doc_id']}: {r['title']}" for r in recommendation["runbooks"]]
        if recommendation["on_call"]:
            body += ["", f"On call: {recommendation['on_call']}"]
    else:
        title = f"Investigate: {incident['title']}"
        body = [f"Sent to review: {', '.join(state['reasons'])}."]
    record, cmdb_status = state.get("cmdb_record"), state.get("cmdb_status")
    if record:
        body += [
            "",
            (
                f"Owner (CMDB): {record['owner']}, tier {record['tier']}, "
                f"maintenance {record['maintenance_window']}."
            ),
        ]
    elif cmdb_status in {"unavailable", "invalid"}:
        body += ["", f"Owner: unknown (CMDB {cmdb_status}); nothing assumed."]
    elif cmdb_status == "unknown_service":
        body += ["", "Owner: unknown (not in the CMDB)."]
    if cited:
        body += ["", "Evidence cited:", *cited]
    body += ["", f"Incident {incident['id']} ({incident['severity']}, {incident['environment']})."]
    return {
        "kind": "create_ticket",
        "title": title[:200],
        "body": "\n".join(body),
        "service": incident["service"],
        "severity": incident["severity"],
        "incident_id": incident["id"],
    }
