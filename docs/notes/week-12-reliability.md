# Week 12 write-up: production reliability and failure engineering (AIENG-69 to AIENG-73)

Status on 17 Oct 2026: **built and measured**, as a pull request stacked on W11. Design and
hypotheses were committed before any W12 code ([roadmap-w12.md](../roadmap-w12.md)); results in
[the findings](../../evals/reliability/runs/2026-10-17-w12-findings.md); the decision in
[ADR-009](../adr/009-reliability-and-degradation.md); operations in [the runbook](../runbook.md);
the [retrospective](reliability-retro.md).

## The result in five lines

1. **Under load, the system waits on the model**: no errors from 1 to 100 concurrent responders,
   triage P95 9.1 s at 100, the two model steps about 95% of every run.
2. **My first bottleneck finding was wrong**: the load generator, not the connection pools; the
   runner now splits clients across processes, and the earlier report says so.
3. **Three failure modes were found by forcing them and fixed**: unbounded retrieval, runs left
   `running` by a dead process, and duplicate tickets from concurrent writes.
4. **Duplicates and restarts are safe**: 50 submissions make one incident, 20 decisions one
   decision, 10 writes one ticket; a server killed mid-run resumes it after restart.
5. **Degradation is visible**: `GET /status` reports FULL, REDUCED or MANUAL, and the runbook says
   what to do in each. 947 tests pass (2 opt-in skipped).

## What exists now

```
src/incidentops_ai/llm/simulated.py        the model stand-in (real latencies and tokens)
src/incidentops_ai/reliability/            load.py (load runner), burst.py, status.py
src/incidentops_ai/retrieval/stack.py      the bm25 stack
workflow: retrieval timeout, recover_stale, advisory lock in act
apps/api/app/routes/status.py              GET /status
docs/runbook.md, docs/notes/reliability-retro.md
evals/reliability/runs/                    load baseline, measurement correction, bursts
```

## Decisions, with why and why not

| Decision | Why | Why not the alternative |
|---|---|---|
| **Simulated model for load** | Real latencies and tokens, no spend | A real-model load (over the cap; measures the provider) |
| **A bound on every dependency** | A hung store held a run indefinitely | Trust the drivers' defaults |
| **Stale-run recovery by age** | Safe with more than one process | Fail every `running` run at startup |
| **Advisory lock around `act`** | Serialises writers across processes | Search-then-create (raced) |
| **Tiers and `/status`** | An operator sees which dependency and what users get | An "up" health check |

## What the work taught

- Measure the measuring tool: one client process was the bottleneck I attributed to the server.
- A failure mode you have not forced is a failure mode you have not seen: all three fixes came from
  tests that broke something on purpose.
- A process cannot record its own death; something else has to notice.
- The approval gate serialised actions in the normal path; the race was in the recovery path.

## What went wrong on the way

- The bottleneck claim, retracted (above).
- A two-worker "halving" that did not reproduce on a second run.
- `set -- $cfg` does not split words in zsh; the first pool comparison never ran.

## What is still weak

- Stale runs are recovered only at startup (the v1.0 must-fix).
- No real provider limits measured; one API process measured.
- Ticket deduplication still leans on the ticket system's search being current.

## Where to look

- `GET /status`, `docs/runbook.md`
- `uv run python -m incidentops_ai.reliability.load` (scratch database with the corpus ingested)
