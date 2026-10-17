# IncidentOps AI operator runbook (AIENG-72, W12)

Start every page with **`GET /status`** (any authenticated caller). It reports the tier and each
dependency's state, from checks of at most 2 s each; it never calls the model and never shows
connection details. The tiers:

| Tier | Meaning | What users can do |
|---|---|---|
| **FULL** | everything healthy | triage with recommendations and tickets |
| **REDUCED** | an optional integration is degraded | triage still runs; affected runs go to review with a named reason |
| **MANUAL** | the database is down, no model route is usable, or triage is not configured | no automatic triage: work incidents by hand (below) |

## When MANUAL: working without IncidentOps

1. Tell responders in the incident channel: "IncidentOps triage is off; use the runbooks directly."
2. Runbooks and postmortems are plain files in `data/corpus/`; the service catalogue (owners,
   on-call, dependencies) is `data/graph/catalogue.toml`.
3. Open tickets in the ticket system by hand. When IncidentOps is back, a run started for the
   same incident labels its ticket with the run id; it does not see a hand-opened ticket, so close
   duplicates by hand.

## Per dependency

### Database (PostgreSQL)

- **Tier:** MANUAL when down.
- **Users see:** `503 database_unavailable` with `Retry-After`; no partial results.
- **First five minutes:**
  1. `GET /status`: `database: down` (the detail is the exception type only).
  2. Check the database host and connections; the API keeps running and reconnects by itself
     (`pool_pre_ping`).
  3. Runs that were in flight when it went down stay `running`. After the database is back,
     **restart the API** (or wait for the next start): runs older than
     `INCIDENTOPS_STALE_RUN_AFTER_S` (900 s) are marked `failed` (`StaleRun`).
  4. Resume them: `POST /incidents/{id}/triage/{run_id}/resume` (admin). Finished steps are not
     repeated; at most three resumes per run, then `abandoned`.

### Model provider

- **Tier:** REDUCED when one route's breaker is open (others answer); MANUAL when every route's
  breaker is open, or no model is configured.
- **Users see:** with a fallback, nothing different (the answering model is in the run's trace and
  `llm_runs`); without one, runs go to review with `model_failed`, and no causes are shown.
- **First five minutes:**
  1. `GET /status`: `model` lists the open breakers.
  2. Check the provider's status page and your quota. A breaker retries one call after 30 s by
     itself.
  3. To add a fallback, set `INCIDENTOPS_LLM_ROUTES` (for example
     `{"diagnosis": ["primary-model", "fallback-model"]}`) and restart. Fallback happens only on
     timeouts, rate limits and provider errors, never on invalid output.

### Retrieval / vector store

- **Tier:** stays FULL or REDUCED (it is the database); a run whose retrieval fails or takes over
  5 s goes to review.
- **Users see:** `retrieval_error` in the run (`RetrievalTimeout` or the error type), no evidence,
  `no_answer`, review. Nothing is invented from missing evidence.
- **First five minutes:** check database load (`pg_stat_activity`); `INCIDENTOPS_RETRIEVAL_STACK=bm25`
  removes the embedding call if the embedding provider is the problem.

### Knowledge graph (Neo4j)

- **Tier:** REDUCED.
- **Users see:** `graph_status: unavailable`; recommendations without the on-call engineer or open
  fixes. The route is unchanged.
- **First five minutes:** check the Neo4j container or host; the API reconnects by itself.

### CMDB (legacy SOAP)

- **Tier:** REDUCED.
- **Users see:** every run goes to review with `integration_degraded`; the proposed ticket says
  "Owner: unknown (CMDB unavailable); nothing assumed".
- **First five minutes:** check the CMDB endpoint and `INCIDENTOPS_CMDB_API_KEY`; a SOAP fault or
  malformed answer shows as `invalid`, an unreachable one as `unavailable`.

### Ticket system (Jira-style API or the MCP ticket server)

- **Tier:** REDUCED.
- **Users see:** an approved run's `act` step fails; the run is `failed` and resumable.
- **First five minutes:**
  1. Check the ticket system and the token.
  2. Resume the failed run once it is back. **A write that timed out may have succeeded:** the
     resume searches for the run's label (Jira) or uses the run id as the idempotency key (MCP),
     so it finds that ticket instead of opening a second one.

### Identity

- Not a tier: without a valid token every route answers 401. If no API token and no JWT key is
  configured, every route answers `503 auth_not_configured`. Check `INCIDENTOPS_JWT_*` and the
  identity provider's key rotation.

## Load

The W12 baseline (one API process, one laptop, simulated model, at most 25 responders per client
process): no errors up to 100 concurrent responders; triage P95 9.1 s at 100, almost all of it the
model. The API's own overhead per run (checkpoint writes, the event loop) grows from about 40 ms at
1 responder to about 400 ms at 100. Larger connection pools (`INCIDENTOPS_DATABASE_POOL_SIZE`,
`INCIDENTOPS_DATABASE_MAX_OVERFLOW`, `INCIDENTOPS_CHECKPOINT_POOL_SIZE`) take 10 to 15% off that at
100 concurrent runs; past that, run more API processes. When you load-test, split the load
generator across processes: one client process cannot drive 100 concurrent requests itself
([how this was found](../evals/reliability/runs/2026-10-17-measurement.md)).
