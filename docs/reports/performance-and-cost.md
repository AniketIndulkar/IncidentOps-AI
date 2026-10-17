# Performance and cost report (v1.0)

How fast a triage is, what it costs, how the system behaves as concurrency grows, and how much
machine it needs. Prices are an assumption ($2.50 per million input tokens, $10 per million
output), not a bill. The monthly picture is in [cost-model.md](../cost-model.md).

## Summary

| Measure | Value | Committed result |
|---|---|---|
| One triage, real model, median end to end | 6.52 s (sequential steps: 9.85 s) | [workflow findings](../../evals/workflow/runs/2026-10-17-workflow-findings.md) |
| Share of a run spent in the two model steps | about 95% | [load results](../../evals/reliability/runs/2026-10-17-load.md) |
| Model cost per triage (estimate) | $0.019 | [load results](../../evals/reliability/runs/2026-10-17-load.md) |
| Triage P95 at 10 / 100 concurrent responders (simulated model) | 8.3 s / 9.1 s, 0 errors | [load results](../../evals/reliability/runs/2026-10-17-load.md) |
| LangGraph overhead per run | +6.4 ms; +37.7 ms with PostgreSQL checkpoints | [workflow findings](../../evals/workflow/runs/2026-10-17-workflow-findings.md) |
| Hybrid retrieval, p95 per query | 248 ms | [stack comparison](../../evals/retrieval/runs/2026-10-16-stack-comparison.md) |
| Memory at idle: API / PostgreSQL / Neo4j | 225–245 / 25–28 / 349 MiB | [W13 findings](../../evals/release/2026-10-17-w13-findings.md) |

## One triage, real model (W07)

**Dataset:** the 12 workflow cases. **Command:** `uv run python -m incidentops_ai.workflow.benchmark`
(24 real runs, about $0.50).

Retrieval, the graph, memory recall and the CMDB run side by side, then extraction and diagnosis
run side by side. That layout took the median from 9.85 s to 6.52 s (−34%) with the same routes
under scripted models. Two real runs of the same incident do not always give the same result (8 of
12 identical): the variation is the model's, not the layout's.

## Under concurrent load (W12)

**Conditions:** the API under uvicorn (one process), PostgreSQL and the load generator on one
laptop; the model **simulated** from the step latencies recorded in W07 (extraction 4.6–9.2 s,
diagnosis 1.1–8.2 s) with the real mean token counts; BM25 retrieval; each responder creates,
triages, reads and approves one incident; at most 25 responders per client process.
**Command:** `INCIDENTOPS_DATABASE_URL=<scratch database with the corpus ingested> uv run python -m incidentops_ai.reliability.load`
(free).

| Responders | Triage P50 / P95 / P99 | Create P95 | Decide P95 | Throughput | Errors |
|---|---|---|---|---|---|
| 1 | 6.4 / 6.4 / 6.4 s | 13 ms | 52 ms | 0.12/s | 0 |
| 10 | 6.4 / 8.3 / 8.3 s | 23 ms | 71 ms | 1.0/s | 0 |
| 50 | 6.3 / 9.5 / 9.6 s | 120 ms | 169 ms | 4.3/s | 0 |
| 100 | 7.7 / 9.1 / 9.3 s | 196 ms | 196 ms | 8.9/s | 0 |

Where the time went, from the traces (mean per run):

| Responders | Run | Outside the steps |
|---|---|---|
| 1 | 6.34 s | 42 ms |
| 10 | 6.64 s | 68 ms |
| 100 | 7.30 s | 402 ms |

Throughput grows linearly because a run spends most of its time waiting on the model. The API's
own overhead (checkpoint writes and the event loop) grows tenfold from 1 to 100 responders: small
next to the model, and the trend to watch. Larger connection pools took 10 to 15% off it; the
sizes are settings, the defaults unchanged.

**A retracted finding:** the first run blamed the database pools for slow creates at 100
responders. Doubling them changed nothing. The cause was the load generator: one Python process
could not drive 100 concurrent requests without queueing them itself (P50 430 ms from one process,
47 ms from four). The superseded report is kept, marked as such:
[measurement](../../evals/reliability/runs/2026-10-17-measurement.md),
[stress (superseded)](../../evals/reliability/runs/2026-10-17-stress.md).

**Limits:** the model is simulated, so a real provider's rate limits and slow tails are not here;
one machine ran everything, so the numbers are relative; one API process was measured; at low
concurrency each percentile is one or a few samples.

## Smaller parts

| Part | Measure | Source |
|---|---|---|
| MCP ticket server | reads 0.64 ms median, 4.8 ms max over 50; starts in 0.3 s | [W08 findings](../../evals/workflow/runs/2026-10-17-w08-findings.md) |
| Attachment extraction | PDF text layer 37 ms; OCR 176–425 ms; vision transcript 2.3–3.5 s | [multimodal benchmark](../../evals/multimodal/runs/2026-10-17-multimodal-benchmark.md) |
| Reranker (off) | +645 ms mean for 20 candidates, +1.3 GB memory | [stack comparison](../../evals/retrieval/runs/2026-10-16-stack-comparison.md) |

## Machine size

Measured on the clean deployment (W13), idle after ingesting the corpus: the API container
225–245 MiB, PostgreSQL 25–28 MiB, Neo4j (empty, default settings) 349 MiB. The API image is
644 MB. Without the graph the stack fits the 1 GB instance of [the Lightsail guide](../deploy-lightsail.md)
with little room; with Neo4j, use 2 GB or more. Memory under load was not measured.

## Cost per triage

A triage makes two model calls. At the W07 run's mean of 2,010 input and 449 output tokens per
call:

```
2 × (2,010 × $2.50 + 449 × $10.00) / 1,000,000 = $0.019
```

Embedding the query adds about $0.000001 (hybrid; nothing with BM25). A run sent to review costs
the same as one that is recommended: both model steps run whatever the route. The exception is a
run with no evidence at all (retrieval failed or the corpus is empty), where diagnosis answers
`no_evidence` without a model call. Monthly figures: [cost-model.md](../cost-model.md).
