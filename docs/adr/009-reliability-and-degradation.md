# ADR-009: Bounded dependencies, recoverable runs, serialised writes, and visible degradation

- **Status:** accepted (W12, 17 Oct 2026)
- **Context tickets:** AIENG-69 to AIENG-73
- **Evidence:** [W12 findings](../../evals/reliability/runs/2026-10-17-w12-findings.md),
  [runbook](../runbook.md), [retrospective](../notes/reliability-retro.md), [W12 design](../roadmap-w12.md)

## Context

Until W12 the system had been tested one request at a time. Production means concurrent users,
dependencies that fail at the worst moment, and processes that die mid-run.

## Decision

1. **Every dependency call is bounded and ends in a named state**: retrieval 5 s
   (`RetrievalTimeout`), CMDB 3 s, Jira 5 s, status checks 2 s, the model through the gateway's
   timeouts and breaker. A failure becomes a reason on the run (review), never a guess.
2. **A run a dead process left behind is recoverable**: runs `running` for longer than
   `INCIDENTOPS_STALE_RUN_AFTER_S` (900 s) are marked `failed` (`StaleRun`) and resume from their
   checkpoint, without repeating finished steps.
3. **Irreversible actions are serialised per run across processes** by a PostgreSQL advisory lock,
   on top of the idempotency (run label or key) each ticket system already has.
4. **Degradation is visible**: `GET /status` reports FULL, REDUCED or MANUAL and each dependency's
   state; `docs/runbook.md` says what users see and what the operator does in each case.
5. **Load is measured with the model simulated from real recordings**, and with a load generator
   split across processes.

## Why not the alternatives

- **No timeouts, trust the drivers.** A hung vector store held a run indefinitely in the test.
- **Mark every `running` run failed at startup.** With more than one process, that would fail runs
  another process is still driving; the age threshold avoids it.
- **Rely on the ticket system's idempotency alone.** A Jira-style API has none; search-then-create
  raced ten ways in the test.
- **A health endpoint that only says "up".** An operator needs to know which dependency, and what
  users now get.
- **A real-model load test.** About $0.02 a triage: a few hundred triages would pass the spend cap,
  and it would measure the provider more than the system.

## Consequences

- A run held up longer than the threshold by a live process would be marked failed (and resumable)
  by a restart; the threshold is well above the model timeouts.
- `act` holds a database transaction (and its connection) for the duration of the ticket write.
- The pool sizes are settings; the defaults are unchanged.

## Revisit when

- A real provider's limits are measured: set the gateway's per-model rate limit from them.
- More than one API process runs: re-run the load test with several, and move stale-run recovery
  to a periodic sweep (already the v1.0 must-fix).
