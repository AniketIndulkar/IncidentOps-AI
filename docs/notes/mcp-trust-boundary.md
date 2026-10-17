# The ticket MCP tool and its trust boundary (AIENG-52)

IncidentOps has one tool that changes another system: opening a ticket. It is reached over the
Model Context Protocol, and this note says who is responsible for what.

## The three roles

| Role | Here | Responsible for |
|---|---|---|
| **Host** | the IncidentOps API process | deciding *whether* a write may happen: the approval gate, the audit trail, the secret that signs approvals, which tools a model may see (none of the write tools) |
| **Client** | `TicketClient` inside the host (`mcp.Client` over stdio) | one connection per server, timeouts, turning error results into exceptions; it adds no authority of its own |
| **Server** | `python -m incidentops_ai.tickets.server`, a child process with its own SQLite | its own data, input validation, idempotency, and (because we wrote it) checking the approval signature |

```
person ──decision──▶ host: workflow_events (append-only) ──sign(run, decision, reviewer, args)──┐
                                                                                                ▼
model ── never sees the write tool or the secret        client ──create_ticket(args, approval)──▶ server
                                                                                  verify ▶ write ▶ ticket
```

## What makes a write possible

1. The run reached the approval gate and a person approved it through the API. The decision is an
   audit event; its id is part of what gets signed.
2. The host signs `(run id, decision id, reviewer, fingerprint of the exact ticket arguments)`
   with `INCIDENTOPS_APPROVAL_SECRET`.
3. The server recomputes the fingerprint from the arguments it received, checks that the approval
   is for the run the ticket is keyed on, and compares signatures in constant time. A missing,
   forged or mismatched approval is refused with a readable error and nothing is written.
4. The ticket's idempotency key is the run id, so a retried write returns the first ticket.

## What it protects against, and what it does not

| Threat | Stopped by |
|---|---|
| A prompt-injected model asking to open a ticket | The model has no write tool; if one were exposed, it has no secret to sign with |
| A bug that calls `act` without a decision | `TicketActions` refuses without an approve decision; the server refuses without a signature |
| Arguments changed after approval (a different body, another service) | The signature covers the exact arguments |
| A replay of an approval for another run | The approval names the run, and the run id is the idempotency key |
| A timeout after the server wrote | Idempotency: the retry returns the same ticket |
| **Someone who holds the secret** | Nothing: whoever has it can mint approvals. It lives in the host's and the server's environment only (never on a command line) |
| **Reviewer identity** | Not yet: the reviewer's name is what the caller says (one API token until W10) |

## With someone else's MCP server

A vendor's ticket server will not check our signatures. Then step 3 does not exist and the host
gate (steps 1, 2 and the refusal in `TicketActions`) is the whole boundary. The rules stay the same:
write tools are never offered to a model, every write passes the approval gate, and the decision is
recorded before the call is made.
