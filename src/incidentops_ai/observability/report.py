"""Rebuild a run from its trace, and summarise latency, tokens and cost (AIENG-67).

uv run python -m incidentops_ai.observability.report traces.jsonl            # summary
uv run python -m incidentops_ai.observability.report traces.jsonl --trace ID # one run
"""

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path

from incidentops_ai.observability.tracing import Span


def load(path: Path) -> list[Span]:
    return [Span(**json.loads(line)) for line in path.read_text().splitlines() if line.strip()]


def _children(spans: list[Span]) -> dict[str | None, list[Span]]:
    tree: dict[str | None, list[Span]] = defaultdict(list)
    for span in sorted(spans, key=lambda s: s.start):
        tree[span.parent_id].append(span)
    return tree


def reconstruct(spans: list[Span], trace_id: str) -> str:
    """The run as a tree: every span, its duration and what it recorded."""
    mine = [s for s in spans if s.trace_id == trace_id]
    tree = _children(mine)
    lines = [f"trace {trace_id}: {len(mine)} spans"]

    def walk(parent: str | None, depth: int) -> None:
        for span in tree.get(parent, []):
            a = span.attributes
            detail = []
            if span.kind == "model":
                detail.append(
                    f"{a.get('model')} {a.get('prompt')} {a.get('input_tokens')}/"
                    f"{a.get('output_tokens')} tokens ${a.get('cost_usd', 0):.4f} {a.get('outcome')}"
                )
            elif "outcome" in a:
                detail.append(str(a["outcome"]))
            for key in ("phase", "route", "results", "paused"):
                if key in a:
                    detail.append(f"{key}={a[key]}")
            if span.error:
                detail.append(f"ERROR {span.error}")
            lines.append(
                f"{'  ' * depth}{span.name} [{span.kind}] {span.duration_ms} ms  "
                + "  ".join(detail)
            )
            walk(span.span_id, depth + 1)

    walk(None, 0)
    totals = [s for s in mine if s.kind == "model"]
    lines.append(
        f"model calls {len(totals)}, tokens "
        f"{sum(s.attributes.get('input_tokens') or 0 for s in totals)}/"
        f"{sum(s.attributes.get('output_tokens') or 0 for s in totals)}, cost "
        f"${sum(s.attributes.get('cost_usd') or 0 for s in totals):.4f}"
    )
    return "\n".join(lines)


def summary(spans: list[Span]) -> list[dict]:
    """Per span name: count, p50 and p95 latency, error rate, tokens and cost."""
    groups: dict[str, list[Span]] = defaultdict(list)
    for span in spans:
        groups[span.name].append(span)
    rows = []
    for name, group in sorted(groups.items()):
        durations = sorted(s.duration_ms for s in group)
        p95 = durations[max(0, round(0.95 * len(durations)) - 1)]
        rows.append(
            {
                "name": name,
                "count": len(group),
                "p50_ms": round(statistics.median(durations), 1),
                "p95_ms": p95,
                "error_rate": round(sum(s.status == "error" for s in group) / len(group), 3),
                "input_tokens": sum(s.attributes.get("input_tokens") or 0 for s in group),
                "output_tokens": sum(s.attributes.get("output_tokens") or 0 for s in group),
                "cost_usd": round(sum(s.attributes.get("cost_usd") or 0 for s in group), 4),
            }
        )
    return rows


def render_summary(rows: list[dict]) -> str:
    lines = [
        "| Span | Count | p50 ms | p95 ms | Errors | Tokens in/out | Cost |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['name']} | {r['count']} | {r['p50_ms']} | {r['p95_ms']} | "
            f"{r['error_rate']:.0%} | {r['input_tokens']:,}/{r['output_tokens']:,} | "
            f"${r['cost_usd']:.4f} |"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Read a trace file.")
    parser.add_argument("path", type=Path)
    parser.add_argument("--trace", help="a trace id (a workflow run's id without dashes)")
    args = parser.parse_args()
    spans = load(args.path)
    print(reconstruct(spans, args.trace) if args.trace else render_summary(summary(spans)))


if __name__ == "__main__":
    main()
