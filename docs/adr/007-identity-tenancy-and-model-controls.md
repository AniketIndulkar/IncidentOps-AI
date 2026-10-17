# ADR-007: Identity from the token, permissions on the server, tenants in the data layer, and controls around the model

- **Status:** accepted (W10, 17 Oct 2026)
- **Context tickets:** AIENG-59 to AIENG-63
- **Evidence:** [W10 findings](../../evals/security/runs/2026-10-17-w10-findings.md),
  [threat model](../threat-model.md), [W10 design](../roadmap-w10.md)

## Context

Until W10 one shared API token opened everything, in one tenant, and a reviewer was whoever they
typed. The system also reads text anyone can write (incidents, screenshots, runbooks, notes) and
hands it to a model that may follow it.

## Decision

1. **IncidentOps is a resource server.** It verifies bearer JWTs from the company IdP (HS256 or
   RS256; the algorithm follows the configured key) and builds a principal: user, name, tenant,
   roles. The static API token remains a documented machine credential.
2. **Four roles, ten permissions, checked on the server per route**, before anything else runs.
   The reviewer and the memory author are the principal.
3. **The tenant comes from the token and is enforced where data is read**: repository lookups
   (another tenant's resource is not found), idempotency keys, the retrieval filter, the graph,
   memory, attachments, tools (bound, never a model argument), and ops SQL by row-level security
   keyed on the database login.
4. **Controls around the model, in code**: PII and secrets redacted before any prompt; a guard on
   every model call (a canary in the system prompt; leaks withheld; output strings redacted);
   instruction-like text flagged and routed to review; the earlier controls (citations, the route
   in code, the approval gate, signed writes, the SQL layers) unchanged.
5. **Attacks are tests.** Reproducible cases run against a model scripted to obey them, marked
   `redteam`, in their own CI step.

## Why not the alternatives

- **Keep the shared token, add a "user" header.** Anyone could claim to be anyone; the audit trail
  would record a claim.
- **Check roles in the UI or in the prompt.** A UI can be skipped and a prompt can be talked out
  of it; only a server-side check holds for every caller.
- **Tenant isolation by prompt** ("only use documents of tenant X"). The model would see the other
  tenant's text, which is already the leak.
- **RLS on a session setting.** `set_config` runs as the reader (W09); `session_user` cannot be
  changed by a query.
- **Presidio.** A large model download and probabilistic NER for names; the identifiers that leak
  from incidents have shapes. Revisit for names and addresses.
- **Block on injection detection.** A phrase list has false positives; routing to a person costs a
  minute, blocking loses the incident.

## Consequences

- Every route now needs a caller (except `/health`); clients send a token.
- Each tenant needs its own ops reader login (more credentials to manage).
- The guard adds a marker to every system prompt and can withhold an honest output that happens to
  quote 60 characters of its instructions.
- The injection detector will miss phrasings; the real run already found one.

## Revisit when

- A real IdP is connected: test with its tokens and key rotation (JWKS).
- Names or addresses become a leak concern: add NER-based detection.
- Tenants grow past a handful: a credential per tenant for ops data may need a broker.
