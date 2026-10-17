"""Does a reviewer's note change the diagnosis, and only where it belongs? (H31, AIENG-50)

    INCIDENTOPS_DATABASE_URL=<scratch database> uv run python -m incidentops_ai.memory.benchmark

Three incidents the corpus cannot answer, each run through the workflow with real models three
times: with no note; with the same note stored for another service and for another tenant; and with
the note stored for the incident's own service. "Cites M1" means the diagnosis kept a citation of
the recalled note. The database must have the corpus ingested and indexed (W03); runs and notes
are written into it, so use a scratch database.
"""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from incidentops_ai.config import Settings
from incidentops_ai.db import create_engine, create_session_factory
from incidentops_ai.incidents.models import IncidentCreate
from incidentops_ai.incidents.repository import IncidentRepository
from incidentops_ai.llm.factory import build_model_config, build_provider
from incidentops_ai.memory.repository import MemoryRepository
from incidentops_ai.retrieval.factory import build_embedder
from incidentops_ai.retrieval.stack import build_retriever
from incidentops_ai.workflow.benchmark import Counting
from incidentops_ai.workflow.graph import WorkflowDeps
from incidentops_ai.workflow.runner import WorkflowRunner, checkpoint_url

CASES = Path("evals/memory/cases.jsonl")
CONDITIONS = ("no note", "note elsewhere", "own note")


def _cited(state: dict) -> list[str]:
    diagnosis = state.get("diagnosis") or {}
    return [c["evidence_id"] for cause in diagnosis.get("causes", []) for c in cause["citations"]]


async def run(settings: Settings) -> dict:
    engine = create_engine(settings)
    sessions = create_session_factory(engine)
    provider, embedder = build_provider(settings), build_embedder(settings)
    config = build_model_config(settings)
    retriever = build_retriever(settings.retrieval_stack, sessions, embedder, None)
    cases = [json.loads(line) for line in CASES.read_text().splitlines() if line.strip()]
    rows, calls = [], Counting(provider)
    try:
        async with AsyncPostgresSaver.from_conn_string(
            checkpoint_url(settings.database_url)
        ) as saver:
            await saver.setup()
            runner = WorkflowRunner(WorkflowDeps(sessions, calls, config, retriever, None), saver)
            for case in cases:
                for condition in CONDITIONS:
                    async with sessions() as session:
                        notes = MemoryRepository(session)
                        if condition == "note elsewhere":
                            await notes.add(
                                tenant_id="default",
                                service=f"{case['service']}-legacy",
                                text=case["note"],
                                author="Amara Okafor",
                            )
                            await notes.add(
                                tenant_id="acme",
                                service=case["service"],
                                text=case["note"],
                                author="Amara Okafor",
                            )
                        if condition == "own note":
                            await notes.add(
                                tenant_id="default",
                                service=case["service"],
                                text=case["note"],
                                author="Amara Okafor",
                            )
                        created = await IncidentRepository(session).create(
                            IncidentCreate(
                                title=case["title"],
                                description=case["description"],
                                service=case["service"],
                                severity=case["severity"],
                                environment="production",
                            )
                        )
                        await session.commit()
                    run_ = await runner.start(created.incident.id)
                    state = await runner.state(run_.id)
                    rows.append(
                        {
                            "case": case["id"],
                            "condition": condition,
                            "recalled": len(state.get("memories", [])),
                            "status": (state.get("diagnosis") or {}).get("status"),
                            "cited": _cited(state),
                            "route": run_.route,
                            "reasons": run_.reasons,
                        }
                    )
                    print(rows[-1])
    finally:
        await provider.aclose()
        await embedder.aclose()
        await engine.dispose()
    return {
        "model": config.model,
        "rows": rows,
        "calls": calls.calls,
        "input_tokens": calls.input_tokens,
        "output_tokens": calls.output_tokens,
    }


def render(report: dict, date: str) -> str:
    lines = [
        f"# Memory notes with real models ({date})",
        "",
        (
            f"Model `{report['model']}`; {report['calls']} model calls, {report['input_tokens']:,} input "
            f"and {report['output_tokens']:,} output tokens."
        ),
        "",
        "| Case | Condition | Notes recalled | Diagnosis | Cited | Route |",
        "|---|---|---|---|---|---|",
    ]
    for r in report["rows"]:
        route = r["route"] + (f" ({', '.join(r['reasons'])})" if r["reasons"] else "")
        lines.append(
            f"| {r['case']} | {r['condition']} | {r['recalled']} | {r['status']} | "
            f"{', '.join(r['cited']) or '–'} | {route} |"
        )
    own = [r for r in report["rows"] if r["condition"] == "own note"]
    elsewhere = [r for r in report["rows"] if r["condition"] == "note elsewhere"]
    lines += [
        "",
        f"- Own note cited as M1: **{sum('M1' in r['cited'] for r in own)} of {len(own)}**.",
        (
            f"- Note stored elsewhere recalled: {sum(r['recalled'] for r in elsewhere)}; "
            f"cited: {sum('M1' in r['cited'] for r in elsewhere)} of {len(elsewhere)}."
        ),
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    date = datetime.now(UTC).date().isoformat()
    report = asyncio.run(run(Settings()))
    stem = Path(f"evals/memory/runs/{date}-memory-check")
    stem.with_suffix(".json").write_text(json.dumps(report, indent=2) + "\n")
    stem.with_suffix(".md").write_text(render(report, date))
    print(render(report, date))


if __name__ == "__main__":
    main()
