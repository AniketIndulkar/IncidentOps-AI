# Week 7 write-up: the incident workflow (AIENG-44 to AIENG-48)

Status on 17 Oct 2026: **built and measured**, as a pull request stacked on W06. Design, cases,
routing rule and hypotheses were committed before any workflow code
([roadmap-w07.md](../roadmap-w07.md)); the numbers are in
[the findings](../../evals/workflow/runs/2026-10-17-workflow-findings.md) and the decision in
[ADR-004](../adr/004-agentic-vs-deterministic-steps.md).

## The result in five lines

1. Triage is now a **LangGraph workflow**: `intake → (retrieve ∥ graph) → (extract ∥ diagnose) →
   assess → recommend | review`. Two steps call a model; the route and the recommendation are code.
2. **11 of 12 incidents** got the expected route and, when recommended, cited an expected document.
3. Putting the two model calls in the same step cut the median run from **9.85 s to 6.52 s (−34%)**.
4. Every step is checkpointed in PostgreSQL. A run that crashed before `assess` **resumed without
   calling the model again** (with real models on a fresh connection; a test resumes in a separate
   process). LangGraph costs 6 ms per run, 38 ms with checkpoints.
5. The route is deterministic, its inputs are not: three cases changed route between two runs because
   the model listed three open questions once and four once. 655 tests pass (2 opt-in skipped).

## What exists now

```
evals/workflow/          cases.jsonl (12 incidents, expected routes and documents), runs/
src/incidentops_ai/workflow/
  graph.py      state (TypedDict, step log with an append reducer), the eight nodes, both layouts
  assess.py     the routing rule: reason codes, no model
  runner.py     workflow_runs records, start / resume / state, failures recorded with their type
  orm.py + migration e2b4d6f8a1c3    the workflow_runs table
  benchmark.py  task success, both layouts, crash and resume, overhead against a plain function
apps/api/app/routes/workflow.py    POST/GET /incidents/{id}/triage, GET .../{run_id}, POST .../resume
docs/adr/004-agentic-vs-deterministic-steps.md
```

LangGraph's checkpoint tables (`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`,
`checkpoint_migrations`) are created by the saver at first use, not by Alembic, which ignores them.

## Decisions, with why and why not

| Decision | Why | Why not the alternative |
|---|---|---|
| **A fixed graph** | Every case uses the same steps; behaving models route all 12 correctly | An agent loop: more calls, and failure modes (loops, skipped steps) to budget for |
| **Route as reason codes** | Readable, countable, testable; caught a SEV1 the model called SEV2 | A model choosing whether a human sees the incident |
| **LangGraph + PostgreSQL saver** | State, step log, checkpoints, resume, W08 interrupts, in the DB we run | A plain function: 6 ms faster, none of the above |
| **Model calls in one super-step** | LangGraph waits for a whole step; only same-step nodes overlap | Extraction straight after intake (measured: no gain) |
| **Recommendation in code** | The facts are in the state; no new way to be wrong | A third model call |
| **Background connection pool** | The API starts while PostgreSQL is down, as it did before | Connecting at boot (the server would not start) |

## What the numbers taught

- Measure the parallel layout, don't assume it. The first layout looked parallel and saved nothing,
  because of super-steps.
- Extraction, not diagnosis, is the slow call (22 of 24 runs). The saving is roughly one diagnosis.
- "Deterministic routing" is deterministic about its inputs. A threshold on a count the model chose
  (`many_open_questions` at 4) made the same incident route differently on two runs.
- Two of six recommendations named no runbook: the diagnosis cited only postmortems.

## What went wrong on the way

- The first layout gave 0.64 s against 0.66 s sequential with scripted delays: super-steps. Fixed by
  moving extraction into diagnosis's step.
- The full suite caught that the checkpointer stopped the API from starting without a database. Now a
  pool that connects in the background, and `setup()` before the first workflow call.
- `alembic check` wanted to drop LangGraph's tables. Now filtered by name.
- A failing branch cancels its sibling mid-write: the dead connection broke the next session in tests
  (the test engine lacked the app's pre-ping) and the cancelled call's `llm_runs` row is lost (open,
  for W08).
- The resume test first "resumed in a new process" with a new connection in the same process. It now
  starts a real second process.

## What is still weak

- Two runs per case: the variance is seen, not estimated.
- A cancelled model call can go unrecorded (spend and audit under-count).
- Checkpoints hold incident data with no retention policy yet.

## Where to look

- `POST /incidents/{id}/triage`, then `GET /incidents/{id}/triage/{run_id}` for the state and step log.
- `uv run python -m incidentops_ai.workflow.benchmark --overhead` (free) or without the flag (real models).
