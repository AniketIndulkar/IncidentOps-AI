"""A concurrent load test against the real API process (AIENG-69).

    INCIDENTOPS_DATABASE_URL=<scratch database, migrated, corpus ingested> \
        uv run python -m incidentops_ai.reliability.load

Starts `uvicorn app.main:app` with the simulated model (real recorded latencies and token counts),
BM25 retrieval and a trace file, then runs virtual responders: each creates an incident, triages
it, reads the run and approves it. Levels of 1, 5, 10 and 20 concurrent responders, a few
iterations each. Reports P50/P95/P99 per endpoint and per triage, throughput, error rate and an
estimated cost per triage, and reads the traces for where the time went as concurrency grew.
"""

import argparse
import asyncio
import json
import os
import socket
import statistics
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

from incidentops_ai.llm.simulated import TOKENS
from incidentops_ai.observability.report import load as load_spans
from incidentops_ai.observability.tracing import estimate_cost_usd
from incidentops_ai.workflow.benchmark import load_cases

TOKEN = "load-test-token-0123456789"
LEVELS = (1, 5, 10, 20)


@dataclass
class Sample:
    level: int
    user: int
    endpoint: str
    status: int
    ms: float
    start: float


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(database_url: str, trace_file: Path, port: int) -> subprocess.Popen:
    env = {
        **os.environ,
        "INCIDENTOPS_DATABASE_URL": database_url,
        "INCIDENTOPS_API_TOKEN": TOKEN,
        "INCIDENTOPS_LLM_PROVIDER": "simulated",
        "INCIDENTOPS_LLM_MODEL": "simulated",
        "INCIDENTOPS_RETRIEVAL_STACK": "bm25",
        "INCIDENTOPS_LLM_RATE_LIMIT_PER_MINUTE": "1000000",
        "INCIDENTOPS_TRACE_FILE": str(trace_file),
        "PYTHONPATH": "apps/api",
    }
    for name in ("INCIDENTOPS_JIRA_URL", "INCIDENTOPS_TICKETS_DB", "INCIDENTOPS_CMDB_URL"):
        env.pop(name, None)
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        env=env,
    )


async def wait_until_up(base: str, timeout_s: float = 30) -> None:
    deadline = time.monotonic() + timeout_s
    async with httpx.AsyncClient() as client:
        while time.monotonic() < deadline:
            try:
                if (await client.get(f"{base}/health")).status_code == 200:
                    return
            except httpx.TransportError:
                pass
            await asyncio.sleep(0.2)
    raise RuntimeError("the API did not start")


async def responder(client: httpx.AsyncClient, level: int, user: int, iterations: int, samples):
    cases = load_cases()

    async def call(endpoint: str, method: str, url: str, **kwargs) -> httpx.Response:
        started = time.perf_counter()
        response = await client.request(method, url, **kwargs)
        samples.append(
            Sample(
                level,
                user,
                endpoint,
                response.status_code,
                (time.perf_counter() - started) * 1000,
                time.time(),
            )
        )
        return response

    for i in range(iterations):
        case = cases[(user + i) % len(cases)]
        body = {
            k: getattr(case, k)
            for k in ("title", "description", "service", "severity", "environment")
        }
        created = await call("create", "POST", "/incidents", json=body)
        if created.status_code != 201:
            continue
        incident = created.json()["id"]
        triage = await call("triage", "POST", f"/incidents/{incident}/triage")
        if triage.status_code != 200:
            continue
        run = triage.json()["run"]["id"]
        await call("read", "GET", f"/incidents/{incident}/triage/{run}")
        await call(
            "decide",
            "POST",
            f"/incidents/{incident}/triage/{run}/decision",
            json={"decision": "approve"},
        )


def _pct(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, max(0, round(q * len(ordered)) - 1))], 1)


def summarise(samples: list[Sample], windows: dict[int, tuple[float, float]]) -> list[dict]:
    rows = []
    for level, (begin, end) in windows.items():
        mine = [s for s in samples if s.level == level]
        for endpoint in ("create", "triage", "read", "decide"):
            ms = [s.ms for s in mine if s.endpoint == endpoint]
            if not ms:
                continue
            errors = sum(s.status >= 400 for s in mine if s.endpoint == endpoint)
            rows.append(
                {
                    "level": level,
                    "endpoint": endpoint,
                    "n": len(ms),
                    "p50_ms": _pct(ms, 0.5),
                    "p95_ms": _pct(ms, 0.95),
                    "p99_ms": _pct(ms, 0.99),
                    "errors": errors,
                }
            )
        triages = sum(1 for s in mine if s.endpoint == "decide" and s.status < 400)
        rows.append(
            {
                "level": level,
                "endpoint": "throughput",
                "n": triages,
                "per_s": round(triages / (end - begin), 2),
            }
        )
    return rows


CRITICAL_PATH = ("intake", "extract", "assess", "recommend", "review", "approval")


