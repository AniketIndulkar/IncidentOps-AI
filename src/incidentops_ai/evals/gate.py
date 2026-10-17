"""The eval regression gate (AIENG-68): CI fails when a layer falls below its threshold.

    INCIDENTOPS_DATABASE_URL=<migrated database> uv run python -m incidentops_ai.evals.gate
    uv run python -m incidentops_ai.evals.gate --refresh-baseline   # after a real-model run

It measures retrieval, workflow routing and security now, reads generation from the real-model
run named in evals/baseline.json, and writes evals/dashboard.md. A prompt changed since that run
(the prompt fingerprint differs) fails the gate until the run is repeated and the baseline
refreshed: that is how a prompt change gets evaluated in a CI with no API key.
"""

import argparse
import asyncio
import json
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path

from incidentops_ai.evals.layers import (
    fingerprint,
    generation_layer,
    retrieval_layer,
    security_layer,
    workflow_layer,
)

THRESHOLDS = Path("evals/thresholds.toml")
BASELINE = Path("evals/baseline.json")
DASHBOARD = Path("evals/dashboard.md")
LOWER_IS_BETTER = {"injection_false_positives", "pii_false_positives"}


def check(layers: dict[str, dict], thresholds: dict[str, dict]) -> list[str]:
    """Every metric below (or, for counts of mistakes, above) its threshold."""
    failures = []
    for layer, limits in thresholds.items():
        for metric, limit in limits.items():
            value = layers.get(layer, {}).get(metric)
            if value is None:
                failures.append(f"{layer}.{metric}: not measured")
            elif metric in LOWER_IS_BETTER and value > limit:
                failures.append(f"{layer}.{metric} = {value}, at most {limit} allowed")
            elif metric not in LOWER_IS_BETTER and value < limit:
                failures.append(f"{layer}.{metric} = {value}, at least {limit} needed")
    return failures


def baseline_problem(baseline: dict, current: str) -> str | None:
    if baseline.get("prompts_fingerprint") != current:
        return (
            "prompts changed since the last real-model generation run "
            f"({baseline.get('prompts_fingerprint')} -> {current}): run "
            "`python -m incidentops_ai.evals.generation generate` and `judge`, then "
            "`python -m incidentops_ai.evals.gate --refresh-baseline`"
        )
    return None


def render(layers: dict, thresholds: dict, failures: list[str], extra: dict) -> str:
    lines = [
        f"# Evaluation dashboard ({datetime.now(UTC).date().isoformat()})",
        "",
        (
            "Written by `python -m incidentops_ai.evals.gate`. Generation numbers come from the "
            "recorded real-model run; the rest are measured on every run of the gate."
        ),
        "",
        f"**Gate: {'FAIL' if failures else 'pass'}**" + (":" if failures else ""),
    ]
    lines += [f"- {f}" for f in failures]
    lines += ["", "## Quality", "", "| Layer | Metric | Value | Threshold |", "|---|---|---|---|"]
    for layer, metrics in layers.items():
        for metric, value in metrics.items():
            limit = thresholds.get(layer, {}).get(metric, "–")
            lines.append(f"| {layer} | {metric} | {value} | {limit} |")
    if extra:
        lines += ["", "## Cost and latency", "", "| Source | Measure | Value |", "|---|---|---|"]
        for (source, measure), value in extra.items():
            lines.append(f"| {source} | {measure} | {value} |")
    return "\n".join(lines) + "\n"


def cost_and_latency(baseline: dict) -> dict:
    from incidentops_ai.observability.tracing import estimate_cost_usd

    out = {}
    run_path = baseline.get("generation_run")
    if run_path and Path(run_path).exists():
        run = json.loads(Path(run_path).read_text())
        calls, tokens_in, tokens_out = run.get("generate_usage", [0, 0, 0])
        latencies = sorted(r["latency_ms"] for r in run["records"].values())
        out[("generation run", "model calls")] = calls
        out[("generation run", "tokens in/out")] = f"{tokens_in:,}/{tokens_out:,}"
        out[("generation run", "cost (assumed price)")] = (
            f"${estimate_cost_usd(tokens_in, tokens_out):.3f}"
        )
        out[("generation run", "diagnosis p50 / max")] = (
            f"{latencies[len(latencies) // 2]} ms / {latencies[-1]} ms"
        )
    workflow = Path("evals/workflow/runs/2026-10-17-workflow-benchmark.json")
    if workflow.exists():
        summary = json.loads(workflow.read_text())["summary"]
        median = summary["median_seconds"]
        out[("workflow run (W07)", "median end-to-end")] = f"{median['parallel']:.2f} s"
        out[("workflow run (W07)", "cost per run (assumed)")] = (
            f"${estimate_cost_usd(summary['input_tokens'], summary['output_tokens']) / 24:.4f}"
        )
    return out


async def run_gate(
    sessions, *, retriever=None, prompts=None, dashboard: Path = DASHBOARD
) -> tuple[dict, list[str]]:
    thresholds = tomllib.loads(THRESHOLDS.read_text())
    baseline = json.loads(BASELINE.read_text())
    layers = {
        "retrieval": await retrieval_layer(retriever),
        "workflow": await workflow_layer(sessions),
        "security": security_layer(),
        "generation": generation_layer(Path(baseline["generation_run"])),
    }
    failures = check(layers, thresholds)
    current = fingerprint(prompts) if prompts else fingerprint()
    if problem := baseline_problem(baseline, current):
        failures.append(problem)
    dashboard.write_text(render(layers, thresholds, failures, cost_and_latency(baseline)))
    return layers, failures


async def _main(args) -> int:
    if args.refresh_baseline:
        baseline = {
            "prompts_fingerprint": fingerprint(),
            "generation_run": args.run,
            "recorded": datetime.now(UTC).date().isoformat(),
        }
        BASELINE.write_text(json.dumps(baseline, indent=2) + "\n")
        print(f"baseline: {baseline}")
        return 0
    from incidentops_ai.config import Settings
    from incidentops_ai.db import create_engine, create_session_factory

    engine = create_engine(Settings())
    try:
        layers, failures = await run_gate(create_session_factory(engine))
    finally:
        await engine.dispose()
    print(json.dumps(layers, indent=2))
    for failure in failures:
        print(f"FAIL {failure}", file=sys.stderr)
    return 1 if failures else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="The eval regression gate (AIENG-68).")
    parser.add_argument("--refresh-baseline", action="store_true")
    parser.add_argument("--run", default="evals/generation/runs/2026-10-17-generation.json")
    sys.exit(asyncio.run(_main(parser.parse_args())))


if __name__ == "__main__":
    main()
