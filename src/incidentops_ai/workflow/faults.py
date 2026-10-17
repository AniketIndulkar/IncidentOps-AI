"""Fault injection for the workflow (AIENG-53), against hypotheses H33 and H34 in roadmap-w08.md.

    INCIDENTOPS_DATABASE_URL=<scratch database> uv run python -m incidentops_ai.workflow.faults

Each scenario injects one fault into a real run (real PostgreSQL, real checkpoints, the real ticket
MCP server over stdio, scripted models) and reports how it ended: the run's status, its audit
events, the tickets it left, and the model calls made against those recorded. A scenario passes
when the run ends in the expected explicit status with its events, no duplicate ticket and no
unrecorded model call. The test suite runs the same scenarios.
"""

import argparse
import asyncio
import contextlib
import statistics
import tempfile
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from incidentops_ai.config import Settings
from incidentops_ai.db import create_engine, create_session_factory
from incidentops_ai.diagnosis.models import ModelDiagnosis
from incidentops_ai.errors import (
    WorkflowFailedError,
    WorkflowNotAwaitingDecisionError,
    WorkflowNotResumableError,
)
from incidentops_ai.extraction.models import IncidentExtraction
from incidentops_ai.incidents.models import IncidentCreate
from incidentops_ai.incidents.repository import IncidentRepository
from incidentops_ai.llm.fake_provider import FakeProvider
from incidentops_ai.llm.provider import LLMStatus, ModelConfig, Prompt, ToolCall, ToolSpec
from incidentops_ai.llm.runs import LLMRunRow
from incidentops_ai.llm.tool_loop import run_tool_loop
from incidentops_ai.tickets.client import TicketActions, TicketClient, TicketTimeoutError
from incidentops_ai.tools.registry import Tool, ToolRegistry
from incidentops_ai.workflow.benchmark import Scripted, corpus_bm25
from incidentops_ai.workflow.graph import WorkflowDeps
from incidentops_ai.workflow.runner import Decision, WorkflowRunner, checkpoint_url

SECRET = "fault-injection-approval-secret"
APPROVE = Decision(decision="approve", reviewer="Amara Okafor")
REJECT = Decision(decision="reject", reviewer="Amara Okafor", comment="Wrong service.")
TERMINAL = {"actioned", "approved", "rejected", "failed", "abandoned", "awaiting_approval"}


class Model:
    """Answers by schema. A schema can be scripted to fail (an LLMStatus) or to raise."""

    def __init__(
        self,
        *,
        extraction: IncidentExtraction | LLMStatus = Scripted.EXTRACTION,
        raise_on: type | None = None,
    ) -> None:
        self._replies = {
            IncidentExtraction: extraction,
            ModelDiagnosis: ModelDiagnosis(answer_status="insufficient_evidence", causes=[]),
        }
        self._raise_on = raise_on
        self.calls = 0

    async def generate_structured(self, *, prompt, schema, config):
        if schema is self._raise_on:
            raise RuntimeError(f"{schema.__name__} provider crashed")
        self.calls += 1
        return await FakeProvider([self._replies[schema]]).generate_structured(
            prompt=prompt, schema=schema, config=config
        )

    async def aclose(self) -> None:
        return None


@dataclass
class Outcome:
    fault: str
    expected: str
    status: str
    events: list[str]
    tickets: int
    calls_made: int
    calls_recorded: int
    ok: bool
    detail: str = ""


@dataclass
class FaultEnv:
    sessions: async_sessionmaker[AsyncSession]
    saver: AsyncPostgresSaver
    workdir: Path
    _count: list[int] = field(default_factory=lambda: [0])

    def tickets_db(self) -> Path:
        self._count[0] += 1
        return self.workdir / f"tickets-{self._count[0]}.db"

    def deps(self, model, *, tickets=None, fault=None) -> WorkflowDeps:
        return WorkflowDeps(
            self.sessions,
            model,
            ModelConfig(model="scripted"),
            corpus_bm25(),
            None,
            tickets=tickets,
            fault=fault,
        )

    async def incident(self) -> uuid.UUID:
        async with self.sessions() as session:
            created = await IncidentRepository(session).create(
                IncidentCreate(
                    title="Orders cannot be placed",
                    description="Writes wait for a pool connection and time out after 30 s.",
                    service="orders-api",
                    severity="SEV2",
                    environment="production",
                )
            )
            return created.incident.id

    async def recorded(self, incident_id: uuid.UUID) -> int:
        async with self.sessions() as session:
            return await session.scalar(
                select(func.count())
                .select_from(LLMRunRow)
                .where(LLMRunRow.incident_id == incident_id)
            )


async def _outcome(
    env: FaultEnv,
    runner: WorkflowRunner,
    run_id: uuid.UUID,
    incident_id: uuid.UUID,
    model: Model,
    *,
    fault: str,
    expected: str,
    tickets: int,
    max_tickets: int = 1,
    detail: str = "",
) -> Outcome:
    run = await runner.get(run_id)
    events = [e.kind for e in await runner.events(run_id)]
    recorded = await env.recorded(incident_id)
    ok = (
        run.status == expected
        and run.status in TERMINAL
        and bool(events)
        and tickets <= max_tickets
        and recorded == model.calls
    )
    return Outcome(fault, expected, run.status, events, tickets, model.calls, recorded, ok, detail)


