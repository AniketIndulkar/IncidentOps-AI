# ADR-004: Which workflow steps a model drives, and what the workflow runs on

- **Status:** accepted (W07, 17 Oct 2026)
- **Context tickets:** AIENG-44 to AIENG-48
- **Evidence:** [workflow findings](../../evals/workflow/runs/2026-10-17-workflow-findings.md),
  [W07 design and hypotheses](../roadmap-w07.md)

## Context

Until W06, triage was two calls a client made in order: extraction, then diagnosis. W07 turns it into
a workflow with named steps, a route to "recommend" or "human review", checkpoints that survive a
crash, and room for W08's human approval. Two questions had to be answered with numbers: which steps
should a model decide, and does a pipeline this simple need a framework at all?

## Decision

1. **A fixed graph.** `intake → (retrieve ∥ graph) → (extract ∥ diagnose) → assess → recommend | review`.
   Every incident takes the same steps; no model chooses the next step, a tool, or whether to stop.
2. **Two model steps, each one structured call with checked output.** Extraction (W02 schema and
   review rules) and diagnosis (W04 citations checked against the evidence). Everything else is code.
3. **The route is code.** `assess` returns reason codes (`model_failed`, `no_answer`,
   `unsupported_causes`, `extraction_needs_review`, `sev1`); any code sends the run to review.
4. **The recommendation is code.** Cited runbooks, the on-call engineer, open upstream fixes: facts
   already in the state, assembled without a third model call.
5. **LangGraph 1.2 with `AsyncPostgresSaver`.** It gives explicit state with a step log, a checkpoint
   after every step in the database we already run, resume of a failed run without repeating finished
   steps, and the interrupt W08 needs for approval. The two model calls share a super-step.

## Why not the alternatives

- **An agent loop** (a model with retrieve, graph and diagnose as tools, deciding what to call).
  Nothing in the 12 cases needed a choice: every case used the same steps, and with models that behave
  the fixed path routes all 12 correctly (H24). A loop adds model calls (cost, seconds) and failure
  modes the fixed graph does not have (skipped steps, repeated calls, no termination), which W08 would
  then have to budget and test for.
- **A plain async function.** 6 ms faster per run and the same result (a test checks the two agree),
  but no checkpoints, no resume, no step-level state to pause on for a human. Building those by hand is
  what LangGraph already does; its cost here is +6.4 ms, or +37.7 ms with PostgreSQL checkpoints, on
  runs of 5 to 16 seconds.
- **A model deciding the route.** The route decides whether a person sees the incident before anyone
  acts. Reason codes can be read, counted and tested; a model's choice cannot. In the run the
  extraction suggested SEV2 for an incident recorded as SEV1, twice; the code's `sev1` rule caught it.
- **Running the steps one after another.** Simpler to read, 34% slower at the median (9.85 s against
  6.52 s), with the same result for the same model outputs.

## Consequences

- **A deterministic route is only as stable as its inputs.** Three of twelve cases changed route
  between two real runs because the extraction listed three open questions once and four once, and
  the W02 rule fires at four. That rule is now an input to a route; W08/W09 should decide whether a
  count a model chooses belongs in it.
- Checkpoints hold incident data in the incident's database. LangGraph creates and migrates its own
  four tables at first use; Alembic ignores them. Retention is a W12/W13 concern.
- Super-steps constrain the layout: a branch waits for the slowest node of the step before it. The
  layout is chosen with that in mind, and measured.
- A run that raises is `failed`, keeps its checkpoints, and can be resumed by an operator
  (`POST /incidents/{id}/triage/{run_id}/resume`).
- A node that raises cancels the other nodes of its step. A model call cancelled after it returned is
  not recorded in `llm_runs` (found in testing); W08's fault injection must close that before step
  and spend budgets rely on the record.

## Revisit when

- a case needs a step chosen at run time (asking for logs, querying a ticket system): W08's MCP tool is
  the first candidate, and it would be one bounded, approval-gated step, not a loop;
- route noise from the extraction matters in the W09 evaluation;
- a third model step is proposed: measure it against the deterministic version first.
