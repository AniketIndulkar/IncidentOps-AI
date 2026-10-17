# ADR-005: The approval gate, append-only memory, and the trust boundary for tools

- **Status:** accepted (W08, 17 Oct 2026)
- **Context tickets:** AIENG-49 to AIENG-53
- **Evidence:** [W08 findings](../../evals/workflow/runs/2026-10-17-w08-findings.md),
  [fault injection](../../evals/workflow/runs/2026-10-17-fault-injection.md),
  [memory check](../../evals/memory/runs/2026-10-17-memory-check.md),
  [trust-boundary note](../notes/mcp-trust-boundary.md), [W08 design](../roadmap-w08.md)

## Context

After W07 the workflow could recommend, but it could not act, and nothing stopped it acting if it
could. W08 had to let it change another system (open a ticket) without that ever happening on a
model's say-so, remember what reviewers teach it without rewriting what already happened, and stop
any retry or loop from running without limit.

## Decision

1. **Every run stops at one gate.** A LangGraph `interrupt` (`approval`) before the only step that
   writes (`act`). Only `POST .../decision` moves the run on, once: the run row is locked while the
   decision is recorded. The failed-run resume refuses a paused run. A malformed answer makes the
   gate ask again; it is never stored as the answer.
2. **The audit trail is append-only in the database.** `workflow_events` (one row per transition,
   with the actor) and `memories` reject `UPDATE` and `DELETE` with a trigger, so neither a bug nor a
   careless migration script can rewrite them.
3. **Memory is notes, never edits.** A correction supersedes a note with a new row; a withdrawal is a
   superseding row without text; each note has at most one successor. `recall` gives the diagnosis up
   to three current notes for the tenant and service as evidence `M1..M3`, cited like documents. A
   run keeps the notes it recalled in its checkpoint.
4. **Tools are reached over MCP, and writes carry a signed approval.** The host signs the run, the
   decision's event id, the reviewer and a fingerprint of the exact arguments. Our ticket server
   checks that signature; the host refuses to call without an approve decision regardless. Write
   tools are never offered to a model. Writes are idempotent on the run id.
5. **Every loop has a budget and an explicit ending.** The tool loop stops repeated calls
   (`loop_detected`) and caps total calls (`tool_budget`); the workflow caps super-steps per
   invocation (25) and resumes per run (3, then `abandoned`).

## Why not the alternatives

- **Auto-approve recommended runs, gate only reviewed ones.** The write is the risk, whatever the
  route; and the memory check showed a single note can turn a no-answer into a recommendation.
- **Audit in the application only** (no trigger). The test that tries `UPDATE` and `DELETE` is the
  point: a guarantee the database enforces does not depend on every future code path behaving.
- **Mutable memory with a version number.** Simpler to query, but a run could no longer point at the
  exact text it used, and a correction would silently change the meaning of past runs.
- **Give the model the ticket tools** (an agent that decides to open tickets). Nothing in the cases
  needs the model to choose *whether* to write; letting it would make the approval check the only
  defence against prompt injection instead of one of two.
- **Trust the MCP server to enforce approval.** A vendor's server will not check our signatures; the
  host gate must hold on its own, and does.

## Consequences

- A person must act on every run before anything is written; runs wait at `awaiting_approval`. There
  is no timeout on the wait yet.
- Reviewer identity is self-declared until W10 (one API token).
- Whoever holds `INCIDENTOPS_APPROVAL_SECRET` can mint approvals; it lives only in the host's and the
  server's environments.
- A recommendation can rest on a single reviewer's note; nothing flags it yet.
- The ticket server is a child process of the API; if it dies, `act` fails and the run is resumable.

## Revisit when

- W10 adds per-user access: the reviewer becomes the authenticated user, and the gate can require a
  role.
- A vendor ticket system replaces ours: drop the server-side signature check from the boundary and
  re-test the host gate alone.
- Notes become numerous: recall needs ranking, and stale notes need review.
