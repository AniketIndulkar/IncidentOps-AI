# Reports (v1.0)

Three reports, each built from results committed in `evals/`. Every number in them names its
dataset, the command that reproduces it and the file that holds the result. None was re-run for
v1.0: the code those runs measured is the code being released (scope was frozen before these were
written, and no prompt has changed since the recorded generation run, which the CI gate checks).

| Report | What it answers |
|---|---|
| [evaluation.md](evaluation.md) | How often each layer gets it right: extraction, retrieval, generation and its judge, graph, attachments, workflow, memory, text-to-SQL |
| [performance-and-cost.md](performance-and-cost.md) | Latency of one triage, behaviour from 1 to 100 concurrent responders, machine size, cost per triage |
| [security.md](security.md) | Every attack tried against the threat model, and what held |

Monthly cost estimates: [cost-model.md](../cost-model.md). What v1.0 cannot do or has not shown:
[known-limitations.md](../known-limitations.md).

## Free or paid

| Free (no model calls) | Paid (real model; spend as recorded) |
|---|---|
| `pytest` (958 tests, including 170 security tests: `-m redteam`) | `extraction.run_samples` (10 calls) |
| `evals.gate` (retrieval, workflow routes, SQL, PII, injection; reads the recorded generation run) | `evals.generation generate` (16 calls, about $0.15) and `judge` |
| `workflow.benchmark --overhead` | `workflow.benchmark` (24 runs, about $0.50) |
| `graph.benchmark` (needs Neo4j) | `memory.benchmark` (about $0.15) |
| `reliability.load`, `reliability.burst` (simulated model) | `ops.benchmark` (about $0.07) |
| `retrieval.stack_benchmark` (fractions of a cent: query embeddings) | `security.benchmark` (about $0.12) |
| | `multimodal.benchmark` (vision calls) |

All commands are `uv run python -m incidentops_ai.<module>`; the ones that use a database take
`INCIDENTOPS_DATABASE_URL` and expect the corpus ingested (`retrieval.ingest data/corpus`). Paid
runs need `OPENAI_API_KEY` and `INCIDENTOPS_LLM_MODEL`.

## Reading them honestly

- The sets are small (3 to 30 cases) and single-author (the assistant that built the system). A
  result like "8/8" says the design works on the cases written to probe it, not how often it works
  on real incidents.
- Two hypotheses were refuted and are reported as such: the judge's agreement with labels (H46)
  and the attachment router's choice (H23). The first bottleneck finding of W12 was wrong and is
  kept, marked superseded.
- Prices are assumptions ($2.50 / $10 per million tokens), not a bill.