async def tool_timeout(env: FaultEnv) -> Outcome:
    """The ticket server stores the ticket, then answers too late. Resume must not duplicate it."""
    db, model = env.tickets_db(), Model()
    slow = {"INCIDENTOPS_TICKETS_FAULT_DELAY_S": "2"}
    async with TicketClient.connect(db_path=db, secret=SECRET, extra_env=slow) as tickets:
        runner = WorkflowRunner(
            env.deps(model, tickets=TicketActions(tickets, SECRET, timeout_s=0.5)), env.saver
        )
        incident_id = await env.incident()
        run = await runner.start(incident_id)
        cause = "no error"
        try:
            await runner.decide(run.id, APPROVE)
        except Exception as error:  # noqa: BLE001  (the expected failure)
            cause = type(error.__cause__).__name__
    async with TicketClient.connect(db_path=db, secret=SECRET) as tickets:  # the server restarted
        runner = WorkflowRunner(env.deps(model, tickets=TicketActions(tickets, SECRET)), env.saver)
        await runner.resume(run.id)
        count = len(await tickets.search("Investigate"))
        state = await runner.state(run.id)
    outcome = await _outcome(
        env,
        runner,
        run.id,
        incident_id,
        model,
        fault="tool timeout after the write",
        expected="actioned",
        tickets=count,
        detail=f"first attempt: {cause}; resume: created={state['action_result']['created']}",
    )
    outcome.ok = outcome.ok and cause == TicketTimeoutError.__name__
    return outcome


async def duplicate_call(env: FaultEnv) -> Outcome:
    """The write is sent again for the same run; a model repeats a tool call."""
    db, model = env.tickets_db(), Model()
    async with TicketClient.connect(db_path=db, secret=SECRET) as tickets:
        actions = TicketActions(tickets, SECRET)
        runner = WorkflowRunner(env.deps(model, tickets=actions), env.saver)
        incident_id = await env.incident()
        run = await runner.start(incident_id)
        await runner.decide(run.id, APPROVE)
        state = await runner.state(run.id)
        again = await actions.create(
            state["proposed_action"], run_id=str(run.id), decision=state["decision"]
        )
        count = len(await tickets.search("Investigate"))
    loop = await _looping_tool_call()
    return await _outcome(
        env,
        runner,
        run.id,
        incident_id,
        model,
        fault="duplicate write; repeated tool call",
        expected="actioned",
        tickets=count,
        detail=f"second write created={again['created']}; tool loop: {loop}",
    )


async def _looping_tool_call() -> str:
    ran: list = []

    async def handler(args):
        ran.append(args)
        return []

    from pydantic import BaseModel

    class Args(BaseModel):
        query: str

    registry = ToolRegistry(
        [Tool(ToolSpec(name="search_tickets", description="d", arguments=Args), handler)]
    )
    same = [
        [ToolCall(call_id=f"c{i}", name="search_tickets", arguments_json='{"query": "pool"}')]
        for i in range(10)
    ]
    result = await run_tool_loop(
        FakeProvider(tool_turns=same),
        Prompt(name="p", version="v1", instructions="i", input="x"),
        registry,
        ModelConfig(model="scripted"),
        max_rounds=10,
    )
    return f"{result.stop_reason} after {result.rounds} rounds, tool ran {len(ran)}x"


async def invalid_state(env: FaultEnv) -> Outcome:
    """Decisions and resumes sent to runs in the wrong state, and a malformed resume value."""
    model = Model()
    runner = WorkflowRunner(env.deps(model), env.saver)
    incident_id = await env.incident()
    run = await runner.start(incident_id)
    refused = []
    try:
        await runner.resume(run.id)  # paused, not failed
    except WorkflowNotResumableError:
        refused.append("resume of a paused run")
    config = {"configurable": {"thread_id": str(run.id)}}
    asked = await runner.graph.ainvoke(Command(resume={"decision": "maybe"}), config)
    if "__interrupt__" in asked:  # the gate asks again rather than storing a bad answer
        refused.append("malformed decision")
    snapshot = await runner.graph.aget_state(config)
    await runner.decide(run.id, APPROVE)
    try:
        await runner.decide(run.id, REJECT)
    except WorkflowNotAwaitingDecisionError:
        refused.append("second decision")
    outcome = await _outcome(
        env,
        runner,
        run.id,
        incident_id,
        model,
        fault="invalid state",
        expected="approved",
        tickets=0,
        detail=f"refused: {', '.join(refused)}; gate still next: {snapshot.next == ('approval',)}",
    )
    outcome.ok = outcome.ok and len(refused) == 3 and snapshot.next == ("approval",)
    return outcome


