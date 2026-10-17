# Cost model (v1.0)

**These are estimates.** Model prices are an assumption ($2.50 per million input tokens and
$10.00 per million output tokens, the figure used since W06, in
`src/incidentops_ai/observability/tracing.py`), and token counts are the means of recorded real
runs. Check your provider's current prices and replace the two numbers; everything below scales
linearly with them. The measurements behind this page: [performance-and-cost.md](reports/performance-and-cost.md).

## Per operation

| Operation | Model calls | Tokens in / out (mean, recorded) | Estimated cost |
|---|---|---|---|
| Triage (extraction + diagnosis) | 2 | 2 × 2,010 / 2 × 449 | **$0.019** |
| Triage with no evidence at all | 1 | 2,010 / 449 | $0.010 |
| Resume of a failed run | 0 to 2 (finished steps are not repeated) | | $0 to $0.019 |
| Approve or reject | 0 | | $0 |
| Image attachment, vision transcript | 1 | about 600 / 130 | about $0.003 |
| PDF attachment (text layer or OCR) | 0 | | $0 |
| An ops question (text-to-SQL) | 1 | about 740 / 135 | about $0.003 |
| Query embedding (hybrid retrieval) | 1 embedding | about 50 | about $0.000001 |
| Re-indexing the 18-document corpus | embeddings | about 12,000 | about $0.0002 |

Sources: triage from the W07 run (`evals/workflow/runs/2026-10-17-workflow-benchmark.md`, the
token means the W12 simulator replays); attachments from `evals/multimodal/runs/2026-10-17-multimodal-benchmark.md`;
ops questions from `evals/sql/runs/2026-10-17-text-to-sql.md` (23 calls, 17,000 in / 3,100 out);
embeddings at an assumed $0.02 per million tokens.

## Per month

Model spend only, at $0.019 a triage, with one image attachment on a quarter of incidents and
five ops questions a day:

| Incidents a day | Triages a month | Model spend a month |
|---|---|---|
| 10 | 300 | about $6 |
| 50 | 1,500 | about $30 |
| 200 | 6,000 | about $119 |
| 1,000 | 30,000 | about $594 |

Triage is over 95% of it: at 200 incidents a day, triage $114, images $4.50, ops questions $0.45.

The hosting, for one server running everything (see [deploy.md](deploy.md)):

| Setup | Memory needed (measured idle) | Example |
|---|---|---|
| API + PostgreSQL | about 270 MiB | the 1 GB Lightsail instance of [deploy-lightsail.md](deploy-lightsail.md), $7 a month at the time of W01 |
| API + PostgreSQL + Neo4j | about 620 MiB | 2 GB or more; check the provider's current price |

Memory under load was not measured; leave headroom.

## Controlling spend

- **The rate limit is the ceiling.** `INCIDENTOPS_LLM_RATE_LIMIT_PER_MINUTE` (default 10) caps the
  model-calling requests each API process accepts. At the default, one process can start at most
  10 triages a minute: about $0.19 a minute, or about $270 a day if saturated. Lower it to lower the
  ceiling; it is per process, so multiply by the number of processes.
- **Every call is recorded.** `llm_runs` holds each model call with its model, tokens and latency
  (traces carry the estimated cost per call). The daily estimate at the assumed price:

  ```sql
  SELECT date_trunc('day', created_at) AS day, model,
         round((sum(input_tokens) * 2.50 + sum(output_tokens) * 10.00) / 1e6, 2) AS est_usd
  FROM llm_runs GROUP BY 1, 2 ORDER BY 1;
  ```
- **Fallback routes cost what the fallback model costs.** A route like
  `{"diagnosis": ["model-a", "model-b"]}` only calls `model-b` when `model-a` fails with a timeout,
  a rate limit or a provider error; the call is recorded under the model that answered.
- **Evaluation runs are separate spend.** Refreshing the generation baseline after a prompt change
  is 16 calls (about $0.15) plus a judge pass (about $0.04).

## What would change the estimate

- **Longer incidents and more evidence.** Input tokens grow with the description and the five
  retrieved chunks; chunks of 2,000 characters dominate the diagnosis prompt.
- **A different model.** Token counts are `gpt-6-luna`'s; another model's output length differs.
- **Retries.** A timeout or rate limit is retried up to 3 times; a bad reply once. The recorded
  runs needed none, so the figures above have no retry overhead.
