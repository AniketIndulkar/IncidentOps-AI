# Week 9 write-up: enterprise integrations and text-to-SQL (AIENG-54 to AIENG-58)

Status on 17 Oct 2026: **built and measured**, as a pull request stacked on W08. Design, contracts,
evaluation sets and hypotheses were committed before any W09 code ([roadmap-w09.md](../roadmap-w09.md),
[contracts](integration-contracts.md)); results in
[the findings](../../evals/sql/runs/2026-10-17-w09-findings.md), the decision in
[ADR-006](../adr/006-text-to-sql-and-integration-failures.md).

## The result in five lines

1. A **Jira-style adapter** retries only what is safe, keeps its token and response bodies out of
   errors, and opens an approved ticket exactly once per run, even after a timeout that wrote.
2. An **ops database** (deployments, alerts, on-call, error budgets) is readable through a role that
   cannot write: 12 of 12 write and DDL statements fail, with or without a read-only transaction.
3. **Text-to-SQL** answered 14 of 15 questions correctly with the real model. The validator refuses
   all 20 attack queries, and the model declined all 8 malicious questions.
4. The layers are **not fully redundant**: with the validator removed, the database still lets the
   reader read catalog tables and role names. For those, the validator is the only wall.
5. A **legacy SOAP CMDB** gives each run its owner; when it is down or answers nonsense, the run
   goes to a person with `integration_degraded` instead of a guessed owner. 825 tests pass.

## What exists now

```
src/incidentops_ai/integrations/  jira.py + fake_jira.py, cmdb.py + fake_cmdb.py
src/incidentops_ai/ops/            catalogue.py (allow-list), database.py (reader, read-only),
                                   sql_guard.py (validator), text_to_sql.py, benchmark.py
migration b8f0a2c4e6d8             schema ops, seed data, role incidentops_ops_reader
apps/api/app/routes/ops.py         POST /ops/query
prompt text_to_sql/v1
workflow: a `cmdb` step; the `integration_degraded` review reason; the owner in the proposal
evals/sql/                         questions (15), malicious (8), attacks (20), runs/
```

## Decisions, with why and why not

| Decision | Why | Why not the alternative |
|---|---|---|
| **Four layers for SQL, each tested alone** | Each one has a gap the others cover | Trusting the role (catalog reads) or the parser (false refusals) |
| **Allow-listed schema** | New columns stay invisible until added on purpose | Introspection |
| **Writes found by label, not retried** | A timeout after a write is the common case | Retry on failure |
| **Named downstream states** | A person sees "CMDB unavailable" | A default owner |
| **Fakes with failure switches** | Every failure in the contract is testable offline | Mocks of individual calls |

## What the numbers taught

- Test each layer with the others removed; "defence in depth" is a claim until then.
- Read what a validator emits before trusting a clean first run: two bugs were in the 45/45.
- The model refusing the malicious questions is good news and no evidence about the validator.
- A strict scoring rule will sometimes mark a better answer wrong (s08). I keep it, and say so.

## What went wrong on the way

- The validator first refused upper-case unquoted names (PostgreSQL folds them) and allowed
  `current_user`. Spot checks found both before the real run.
- SQLAlchemy's `text()` reads `12:00` as a bind parameter; queries now go to the driver as they are.
- The baseline in the W07 benchmark had to learn about each new step (recall, then cmdb).

## What is still weak

- The ops data shares the cluster catalog; catalog reads depend on the validator alone.
- Fakes are simpler than real Jira and real SOAP services.
- Nothing yet filters ops data per tenant (W10).

## Where to look

- `POST /ops/query {"question": "..."}` with `INCIDENTOPS_OPS_DATABASE_URL` set to a reader login.
- `uv run python -m incidentops_ai.ops.benchmark` (real model, scratch database).
