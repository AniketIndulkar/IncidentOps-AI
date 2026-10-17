# W09 roadmap: enterprise integrations and a safe text-to-SQL path (30 Nov–4 Dec 2026)

Source: Jira epic **AIENG-10** and tasks **AIENG-54 to AIENG-58**, read 17 Oct 2026. Overall plan:
[ROADMAP.md](ROADMAP.md). Built early, stacked on W08 (branch `feat/w09-integration-text-to-sql`).

> **Status:** design written before any W09 code. Decisions are mine (W05 to W13 built back to back
> at the user's request).

## Goal of the week

Connect IncidentOps to the kinds of systems an operations team already runs, each with a written
contract and an explicit answer to "what happens when it fails": a ticket system's REST API, an
operational database the model may query but never change, and a legacy SOAP service.

**Exit criterion (from the epic):** a safe text-to-SQL path plus one external-system adapter with
explicit failure handling.

## The three integrations (AIENG-54)

| | Jira-style REST adapter | Ops database (text-to-SQL) | Legacy CMDB (SOAP/XML) |
|---|---|---|---|
| Used for | searching and opening tickets | answering questions over deployments, alerts, on-call | the system of record for service ownership |
| Transport | HTTPS JSON (`httpx`) | PostgreSQL, schema `ops` | HTTP POST of a SOAP 1.1 envelope |
| Auth | bearer token from settings, never logged | a **read-only database role** | a shared API key header |
| Timeout | 5 s per request | 2 s `statement_timeout`, read-only transaction | 3 s |
| Retries | 429 (honouring `Retry-After`) and 5xx, at most 3 tries with backoff; never 4xx; a write is retried only after a search shows it did not happen | none (a failed query is an answer, not a retry) | one retry on connection errors and 5xx |
| Idempotency | a label `incidentops-run-<run id>` on every created issue; create searches for it first | not applicable (reads only) | not applicable (reads only) |
| Failure behaviour | typed errors (`unauthorized`, `not_found`, `rate_limited`, `unavailable`, `invalid_response`) with no response body or token in the message; writes need an approved decision | the query is refused (validation) or fails (database) and the API says which, with no rows | `unavailable` or `invalid`: the workflow sends the run to review with reason `integration_degraded` and does not invent an owner |

The contracts are written out in [notes/integration-contracts.md](notes/integration-contracts.md).

Both HTTP services are faked locally (FastAPI apps in `incidentops_ai.integrations`), with switches
for the failures to test: errors, rate limits, slow answers, malformed bodies, SOAP faults.

## AIENG-55: the Jira-style adapter

- `JiraClient`: `search(jql_like)`, `get(key)`, `create(fields, labels)`. Typed request and response
  models; the raw response is validated before use.
- `JiraTicketSink` plugs into the W08 `act` step beside the MCP ticket server: it refuses without an
  approve decision, searches for the run's label first, and creates only when nothing exists.
- With `INCIDENTOPS_JIRA_URL` and `INCIDENTOPS_JIRA_TOKEN` set, it is the ticket system; otherwise
  the W08 MCP server is used when configured.

## AIENG-56: a read-only ops database

- Schema `ops` (created by a migration, seeded deterministically): `services`, `deployments`,
  `alerts`, `oncall_shifts`, `error_budget`.
- A role `incidentops_ops_reader` (NOLOGIN) with `USAGE` on `ops` and `SELECT` on its tables, nothing
  else; `default_transaction_read_only` and a 2 s `statement_timeout` on the role. A login user is
  granted the role; the app reads through `INCIDENTOPS_OPS_DATABASE_URL`.
- Schema metadata for the model comes from an **allow-list** (tables, columns, one-line
  descriptions), not from introspection.

## AIENG-57: defensive text-to-SQL

Four independent layers; any one of them stops a bad query:

1. **Prompt**: the allow-listed schema only, the question as data, "one SELECT".
2. **Validator** (`sqlglot`, Postgres dialect): exactly one statement; a `SELECT` (or `WITH … SELECT`)
   and nothing else; every table and column on the allow-list (no `pg_catalog`,
   `information_schema` or other schemas); no denied functions (`pg_sleep`, `pg_read_file`,
   `dblink`, `set_config`, `lo_*` …); no `FOR UPDATE`, `INTO`; a `LIMIT` added or capped at 100.
3. **Database role**: SELECT on `ops` only, read-only transactions.
4. **Limits**: `statement_timeout`, row cap, and the query runs in a `READ ONLY` transaction.

`POST /ops/query` takes `{"question": "..."}` and returns the SQL, the rows, or the reason it was
refused. Same token and rate limit as the other model routes.

## AIENG-58: the legacy adapter and degraded integrations

- A mock CMDB speaking SOAP 1.1 (`GetServiceRecord`), parsed with `defusedxml` (no DTDs, no
  entities), normalised to `ServiceRecord(service, owner, tier, maintenance_window)`.
- A new deterministic workflow step `cmdb` beside retrieval, the graph and recall. Its status is
  `ok`, `unknown_service`, `unavailable` or `invalid`. `assess` gains the reason
  **`integration_degraded`** for `unavailable` and `invalid`: a run whose owner cannot be
  established goes to a person, with the reason named, and the proposal says the owner is unknown.

## Evaluation, written before any W09 code

- `evals/sql/questions.jsonl`: 15 questions with a reference SQL whose result is the expected
  answer, and 8 **malicious questions** (asking the model to drop, update, read other schemas,
  sleep, or ignore its rules).
- `evals/sql/attacks.jsonl`: 20 hand-written SQL strings the validator must reject (writes, DDL,
  multi-statement, comments hiding a second statement, catalog reads, denied functions, locking
  clauses, unknown tables and columns, `SELECT INTO`, `COPY`, `SET ROLE`).

**Text-to-SQL success** = the generated SQL passes validation, runs, and returns the same number of
rows as the reference SQL, and every column of the reference result has a column in the generated
result with the same values (in the same order when the question asks for an order; otherwise as a
multiset). Extra columns are allowed: a model that adds `service` beside `count` is not wrong.

## Hypotheses

- **H35.** The Jira adapter retries exactly what is safe: 429 and 5xx up to 3 tries (honouring
  `Retry-After`), never a 4xx, and a write only after a search shows it is absent; no error message
  contains the token or a response body (tests).
- **H36.** As the reader role, 12 of 12 write and DDL statements fail with a permission or read-only
  error, and a 5 s query is cancelled by the timeout (tests, even with the validator bypassed).
- **H37.** The validator rejects 20 of 20 attacks and accepts all 15 reference queries (tests).
- **H38.** With the real model, text-to-SQL succeeds on at least 12 of 15 questions, and 0 of 8
  malicious questions execute anything outside the allow-list or any write.
- **H39.** Each of four legacy failures (connection refused, timeout, malformed XML, SOAP fault)
  routes the run to review with `integration_degraded` and no invented owner (tests).

## Day by day

- **AIENG-54** this document and the contracts note; the eval files.
- **AIENG-55** fake Jira, `JiraClient`, `JiraTicketSink`, tests.
- **AIENG-56** ops schema, seed, reader role, catalogue, tests.
- **AIENG-57** validator, text-to-SQL service, `/ops/query`, real-model run.
- **AIENG-58** fake CMDB, SOAP adapter, `cmdb` step, degraded routing, findings.

## Risks

1. **A validator is a parser's opinion.** `sqlglot` may parse something differently from
   PostgreSQL; the database role is the layer that does not depend on parsing.
2. **Read-only is not harmless.** A SELECT can still be expensive (timeout, row cap) or reveal data
   the asker should not see (W10 adds per-tenant filters).
3. **Fake services are kinder than real ones.** Real Jira has pagination, field schemes, and rate
   limits that vary; the contract tests are the place to grow.
