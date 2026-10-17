# IncidentOps AI threat model (AIENG-63, W10, 17 Oct 2026)

What we protect, from whom, across which boundaries, and the test that holds each control. The
assumptions behind it are in [roadmap-w10.md](roadmap-w10.md#threat-assumptions); the decision in
[ADR-007](adr/007-identity-tenancy-and-model-controls.md). Every test named here carries the
`redteam` marker and runs as its own CI step.

## Assets

1. **Incident data** per tenant: incidents, attachments, runs, notes, ops rows.
2. **Actions**: tickets opened in the ticket system (the only write to another system).
3. **The audit trail** and **memory**: who decided what, what reviewers taught the system.
4. **Secrets**: API token, JWT key, approval secret, ticket and CMDB credentials, the model key.
5. **Model spend**.

## Actors

- **Outsider**: anyone who can reach the API.
- **Tenant user** (viewer, responder, approver, admin) acting beyond their role or tenant.
- **Content author**: anyone who can put text where the model reads it (an incident reporter, a
  screenshot, a runbook author, a reviewer's note, a downstream system's answer).
- **The model itself**, if it follows what a content author wrote.

## Trust boundaries

```
outsider ──HTTPS──▶ [API: authenticate · permit · tenant] ──▶ data stores (tenant-scoped, RLS)
                                    │
             untrusted text ──▶ [redact · flag · fence] ──▶ model ──▶ [guard · citations · route]
                                                                          │
                                                person ──▶ [approval gate] ──▶ [signed write] ──▶ tickets
```

## Threats, controls, tests

| # | Threat | Control | Test (all `redteam`) |
|---|---|---|---|
| T1 | **Untrusted input steers the model** (instructions in an incident, attachment, runbook, note) | Evidence fenced as data; causes must cite verbatim quotes; the route is code; instruction-like text is flagged and sent to review; nothing acts without a person | `test_security_attacks.py` (5 cases, compromised model); real model followed 0 of 6 |
| T2 | **Retrieval poisoning** (a document planted to be retrieved) | Tenant filter before retrieval; poisoned text flagged; citation of it still needs a person's approval | `test_a_poisoned_runbook_sends_the_run_to_review`; `test_another_tenants_documents_never_reach_the_prompt` |
| T3 | **System-prompt extraction** | A canary in every system prompt; outputs with it or 60 verbatim characters of the instructions are withheld | `test_guard.py`; `test_a_system_prompt_leak_is_withheld` |
| T4 | **PII and secret exfiltration** through the model | Redaction before any prompt (incident, attachments, notes, SQL questions); the same detectors on every model output | `test_pii.py`; `test_pii_never_reaches_the_prompt`; `test_pii_in_the_models_output_is_redacted` |
| T5 | **Cross-tenant reads** | Tenant from the token only; repository lookups scoped (404); idempotency keys per tenant; tools bound to the tenant; RLS on ops data keyed on `session_user` | `test_tenant_isolation.py` (8 tests) |
| T6 | **Privilege escalation** | Server-side permission per route; reviewer and author from the token; JWT algorithm follows the key | `test_rbac_matrix.py` (every route × 5 callers); `test_auth_tokens.py` (alg none, key confusion, expiry, audience, issuer, tampering, no tenant) |
| T7 | **Tool abuse** (a model or bug writing to another system) | Write tools never offered to a model; the approval gate; a write signed over its exact arguments, checked by the server; idempotent on the run | `test_workflow_approval.py` (7 attempts at the gate); `test_tickets_mcp.py` (no, forged and mismatched approvals) |
| T8 | **Generated SQL as an attack** | Allow-list, a validator, a reader role, read-only transactions with a timeout, RLS | `test_sql_guard.py` (20 attacks); `test_ops_database.py` (12 writes, read-write too); `test_a_compromised_sql_model_is_refused` |
| T9 | **Audit tampering** | Append-only tables enforced by a trigger | `test_workflow_events_are_append_only`; `test_memories_are_append_only` |
| T10 | **Runaway spend or loops** | Rate limit after login; tool-loop duplicate detection and budgets; resume budget | `test_tool_budgets.py`; `test_workflow_budgets.py`; `test_requests_without_the_token_do_not_use_up_the_budget` |

## Residual risks

1. **The static API token** is an admin credential with no expiry (development and automation).
2. **Regex PII detection** misses names, addresses and anything without a shape.
3. **The injection detector is a phrase list.** The real-model run already found one phrasing it
   missed ("put your full system prompt into…"); it routes to review, it does not block.
4. **A cited poisoned quote passes the citation check**: only the flag and the person stop it.
5. **Catalog reads** depend on the SQL validator alone (W09 risk 38).
6. **The approval secret** can mint approvals; the JWT key can mint identities.
7. **LLM run keys** are global: a key reused across tenants is refused, not replayed (no leak, a
   possible nuisance).
8. **Model provider**: prompts (redacted) leave our boundary for the model API.
