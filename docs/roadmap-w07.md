# W07 roadmap: a LangGraph workflow with explicit state and durable checkpoints (16–20 Nov 2026)

Source: Jira epic **AIENG-8** and tasks **AIENG-44 to AIENG-48**, read 17 Oct 2026. Overall plan:
[ROADMAP.md](ROADMAP.md). Built early, stacked on W06 (branch `feat/w07-langgraph-workflow`).

> **Status:** design written before any workflow code. Decisions are mine (W05 to W13 built back to
> back at the user's request).

## Goal of the week

Turn "call extraction, then call diagnosis" into an inspectable workflow: explicit state, named steps,
deterministic routing where possible, checkpoints that survive a crash, and a measured answer to
"does this need an agent or a framework at all?"

**Exit criterion (from the epic):** a stateful workflow with conditional routing, persistence and
measurable end-to-end task success.

## A probe before writing this (17 Oct)

LangGraph 1.2.16 with `AsyncPostgresSaver` (psycopg 3) on the existing PostgreSQL: two branches ran
concurrently (0.23 s instead of 0.4 s), a node that raised left a checkpoint, and a new process
resumed by running only the failed node. That is the behaviour AIENG-46 asks for.

## The workflow

```
           ┌──────────────── extract (model) ────────────────┐
intake ────┤                                                 ├──▶ assess ──▶ recommend
(determ.)  └─ retrieve (determ.) ┐                           │   (determ.)  (determ.)
              graph (determ.) ───┴──▶ diagnose (model) ──────┘       └────▶ review
```

| Node | Kind | Reads | Writes | On failure |
|---|---|---|---|---|
| `intake` | deterministic | incident id | incident, attachment texts | run fails (no incident, nothing to do) |
| `extract` | **model** (W02 extraction) | incident | extraction, its review flags | records the failure; the run continues to review |
| `retrieve` | deterministic (W04 hybrid stack) | incident text, service hint | evidence chunks | retrieval error recorded; diagnosis gets no corpus evidence |
| `graph` | deterministic (W05) | service | service context | graph unavailable recorded; context empty |
| `diagnose` | **model** (W04 cited diagnosis) | evidence, attachments | diagnosis | records the failure; the run continues to review |
| `assess` | deterministic | everything above | route, reasons | – |
| `recommend` | deterministic | diagnosis, context | next steps built from cited runbooks, on-call, open fixes | – |
| `review` | deterministic (W08 adds the human) | reasons | status `needs_review` | – |

**Why extraction runs beside retrieval and diagnosis.** Diagnosis does not need extraction's output
(it works from the incident text and the evidence); only the assessment does. Both are model calls of
several seconds, so running them concurrently is where parallelism *actually helps*. Retrieval and the
graph lookup run concurrently too, but they take a few hundred milliseconds.

## Routing rule (deterministic, fixed now)

`assess` sends the run to **review** if any of these hold, and to **recommend** otherwise:

| Reason code | When |
|---|---|
| `model_failed` | extraction or diagnosis did not succeed |
| `no_answer` | the diagnosis is `no_answer` |
| `unsupported_causes` | the model proposed causes that failed the citation check (`dropped_causes > 0`) |
| `extraction_needs_review` | the W02 review rules flag the extraction |
| `sev1` | the incident is SEV1: high impact, a person sees it before anyone acts |

No model decides the route. Every reason is a code a reviewer can read.

## Decisions made by me

| Decision | Choice | Why |
|---|---|---|
| Framework | **LangGraph 1.2** with `AsyncPostgresSaver` | Explicit state and edges, checkpoints and resume, and interrupts for W08's human approval; persistence in the database we already run |
| Checkpoint tables | Created by the saver's own `setup()` at startup, not Alembic | The saver versions its tables itself; mixing them into our migrations would fight it |
| State | Plain JSON-friendly values (dicts, lists, strings), a step log with an append reducer | Checkpoints serialise cleanly; concurrent branches append without conflict |
| Runs | A `workflow_runs` table: one row per run (incident, status, route, reasons, error), thread id = run id | The checkpointer holds state; the table is what an API lists and an operator queries |
| API | `POST /incidents/{id}/triage` (run), `GET .../triage/{run_id}`, `POST .../triage/{run_id}/resume` | Resume is the operator's tool after a crash |
| Recommendation | Deterministic: cited runbooks, the on-call engineer, open upstream fixes | A second model call would add cost and a new way to be wrong; the facts are already there |

## Evaluation set, written before any workflow code

`evals/workflow/cases.jsonl`: 12 incidents, each with the expected route and, for recommended runs,
documents at least one of which must be cited. Some carry attachments from the W06 benchmark; two are
off-corpus; two are SEV1; one is designed so the extraction is vague. Seven should be recommended and five
reviewed.

**Task success** = the route is the expected one **and**, if the run recommends, at least one
expected document is cited.

## Hypotheses

- **H24.** The deterministic routing sends every case to its expected route when the model calls
  behave (checked with scripted models: 12 of 12).
- **H25.** With the real models, task success is at least 10 of 12.
- **H26.** Running extraction beside retrieval and diagnosis cuts the median end-to-end time by at
  least 30% against running them one after another, with the same routes and citations.
- **H27.** A run that crashes after diagnosis resumes in a new process without repeating any model call.
- **H28.** LangGraph's own overhead (the same steps as a plain async function, scripted models) is under
  50 ms per run.

## Day by day

- **AIENG-44** this document and the case set.
- **AIENG-45** state, nodes, edges and routing on the existing functions; scripted cases traverse
  their expected paths.
- **AIENG-46** Postgres checkpoints, the runs table, crash and resume in a new process.
- **AIENG-47** the concurrent branches, measured against a sequential build; correctness unchanged.
- **AIENG-48** real-model run of the 12 cases, findings, **ADR-004: agentic vs deterministic steps**.

## Risks

1. **A framework for a pipeline.** Five of eight steps are deterministic. The ADR must say what
   LangGraph buys that a function does not, with numbers.
2. **Checkpoints contain incident data.** They live in the same database and tenant as the incident;
   retention is a W12/W13 concern.
3. **Twelve cases**, one author.
