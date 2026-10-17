# Week 8 write-up: a person in the loop, memory, budgets and one MCP tool (AIENG-49 to AIENG-53)

Status on 17 Oct 2026: **built and measured**, as a pull request stacked on W07. Design and
hypotheses were committed before any W08 code ([roadmap-w08.md](../roadmap-w08.md)); results in
[the findings](../../evals/workflow/runs/2026-10-17-w08-findings.md), the decision in
[ADR-005](../adr/005-approval-gate-and-tool-trust-boundary.md), the boundary in
[mcp-trust-boundary.md](mcp-trust-boundary.md).

## The result in five lines

1. Every triage run now **stops at an approval gate** with a proposed ticket. Only a recorded
   decision moves it on, once; every transition is a row the database will not let anyone change.
2. Reviewers can leave **notes** for a service. The next diagnosis cites them as `M1`; in a real run a
   note turned 3 of 3 unanswerable incidents into answers, and was never recalled outside its scope.
3. The one tool that writes, **opening a ticket, goes over MCP** and needs an approval signed over
   its exact arguments. Reads take under a millisecond over stdio.
4. **Loops and retries have budgets** and explicit endings: a model repeating a call stops in two
   rounds; a run that keeps failing is abandoned after three resumes.
5. **Five injected faults all end safely and visibly**, after fixing two that the scenarios found.
   705 tests pass (2 opt-in skipped).

## What exists now

```
src/incidentops_ai/workflow/  graph.py: recall, proposed ticket, approval interrupt, act, rejected
                              runner.py: decide (row lock), events, resume budget, step limit
                              faults.py: five fault scenarios and the report
src/incidentops_ai/memory/    orm.py, repository.py (notes, supersede, history, evidence M1..Mn),
                              benchmark.py (the H31 real-model check)
src/incidentops_ai/tickets/   store.py (SQLite), approval.py (HMAC), server.py (MCP), client.py
src/incidentops_ai/llm/tool_loop.py   duplicate-call detection, tool budget, stop_reason
migrations f3c5e7a9b1d4 (workflow_events + append-only trigger), a6e8c0d2f4b6 (memories)
apps/api/app/routes/workflow.py  POST .../decision, GET .../events
apps/api/app/routes/memory.py    GET/POST /memory, POST /memory/{id}/supersede, GET .../history
prompt diagnose_incident/v3 (memory notes as evidence)
```

## Decisions, with why and why not

| Decision | Why | Why not the alternative |
|---|---|---|
| **Gate every run** | The write is the risk, whatever the route | Gate only reviewed runs: a single note can produce a recommendation |
| **Append-only by trigger** | The database enforces it, not just this code | Application-level only |
| **Notes supersede, never edit** | A run points at the exact text it used | A version counter on a mutable row |
| **Signed approvals checked by the server** | A changed argument or a missing decision cannot write | Trust the host alone (still the only defence with a vendor's server) |
| **Write tools never offered to a model** | Prompt injection cannot ask for a write | An agent deciding to open tickets |
| **Ask again on a bad gate answer** | LangGraph replays stored answers | Raise (it poisoned the run) |

## What the numbers and the faults taught

- An interrupt's answer is **state**: stored, replayed, permanent. Validating by raising turns one bad
  answer into a stuck run.
- Cancellation loses writes describing work that already happened, unless the write is shielded.
- Timeouts after a successful write are the reason for idempotency keys; the scenario produced one.
- A reviewer's note is powerful evidence: alone, it moved two incidents from review to recommend.

## What went wrong on the way

- mcp 2.3 installed, not the 1.x API I knew: `FastMCP` is now `MCPServer`, and there is a high-level
  `Client`. A probe settled it before any code.
- A plain `ValueError` from a tool reaches the client as "Error executing tool"; `ToolError` carries
  the message.
- The MCP client's anyio cancel scope was entered in a fixture's setup task and exited in its
  teardown task; the fixture now holds the connection in one task of its own.
- The SDK ran sync tools in worker threads, and SQLite connections belong to one thread: every tool
  is now async.
- The W07 round-limit test repeated the identical call each round; that is now a loop, so the test
  asks for a different call each round.

## What is still weak

- Reviewer identity is the name in the request (W10).
- Nothing flags a recommendation that rests only on a note.
- Runs can wait at the gate forever; no expiry.
- The approval secret mints approvals for whoever holds it.

## Where to look

- `POST /incidents/{id}/triage`, then `POST .../triage/{run_id}/decision` and `GET .../events`.
- `uv run python -m incidentops_ai.workflow.faults` (free) and
  `uv run python -m incidentops_ai.memory.benchmark` (real models) against a scratch database.
