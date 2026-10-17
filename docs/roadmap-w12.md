# W12 roadmap: production reliability and failure engineering (21–25 Dec 2026)

Source: Jira epic **AIENG-13** and tasks **AIENG-69 to AIENG-73**, read 17 Oct 2026. Overall plan:
[ROADMAP.md](ROADMAP.md). Built early, stacked on W11 (branch `feat/w12-reliability-load`).

> **Status, 17 Oct 2026: built and measured.** Design written before any W12 code; decisions are
> mine (W05 to W13 built back to back at the user's request). Everything ran locally.
>
> **Result:** H50 to H53 hold. No errors up to 100 concurrent responders; the model is the
> bottleneck. The first bottleneck finding (the connection pools) was the load generator's and was
> retracted. Forcing failures found three faults, now fixed: unbounded retrieval, runs left
> `running` by a dead process, and duplicate tickets from concurrent writes. Findings:
> [w12-findings](../evals/reliability/runs/2026-10-17-w12-findings.md); decision:
> [ADR-009](adr/009-reliability-and-degradation.md); [runbook](runbook.md).

## Goal of the week

Treat IncidentOps as a production system: measure it under concurrent load, break each dependency
on purpose, hammer it with duplicates and restarts, and write down what an operator does when each
part fails.

**Exit criterion (from the epic):** failure-injection tests, load results, graceful degradation,
recovery behaviour, and measured P50/P95/P99 latency, error rate and cost.

## AIENG-69: load test and baseline

- **The model is simulated, faithfully.** A real-model load test would cost about $0.02 per triage
  and pass the $5 cap within a few hundred requests. `INCIDENTOPS_LLM_PROVIDER=simulated` swaps in a
  provider that answers valid structured replies after a latency drawn from the **real step timings
  recorded in W07** (extract 4.6–9.2 s, diagnose 1.1–8.2 s) and reports the **real average token
  counts**, so cost per request is estimated from what real runs used. Everything else is real: the
  API process (uvicorn), PostgreSQL, checkpoints, retrieval, the graph, the audit trail.
- **Retrieval without embeddings**: a `bm25` retrieval stack (DB-backed BM25, W04) so the load
  needs no embedding calls.
- **Scenario** (`python -m incidentops_ai.reliability.load`): each virtual responder creates an
  incident, triages it, reads the run, and approves it; at 1, 5, 10 and 20 concurrent responders,
  for a fixed number of iterations.
- **Measured**: P50/P95/P99 per endpoint and end to end, throughput, error rate, cost per triage.
  The server writes traces; the **bottleneck** is read off them (time in each span against wall
  time), not guessed.

## AIENG-70: dependency failures

Each dependency is broken while a run is in flight; each must end in a **named state**, and no
response may contain a recommendation built on missing information (**no silent wrong answer**):

| Dependency | Failure | Expected |
|---|---|---|
| Model | all routes failing | `model_failed` → review; no causes shown |
| Model | primary failing | fallback answers; the run records it |
| Vector store / retrieval | slow (over a new 5 s retrieval timeout) or erroring | `retrieval_error` → no evidence → `no_answer` → review |
| Database | down at request time | `503 database_unavailable` |
| Database | down mid-run | the run cannot record its end, so it is left `running`: **a new stale-run recovery marks it `failed`** after a timeout, and it resumes |
| Graph (Neo4j) | unavailable | `graph_status: unavailable`, recommendation without on-call |
| CMDB | unavailable or invalid | `integration_degraded` → review |
| Ticket system | timeout after writing | `failed`; resume finds the ticket (W08) |

## AIENG-71: concurrency, idempotency, restart

- 50 concurrent creations with one idempotency key → one incident.
- 20 concurrent decisions on one run → one decision, 19 × 409.
- Repeated and concurrent ticket writes for one run → one ticket.
- **The server process is killed (SIGKILL) during a triage**, restarted, and the run recovered and
  resumed: it completes, and no model step that had finished is called again.

## AIENG-72: degradation tiers and the runbook

| Tier | When | What users get |
|---|---|---|
| **FULL** | every dependency healthy | triage with recommendations and tickets |
| **REDUCED** | retrieval, graph, CMDB or ticket system degraded | runs still complete but go to review with a named reason; nothing is invented |
| **MANUAL** | database down, or every model route failing | no automatic triage; operators work incidents by hand from the runbook |

`GET /status` (any authenticated caller) reports the tier and each dependency's state from cheap
checks (a database ping, the gateway's breakers, the graph, the CMDB, the ticket server).
`docs/runbook.md` gives, per dependency and tier, what the user sees and the operator's first
five minutes.

## AIENG-73: retrospective

`docs/notes/reliability-retro.md`: the week's results, the top five reliability risks, and one
change chosen for v1.0.

## Hypotheses

- **H50.** At 10 concurrent responders, triage P95 is under 15 s with no errors, and the bottleneck
  is visible in the traces.
- **H51.** Every row of the dependency table ends in its named state, and no response carries a
  recommendation built on what was missing.
- **H52.** Duplicates and concurrency produce exactly one incident, one decision and one ticket; a
  killed server's run resumes after restart without repeating a finished model step.
- **H53.** `/status` reports the right tier under each injected failure.

## Day by day

- **AIENG-69** this document; simulated provider, `bm25` stack, the load runner, the baseline.
- **AIENG-70** retrieval timeout, stale-run recovery, the dependency failure tests.
- **AIENG-71** concurrency tests, the kill-and-restart test.
- **AIENG-72** `/status`, tiers, the runbook.
- **AIENG-73** findings, the retro, ADR-009.

## Risks

1. **A simulated model** gets the latency and token shape right but not the variance of a real
   provider under load (its own rate limits and slow tails). The real provider's limits are a
   separate question for deployment.
2. **One machine** runs the load generator, the API and PostgreSQL; numbers are relative.
3. **Stale-run recovery** must not fail a run that is merely slow: its timeout is longer than any
   run's total model timeout.
