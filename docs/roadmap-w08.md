# W08 roadmap: a person in the loop, memory, budgets and one MCP tool (23–27 Nov 2026)

Source: Jira epic **AIENG-9** and tasks **AIENG-49 to AIENG-53**, read 17 Oct 2026. Overall plan:
[ROADMAP.md](ROADMAP.md). Built early, stacked on W07 (branch `feat/w08-hitl-memory-mcp`).

> **Status, 17 Oct 2026: built and measured.** Design written before any W08 code; decisions are
> mine (W05 to W13 built back to back at the user's request). The `v0.2` tag (AIENG-53) is the
> user's call, not mine, and is not made.
>
> **Result:** H29 to H34 hold. The gate held against every attempt; a reviewer's note turned 3 of 3
> unanswerable incidents into cited answers and was never recalled outside its scope; five faults
> ended safely, after fixing two the scenarios found (a malformed gate answer replayed forever, and
> cancelled model calls going unrecorded). Findings:
> [w08-findings](../evals/workflow/runs/2026-10-17-w08-findings.md); decision:
> [ADR-005](adr/005-approval-gate-and-tool-trust-boundary.md).

## Goal of the week

Make the W07 workflow safe to let act: nothing is written to another system without a person's
recorded decision, what people teach it is remembered without rewriting history, nothing can loop or
retry without limit, and the one tool that writes sits behind an explicit trust boundary.

**Exit criterion (from the epic):** a recoverable agent workflow with an explicit trust boundary and
an approval-gated tool action.

## The workflow after W08

```
intake ─┬─ retrieve ─┐
        ├─ graph ────┼─▶ extract ∥ diagnose ─▶ assess ─┬─▶ recommend ─┐
        └─ recall ───┘   (model)     (model)            └─▶ review ────┴─▶ approval ─┬─▶ act ──▶ END
                                                                       (interrupt) └─▶ rejected ─▶ END
```

- **recall** (new, deterministic): the tenant's current memory notes for the incident's service.
- **recommend / review** now also draft the **proposed action**: a ticket in the team's ticket system
  ("Apply runbook …" when recommended, "Investigate: reasons …" when reviewed).
- **approval** (new): a LangGraph `interrupt`. The run stops with status `awaiting_approval` and the
  checkpoint holds everything a reviewer needs. Only a decision through the API resumes it.
- **act** (new): with an approval, creates the ticket through the MCP ticket server (W08's tool).
- **rejected** (new): records the rejection; nothing is written.

### Run statuses

| Status | Meaning | Next |
|---|---|---|
| `running` | steps executing | any below |
| `awaiting_approval` | paused at the gate | a decision |
| `actioned` | approved, ticket created (or found from an earlier attempt) | – |
| `approved` | approved, no ticket system configured, nothing written | – |
| `rejected` | a person said no; nothing written | – |
| `failed` | a step raised; checkpoints kept | `resume` (up to the budget) |
| `abandoned` | failed and out of resume budget | – |

## AIENG-49: the approval gate

- `POST /incidents/{id}/triage/{run_id}/decision` with `{"decision": "approve" | "reject",
  "reviewer": "...", "comment": "...", "memory": "..."?}`. 409 unless the run is
  `awaiting_approval`; the run row is locked while the decision is applied, so two concurrent
  decisions cannot both pass.
- The W07 `resume` stays for **failed** runs only: it cannot push a paused run past the gate.
- **Audit**: a `workflow_events` table, one row per transition (`started`, `awaiting_approval`,
  `decided`, `action_executed`, `failed`, `resumed`, `abandoned`, `completed`), with the actor
  (`system` or the reviewer) and a small JSON payload. **Append-only, enforced by the database**: a
  trigger rejects `UPDATE` and `DELETE`. `GET .../triage/{run_id}/events` lists them.
- Reviewer identity is the name in the request: there is one API token until W10 adds
  per-user access. Recorded as a risk, not solved here.

## AIENG-50: memory

- A `memories` table: tenant, service, text, author, the run it came from (if any), and
  `supersedes` (the note it replaces). **Append-only, same trigger.** A correction is a new note
  that supersedes the old one; withdrawing a note is a superseding row with no text. The *current*
  notes are those no row supersedes.
- Written explicitly: by a reviewer with their decision (`memory` field), or by
  `POST /memory` and `POST /memory/{id}/supersede`. Read with `GET /memory?service=` (current) and
  `GET /memory/{id}/history`.
- Used by `recall`: up to three current notes for the tenant and service become evidence `M1..M3`
  in the diagnosis (prompt `diagnose_incident/v3`), cited like any other evidence.
- **Why this cannot rewrite history:** a run records the ids of the notes it recalled, notes never
  change, and a resumed run uses the notes in its checkpoint, not the current ones. A later
  correction changes later runs only.
- Scope: tenant **and** service. A note about `orders-api` is never recalled for `checkout-api`, and
  never across tenants.

## AIENG-51: budgets and loops

- **Tool loop (W02's `run_tool_loop`, the one model-driven loop in the product):** a budget of rounds
  and of tool calls, and **duplicate-call detection**: a call with the same tool and the same
  canonical arguments as an earlier one in the loop is not run again; the model is told it is a
  duplicate. A round that asks only for duplicates stops the loop with `stop_reason =
  "loop_detected"`. Every stop has an explicit reason (`answered`, `model_failed`, `empty_answer`,
  `round_limit`, `tool_budget`, `loop_detected`) and the executions list is the trace.
- **Workflow:** an explicit LangGraph `recursion_limit` (max steps) and a **resume budget** (3 per
  run): a run that fails again after its last allowed resume becomes `abandoned`, with an event.
- **Duplicate actions:** the ticket is created with the run id as its idempotency key; the server
  returns the existing ticket for a repeated key, and `act` reports `created: false`.

## AIENG-52: one MCP tool and its trust boundary

- `incidentops_ai.tickets`: a small ticket system of our own, standing in for Jira, served over
  **MCP (stdio)** with the official Python SDK. Tools: `search_tickets` and `get_ticket` (read),
  `create_ticket` (write). Storage: SQLite in the server process (it is "another system").
- **The write needs an approval token.** The host signs `(run id, decision id, reviewer, action
  fingerprint)` with a secret the model never sees; the server recomputes the signature over the
  arguments it actually received and refuses anything else. So a prompt-injected model, a bug that
  skips the gate, or a tampered argument cannot create a ticket.
- Host, client and server responsibilities are written down in
  [notes/mcp-trust-boundary.md](notes/mcp-trust-boundary.md). With a third-party MCP server that does
  not check our tokens, only the host gate remains; the note says so.
- The app starts the server as a subprocess when `INCIDENTOPS_TICKETS_DB` and
  `INCIDENTOPS_APPROVAL_SECRET` are set; without them an approval ends `approved` with nothing
  written.

## AIENG-53: fault injection

Each fault is a scenario with an expected end state, run against the real database and the real MCP
server with scripted models; a report lists status, events, tickets and recorded model calls:

| Fault | Expected end |
|---|---|
| Tool timeout (the server writes, then answers too late) | `failed`; resume → `actioned` with the same ticket, one ticket in total |
| Duplicate call (act runs twice; a tool loop repeats a call) | one ticket; `loop_detected` with one execution |
| Invalid state (decision on a run not awaiting; a malformed decision; resume of a paused run) | 409 / 422, run unchanged, no event |
| Model failure (timeout, invalid output, an exception) | review route, still gated; or `failed` and resumable |
| Approval rejected | `rejected`, no ticket, `decided` event with the reviewer |

Also fixed here: a model call cancelled by a failing sibling step is recorded (W07's open gap).

## Hypotheses, written before any W08 code

- **H29.** No path crosses the gate without a decision. Seven attempts (start, re-invoke the paused
  thread, W07 resume, a second decision, two concurrent decisions, a write with no token, a write with
  a token for other arguments) are all blocked.
- **H30.** The database rejects `UPDATE` and `DELETE` on `workflow_events` and `memories`, and every
  status change in the fault suite has its event.
- **H31.** With real models, on three incidents the corpus cannot answer, a reviewer's note for the
  service turns at least 2 of 3 into answers citing `M1`; the same note scoped to another service or
  tenant is cited in 0 of 3.
- **H32.** A model that repeats the same tool call stops within 2 rounds with `loop_detected` and the
  tool runs once.
- **H33.** All five faults end in an explicit status with an audit event, with no duplicate ticket and
  no unrecorded model call.
- **H34.** MCP read calls over stdio take under 20 ms at the median once connected; the server starts
  in under 2 s.

How they are judged: H29, H30, H32 and H33 are tests (the report re-runs the fault scenarios). H31 is
a real-model run on a scratch database: "cites `M1`" means a kept citation with that evidence id.
H34 is timed by the report over 50 calls.

## Day by day

- **AIENG-49** this document; events table and trigger; the approval interrupt; the decision API.
- **AIENG-50** memories table, API, `recall`, prompt v3; resume-semantics tests.
- **AIENG-51** tool-loop budgets and duplicate detection; resume budget; looping tests.
- **AIENG-52** ticket MCP server, client, approval tokens, `act`; trust-boundary note.
- **AIENG-53** fault scenarios and report, the W07 recording gap, H31 run, findings, ADR-005.

## Risks

1. **Reviewer identity is self-declared** until W10.
2. **Memory is untrusted text** that reaches the prompt; it is written by reviewers, but a note can be
   wrong or malicious. It is cited like evidence, never followed as an instruction; W10 tests it.
3. **The approval secret** sits with the host and the server. Whoever has it can mint approvals.
4. **A ticket server of our own** makes the boundary easier to enforce than with a vendor's server.