def bottleneck(spans, windows: dict[int, tuple[float, float]]) -> list[dict]:
    """Per level: the mean time of each step, and of the run outside its critical-path steps."""
    rows = []
    for level, (begin, end) in windows.items():
        runs = [
            s
            for s in spans
            if s.name == "workflow.run"
            and s.attributes.get("phase") == "start"
            and begin <= s.start <= end
        ]
        by_trace = {}
        for s in spans:
            if s.kind == "step" and begin <= s.start <= end + 60:
                by_trace.setdefault(s.trace_id, {})[s.attributes.get("node")] = s.duration_ms
        step_means = {}
        for node in (
            "intake",
            "retrieve",
            "graph",
            "recall",
            "cmdb",
            "extract",
            "diagnose",
            "assess",
            "recommend",
            "review",
        ):
            values = [steps[node] for steps in by_trace.values() if node in steps]
            if values:
                step_means[node] = round(statistics.mean(values), 1)
        outside = []
        for run in runs:
            steps = by_trace.get(run.trace_id, {})
            first = steps.get("intake", 0) + max(
                steps.get(n, 0) for n in ("retrieve", "graph", "recall", "cmdb")
            )
            models = max(steps.get("extract", 0), steps.get("diagnose", 0))
            tail = steps.get("assess", 0) + steps.get("recommend", steps.get("review", 0))
            outside.append(run.duration_ms - first - models - tail)
        rows.append(
            {
                "level": level,
                "runs": len(runs),
                "steps_ms": step_means,
                "outside_steps_ms": round(statistics.mean(outside), 1) if outside else None,
                "run_ms": round(statistics.mean(r.duration_ms for r in runs), 1) if runs else None,
            }
        )
    return rows


async def run(database_url: str, out: Path, iterations: int, levels=LEVELS) -> dict:
    trace_file = out.parent / f"{out.stem}-traces.jsonl"
    trace_file.unlink(missing_ok=True)
    port = _free_port()
    server = start_server(database_url, trace_file, port)
    base = f"http://127.0.0.1:{port}"
    samples: list[Sample] = []
    windows: dict[int, tuple[float, float]] = {}
    try:
        await wait_until_up(base)
        limits = httpx.Limits(max_connections=100)
        async with httpx.AsyncClient(
            base_url=base, timeout=120, limits=limits, headers={"Authorization": f"Bearer {TOKEN}"}
        ) as client:
            for level in levels:
                begin = time.time()
                await asyncio.gather(
                    *(responder(client, level, u, iterations, samples) for u in range(level))
                )
                windows[level] = (begin, time.time())
                print(f"level {level}: {sum(1 for s in samples if s.level == level)} requests")
    finally:
        server.terminate()
        server.wait(timeout=10)
    spans = load_spans(trace_file) if trace_file.exists() else []
    return {
        "date": datetime.now(UTC).date().isoformat(),
        "conditions": {
            "levels": levels,
            "iterations_per_user": iterations,
            "model": "simulated (W07 latencies)",
            "retrieval": "bm25",
            "server": "uvicorn, 1 process",
            "host": "one laptop: load, API, PostgreSQL",
            "tokens_per_call": TOKENS,
        },
        "endpoints": summarise(samples, windows),
        "bottleneck": bottleneck(spans, windows),
        "cost_per_triage_usd": round(2 * estimate_cost_usd(*TOKENS), 4),
        "samples": [asdict(s) for s in samples],
    }


def render(report: dict) -> str:
    c = report["conditions"]
    lines = [
        f"# Load test baseline ({report['date']})",
        "",
        (
            f"Conditions: {c['server']}; model {c['model']}; retrieval {c['retrieval']}; "
            f"{c['iterations_per_user']} iterations per responder; {c['host']}."
        ),
        (
            f"Estimated cost per triage: ${report['cost_per_triage_usd']} (two calls at the recorded "
            "token counts, assumed price)."
        ),
        "",
        "| Responders | Endpoint | n | P50 ms | P95 ms | P99 ms | Errors |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in report["endpoints"]:
        if r["endpoint"] == "throughput":
            lines.append(
                f"| {r['level']} | **throughput** | {r['n']} triages | {r['per_s']}/s | | | |"
            )
        else:
            lines.append(
                f"| {r['level']} | {r['endpoint']} | {r['n']} | {r['p50_ms']} | "
                f"{r['p95_ms']} | {r['p99_ms']} | {r['errors']} |"
            )
    lines += [
        "",
        "## Where the time went (mean per run, from traces)",
        "",
        "| Responders | Runs | Run ms | Outside the steps ms | Slowest steps |",
        "|---|---|---|---|---|",
    ]
    for b in report["bottleneck"]:
        top = sorted(b["steps_ms"].items(), key=lambda kv: -kv[1])[:4]
        lines.append(
            f"| {b['level']} | {b['runs']} | {b['run_ms']} | {b['outside_steps_ms']} | "
            + ", ".join(f"{k} {v}" for k, v in top)
            + " |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Load test the API (AIENG-69).")
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument("--levels", default="1,5,10,20", help="comma-separated responders")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(f"evals/reliability/runs/{datetime.now(UTC).date()}-load.json"),
    )
    args = parser.parse_args()
    database_url = os.environ["INCIDENTOPS_DATABASE_URL"]
    levels = tuple(int(x) for x in args.levels.split(","))
    report = asyncio.run(run(database_url, args.out, args.iterations, levels))
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    args.out.with_suffix(".md").write_text(render(report))
    print(render(report))


if __name__ == "__main__":
    main()
