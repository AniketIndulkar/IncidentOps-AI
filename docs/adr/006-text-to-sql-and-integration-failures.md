# ADR-006: Text-to-SQL behind independent layers, and integrations that fail loudly

- **Status:** accepted (W09, 17 Oct 2026)
- **Context tickets:** AIENG-54 to AIENG-58
- **Evidence:** [W09 findings](../../evals/sql/runs/2026-10-17-w09-findings.md),
  [text-to-SQL run](../../evals/sql/runs/2026-10-17-text-to-sql.md),
  [contracts](../notes/integration-contracts.md), [W09 design](../roadmap-w09.md)

## Context

IncidentOps now reads three outside systems: a ticket API, an operations database a model may
question in plain language, and a legacy SOAP CMDB that says who owns a service. Each can be wrong,
slow, unreachable or hostile. The text-to-SQL path puts generated code next to a database, the
classic way an assistant becomes an attack surface.

## Decision

1. **Text-to-SQL runs only through four layers, each tested with the others removed:** the prompt
   (allow-listed schema, the question as data); the validator (`sqlglot`: one SELECT, allow-listed
   tables and columns, no system or identity functions, a capped numeric LIMIT, regenerated SQL);
   a NOLOGIN reader role with `SELECT` on `ops` only; and a `READ ONLY` transaction with a
   per-query `SET LOCAL statement_timeout` and a row cap.
2. **The schema the model sees is an allow-list**, kept equal to the database by a test, never an
   introspection.
3. **Every outside call has a written contract** (auth, timeout, retries, idempotency, failure
   mapping), and its tests are against fakes that can produce each failure.
4. **Reads may be retried; writes are not retried blind.** A ticket write is labelled with the run
   and found again by search; a CMDB lookup retries once.
5. **A downstream failure is a named state, never a default.** The CMDB adapter returns `ok`,
   `unknown_service`, `unavailable` or `invalid`; the last two route the run to review with
   `integration_degraded`, and the proposal says the owner is unknown.

## Why not the alternatives

- **Trust the database role alone.** It stops every write and every read of our own tables, but
  PostgreSQL lets every role read most of its catalog: three of the twenty attacks run against the
  role with the validator removed.
- **Trust the validator alone.** It is a parser's opinion of PostgreSQL. It refused a valid
  upper-case query until a spot check found it; another case may slip the other way. The role and
  the read-only transaction do not depend on parsing.
- **Let the model see the live schema.** New columns (an `api_key`, say) would appear to the model
  the day they were added.
- **Retry everything.** A retried ticket write after a timeout is how two tickets appear.
- **Fall back to a default owner** when the CMDB is down. The ticket would page the wrong person
  with confidence. Review is slower and right.

## Consequences

- The ops data shares a cluster catalog with everything else. Table and role names are readable
  if the validator ever fails. Moving the ops data to its own database (or cluster) removes that.
- A CMDB outage sends every run to review. That is the intended cost.
- Each adapter's contract is the place to grow: pagination, API versions, auth refresh.
- One more model route (`POST /ops/query`) behind the token and the rate limit.

## Revisit when

- Real Jira or a real CMDB replaces the fakes: re-run the contract tests against a sandbox.
- The ops data moves to its own database: re-run the layer matrix and update the risk.
- Questions need joins across sources (ops data plus incidents): every source must then pass the
  same layers, and W10's tenant filters apply before any of them.
