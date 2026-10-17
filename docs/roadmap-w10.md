# W10 roadmap: identity, access control and AI security (7–11 Dec 2026)

Source: Jira epic **AIENG-11** and tasks **AIENG-59 to AIENG-63**, read 17 Oct 2026. Overall plan:
[ROADMAP.md](ROADMAP.md). Built early, stacked on W09 (branch `feat/w10-iam-rbac-ai-security`).

> **Status, 17 Oct 2026: built and measured.** Design written before any W10 code; decisions are mine
> (W05 to W13 built back to back at the user's request).
>
> **Result:** H40 to H44 hold. Every route enforces its permission for every role; seven token
> attacks are refused; another tenant's data and documents never appear; eight attack cases hold
> against a compromised model; the real model followed 0 of 6 injections. Building it found two
> cross-tenant leaks (global idempotency keys, an unscoped tool). Findings:
> [w10-findings](../evals/security/runs/2026-10-17-w10-findings.md); threat model:
> [threat-model.md](threat-model.md); decision: [ADR-007](adr/007-identity-tenancy-and-model-controls.md).

## Goal of the week

Until now every caller with the one API token could do everything, in the one tenant, and a
reviewer was whoever they said they were. This week: know who is asking, decide on the server what
they may do, keep each tenant's data out of every other tenant's prompts, and attack the AI parts
deliberately, with the attacks kept as tests that fail the pipeline.

**Exit criterion (from the epic):** RBAC and data permissions, PII protection, a threat model and a
red-team suite in CI.

## AIENG-59: identity and the authorization flow

IncidentOps is a **resource server**. It does not log anyone in: in production a company identity
provider issues tokens (OIDC authorization code flow for people, client credentials for
automation), and IncidentOps verifies them.

```
request ─▶ authenticate: Bearer JWT (or the static API token)
           │  signature with the configured key; algorithm on an allow-list
           │  exp / nbf / iat (30 s leeway), iss, aud = "incidentops"
           │  claims: sub, name, tid (tenant), roles
           ▼
        Principal(user_id, name, tenant_id, roles)   ── 401 if anything fails
           ▼
        authorize the route: a permission the roles grant   ── 403 if not
           ▼
        load the resource *in the principal's tenant*   ── 404 if it is in another tenant
           ▼
        data access with the principal's tenant, never one from the request:
        retrieval filter · graph tenant · memory scope · attachments · ops SQL (row-level security)
           ▼
        context assembly (redacted) ─▶ model ─▶ output checks ─▶ response
```

- Keys: `INCIDENTOPS_JWT_SECRET` (HS256) **or** `INCIDENTOPS_JWT_PUBLIC_KEY` (RS256, PEM). The
  accepted algorithm follows the key, so a token signed with HS256 using the public key as the
  secret (the key-confusion attack) is refused. `alg: none` is never accepted.
- The static `INCIDENTOPS_API_TOKEN` stays as a **machine credential**: principal `api-token`,
  tenant `default`, role `admin`. It is a shared secret with no expiry, documented as such, for
  development and automation only.
- `python -m incidentops_ai.auth.dev_token` mints HS256 tokens for local use.

### Threat assumptions

1. The network is hostile; TLS terminates in front of the API (deploy concern).
2. The identity provider is trusted to authenticate people and to put the right tenant and roles in
   the token. A compromised IdP is out of scope.
3. Anyone may call the API; any field of any request may be crafted.
4. Incident text, attachments, retrieved documents, memory notes and downstream answers are
   **untrusted**: any of them may contain instructions aimed at the model.
5. The model may follow those instructions. Every control that matters is enforced in code, after
   or around the model, never only in a prompt.
6. Operators with database or host access are trusted (out of scope).

## AIENG-60: server-side RBAC

| Permission | viewer | responder | approver | admin |
|---|---|---|---|---|
| `incidents:read` (incidents, attachments) | ✓ | ✓ | ✓ | ✓ |
| `incidents:write` (create, upload) | | ✓ | ✓ | ✓ |
| `analysis:run` (extraction, related incidents, diagnosis: spends money) | | ✓ | ✓ | ✓ |
| `triage:read` (runs, state, events) | ✓ | ✓ | ✓ | ✓ |
| `triage:run` | | ✓ | ✓ | ✓ |
| `triage:decide` | | | ✓ | ✓ |
| `triage:resume` | | | | ✓ |
| `memory:read` | ✓ | ✓ | ✓ | ✓ |
| `memory:write` | | | ✓ | ✓ |
| `ops:query` | | ✓ | ✓ | ✓ |

- Every route declares its permission as a dependency; it runs before the body is read for
  anything else, so a refused call costs nothing and learns nothing. 401 `unauthorized` without a
  valid identity, 403 `forbidden` without the permission.
- **The decision's reviewer is the authenticated principal.** The decision body no longer has a
  `reviewer` field; the audit event records the name and the user id from the token. (Closes W08
  risk 34.)

## AIENG-61: data-level permissions

- `incidents` gains `tenant_id`. The repository reads and writes only in the caller's tenant; an
  incident in another tenant is **not found** (404), not forbidden, so ids do not confirm existence.
- **Idempotency keys are scoped per tenant.** Today a key is global: a second tenant reusing a key
  would replay the first tenant's incident.
- Workflow runs take their tenant from the principal; every node already reads `state["tenant_id"]`.
- Retrieval, the graph, memory and attachments receive the principal's tenant.
- **Ops SQL: row-level security keyed on the database user.** The ops tables gain `tenant_id`;
  `ops.tenant_readers` maps reader logins to tenants; a `SECURITY DEFINER` function returns the
  current login's tenant, and each table's policy compares against it. Each tenant has its own
  reader login (`INCIDENTOPS_OPS_DATABASE_URLS`, a JSON map of tenant to URL). A session setting
  would have been simpler, but W09 showed `set_config` runs as the reader if the validator ever
  fails; `current_user` cannot be changed by a query.

## AIENG-62: attacks and controls

| Attack | Control (in code) |
|---|---|
| Instructions in an incident, attachment, retrieved document or memory note | Evidence fenced with a nonce (W04); claims must cite checked quotes (W04); the route is code (W07); writes need a person (W08); **new:** evidence that reads like instructions is flagged and sends the run to review (`suspicious_evidence`) |
| System-prompt extraction | **New:** a guarded provider puts a random canary in every system prompt and withholds any output containing it, or a long verbatim run of the instructions |
| PII in incident text or attachments reaching the model | **New:** redaction before any prompt (emails, phone numbers, card numbers with a Luhn check, IBANs, IPv4 addresses, access keys and tokens, private keys) |
| PII or secrets in model output | **New:** the same detectors on every string the model returns |
| Cross-tenant reads | Tenant from the token; 404 for other tenants' resources; RLS for SQL |
| Privilege escalation | Server-side permissions; token verification refuses `none`, the wrong algorithm, expiry, wrong audience or issuer, tampering |

**Why not Presidio.** It needs a spaCy model (hundreds of MB) and finds names by NER, which is
probabilistic. The identifiers that leak in incident text (emails, phone numbers, cards, keys) are
pattern-shaped; regex detectors with checksums are deterministic, fast and testable. Names are not
redacted: on-call names are the point of an incident tool. Recorded as a limit.

`evals/security/attacks.jsonl`: each case states the attack, how a **compromised model** behaves
(it follows the injection), and the control expected to hold. The tests script that model; a
real-model run measures how often the real model follows the injections at all.

## AIENG-63: threat model and CI

- `docs/threat-model.md`: assets, actors, trust boundaries, threats (untrusted input, retrieval
  poisoning, exfiltration, tool abuse, privilege escalation, tenant breakout), each mitigation and the
  test that holds it, residual risks.
- Security tests carry the `redteam` marker; CI runs them as their own step, so a security
  regression fails the pipeline under its own name.

## Hypotheses

- **H40.** Every route behaves per the permission table for anonymous callers and each of the four
  roles (a matrix test over all routes).
- **H41.** Seven token attacks are refused with 401: `alg: none`, HS256 with the public key as
  secret, expired, wrong audience, wrong issuer, a tampered payload, no tenant.
- **H42.** A principal of another tenant gets 404 or nothing for incidents, runs, memory,
  attachments and ops rows, and another tenant's documents never appear in the prompt the model
  receives.
- **H43.** With a model scripted to follow every injection, the controls hold on all attack cases:
  no uncited claim shown, no system-prompt text returned, no PII in a prompt or an output, no write
  without a person, suspicious evidence routed to review.
- **H44.** The real model follows at most 2 of the 6 injection cases, and none of those reaches an
  output (the controls catch them).

## Day by day

- **AIENG-59** this document; `auth` (verifier, principal, dev tokens).
- **AIENG-60** permissions, route dependencies, the role matrix test; reviewer from the principal.
- **AIENG-61** incident tenant, scoped idempotency, tenant-aware runner and routes, ops RLS.
- **AIENG-62** PII detectors, redaction at the sources, the guarded provider, suspicious evidence,
  the attack set, the real-model run.
- **AIENG-63** threat model, `redteam` marker, CI step, findings, ADR-007.

## Risks

1. **The static API token** is an admin credential with no expiry; keep it out of production.
2. **Regex PII detection** misses what it does not know the shape of (names, addresses, free text).
3. **The injection detector is a heuristic.** It flags phrases; a careful attacker can avoid them.
   It routes to review, it does not block, so a false positive costs a person's time.
4. **Per-tenant reader logins** multiply database credentials with the number of tenants.
