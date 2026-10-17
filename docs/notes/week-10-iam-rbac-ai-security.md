# Week 10 write-up: identity, access control and AI security (AIENG-59 to AIENG-63)

Status on 17 Oct 2026: **built and measured**, as a pull request stacked on W09. Design, threat
assumptions and hypotheses were committed before any W10 code ([roadmap-w10.md](../roadmap-w10.md));
the threat model is [threat-model.md](../threat-model.md); results in
[the findings](../../evals/security/runs/2026-10-17-w10-findings.md); the decision in
[ADR-007](../adr/007-identity-tenancy-and-model-controls.md).

## The result in five lines

1. Callers are **JWT principals** (or the documented machine token); four roles grant ten
   permissions, checked on the server on every route. A matrix test covers all 18 routes.
2. **The tenant comes from the token** and is enforced where data is read: lookups, idempotency
   keys, retrieval, tools, notes, and ops SQL by row-level security keyed on the database login.
3. **PII and secrets are redacted** before any prompt, and every model output passes a guard that
   withholds system-prompt leaks (a canary) and redacts what slips through.
4. With a **compromised model**, all eight attack cases are held by code; with the real model, it
   followed none of six injections and the detector flagged five.
5. 170 security tests run as their own **red-team CI step**. 897 tests pass (2 opt-in skipped).

## What exists now

```
src/incidentops_ai/auth/       principal.py (roles, permissions), tokens.py (verify, mint), dev_token.py
src/incidentops_ai/security/   pii.py, guard.py (GuardedProvider), injection.py, benchmark.py
apps/api/app/deps.py           authenticate, permit(permission), per-tenant ops databases
migrations c1e3a5b7d9f0 (tenant on incidents and idempotency keys), d2f4b6c8e0a1 (ops RLS)
docs/threat-model.md           T1 to T10, each with its control and its test
evals/security/                attacks.jsonl (8 cases), runs/
.github/workflows/ci.yml       "Red-team suite" step (pytest -m redteam)
```

## Decisions, with why and why not

| Decision | Why | Why not the alternative |
|---|---|---|
| **Resource server, JWT verified** | The IdP authenticates; we verify | Logging people in ourselves |
| **Algorithm follows the key** | Refuses `none` and key confusion | An algorithm list from the token |
| **Permission per route, on the server** | Holds for every caller | UI or prompt checks |
| **Tenant enforced where data is read** | The model never sees another tenant's text | "Only use tenant X" in a prompt |
| **RLS on `session_user`** | A query cannot change it | A session setting (`set_config` runs) |
| **Regex redaction** | Deterministic, fast, testable | Presidio (large, probabilistic) |
| **Flag injections, route to review** | False positives cost a minute | Blocking |

## What the work taught

- A feature added three weeks ago (idempotency keys, the related-incidents tool) can be the
  cross-tenant leak: every place data is read had to be revisited.
- The safest place for the tenant is out of the model's reach: bound into tools, never an argument.
- A heuristic list is only as good as its phrasings: the real run found one it missed.
- Switching a control off and watching the test fail is how to know the test tests it.

## What went wrong on the way

- The phone pattern first read groups of a card-like number as a phone number.
- FastAPI now nests included routers; the route-coverage test reads the OpenAPI schema instead.
- `get_ops_database` referenced `authenticate` before it was defined.
- An asyncpg prepared statement refused a multi-statement seed in a migration.

## What is still weak

- No real IdP was connected; tokens are minted.
- Names and addresses are not redacted.
- The injection detector is a phrase list.
- A cited poisoned quote passes the citation check; only the flag and the gate stop it.

## Where to look

- `uv run python -m incidentops_ai.auth.dev_token --sub u-1 --name "Amara Okafor" --tenant default --role approver`
- `uv run pytest -m redteam`