async def model_failure(env: FaultEnv) -> Outcome:
    """The extraction times out (the run is reviewed, still gated); the diagnosis provider raises
    (the run fails, and resumes with a working model)."""
    timed_out = Model(extraction=LLMStatus.TIMED_OUT)
    runner = WorkflowRunner(env.deps(timed_out), env.saver)
    reviewed = await runner.start(await env.incident())
    crashing = Model(raise_on=ModelDiagnosis)
    runner = WorkflowRunner(env.deps(crashing), env.saver)
    incident_id = await env.incident()
    with contextlib.suppress(WorkflowFailedError):  # the expected failure
        await runner.start(incident_id)
    run = (await runner.list_for_incident(incident_id))[0]
    failed_status = (await runner.get(run.id)).status
    healthy = Model()
    runner = WorkflowRunner(env.deps(healthy), env.saver)
    await runner.resume(run.id)
    calls = crashing.calls + healthy.calls
    crashing.calls = calls  # what _outcome compares with the records
    outcome = await _outcome(
        env,
        runner,
        run.id,
        incident_id,
        crashing,
        fault="model failure",
        expected="awaiting_approval",
        tickets=0,
        detail=(
            f"timeout: {reviewed.status} via {', '.join(reviewed.reasons)}; "
            f"crash: {failed_status}, resumed to the gate"
        ),
    )
    outcome.ok = outcome.ok and reviewed.status == "awaiting_approval" and failed_status == "failed"
    return outcome


async def approval_rejected(env: FaultEnv) -> Outcome:
    db, model = env.tickets_db(), Model()
    async with TicketClient.connect(db_path=db, secret=SECRET) as tickets:
        runner = WorkflowRunner(env.deps(model, tickets=TicketActions(tickets, SECRET)), env.saver)
        incident_id = await env.incident()
        run = await runner.start(incident_id)
        await runner.decide(run.id, REJECT)
        count = len(await tickets.search("Investigate"))
    decided = next(e for e in await runner.events(run.id) if e.kind == "decided")
    outcome = await _outcome(
        env,
        runner,
        run.id,
        incident_id,
        model,
        fault="approval rejected",
        expected="rejected",
        tickets=count,
        max_tickets=0,
        detail=f"decided by {decided.actor}: {decided.data['comment']}",
    )
    return outcome


SCENARIOS: dict[str, Callable[[FaultEnv], Awaitable[Outcome]]] = {
    "tool_timeout": tool_timeout,
    "duplicate_call": duplicate_call,
    "invalid_state": invalid_state,
    "model_failure": model_failure,
    "approval_rejected": approval_rejected,
}


async def mcp_timings(workdir: Path, *, calls: int = 50) -> dict:
    """H34: how long the ticket server takes to start, and a read call once connected."""
    started = time.perf_counter()
    async with TicketClient.connect(db_path=workdir / "timing.db", secret=SECRET) as tickets:
        startup = time.perf_counter() - started
        samples = []
        for _ in range(calls):
            began = time.perf_counter()
            await tickets.search("latency", service="payments-gateway")
            samples.append(time.perf_counter() - began)
    return {
        "startup_s": round(startup, 2),
        "read_median_ms": round(statistics.median(samples) * 1000, 2),
        "read_max_ms": round(max(samples) * 1000, 2),
        "calls": calls,
    }


def render(outcomes: list[Outcome], timings: dict, date: str) -> str:
    lines = [
        f"# Workflow fault injection ({date})",
        "",
        "Real PostgreSQL and checkpoints, the ticket MCP server over stdio, scripted models.",
        "",
        "| Fault | Expected | Ended | Events | Tickets | Model calls made / recorded | Pass | Detail |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for o in outcomes:
        lines.append(
            f"| {o.fault} | `{o.expected}` | `{o.status}` | {' → '.join(o.events)} | {o.tickets} "
            f"| {o.calls_made} / {o.calls_recorded} | {'yes' if o.ok else '**no**'} | {o.detail} |"
        )
    lines += [
        "",
        (
            f"**MCP over stdio** ({timings['calls']} reads): server start {timings['startup_s']} s, "
            f"read median {timings['read_median_ms']} ms, max {timings['read_max_ms']} ms."
        ),
    ]
    return "\n".join(lines) + "\n"


async def _run(args: argparse.Namespace) -> None:
    settings = Settings()
    engine = create_engine(settings)
    try:
        async with AsyncPostgresSaver.from_conn_string(
            checkpoint_url(settings.database_url)
        ) as saver:
            await saver.setup()
            with tempfile.TemporaryDirectory() as tmp:
                env = FaultEnv(create_session_factory(engine), saver, Path(tmp))
                outcomes = [await scenario(env) for scenario in SCENARIOS.values()]
                timings = await mcp_timings(Path(tmp))
    finally:
        await engine.dispose()
    report = render(outcomes, timings, args.date)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report)
    print(report)


def main() -> None:
    parser = argparse.ArgumentParser(description="Inject faults into the workflow (AIENG-53).")
    date = datetime.now(UTC).date().isoformat()
    parser.add_argument("--date", default=date)
    parser.add_argument(
        "--out", type=Path, default=Path(f"evals/workflow/runs/{date}-fault-injection.md")
    )
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
