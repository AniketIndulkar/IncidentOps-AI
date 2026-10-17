# Integration contracts (AIENG-54)

What IncidentOps expects from each external system and what it does when the expectation fails.
Written before the adapters; the adapters' tests check each line.

## Jira-style ticket API

| Concern | Contract |
|---|---|
| Endpoints | `GET /rest/api/3/search?jql=…&maxResults=…`, `GET /rest/api/3/issue/{key}`, `POST /rest/api/3/issue` |
| Auth | `Authorization: Bearer <INCIDENTOPS_JIRA_TOKEN>`. The token never appears in logs, errors or exceptions |
| Payloads | Pydantic models for every request and response. A response that does not validate is `invalid_response`, not a crash |
| Timeout | 5 s per request (connect and read) |
| Retries | 429: wait `Retry-After` (capped at 10 s) and retry. 500/502/503/504 and connection errors: back off 0.2 s, 0.4 s. At most 3 attempts. 400/401/403/404/409: never retried |
| Writes | Only with an approved decision. Every created issue carries the label `incidentops-run-<run id>`. Before creating, search for that label; if found, return the existing issue (`created: false`). A create that times out is not retried blindly: the next attempt searches first |
| Errors | `JiraError` with a `kind` (`unauthorized`, `not_found`, `rate_limited`, `unavailable`, `invalid_response`, `rejected`) and the HTTP status; the message names the operation, never the body |

## Ops database

| Concern | Contract |
|---|---|
| Connection | `INCIDENTOPS_OPS_DATABASE_URL`, a login granted only `incidentops_ops_reader` |
| Privileges | `USAGE` on schema `ops`, `SELECT` on its tables. No other schema, no writes, no DDL |
| Session | `default_transaction_read_only = on`, `statement_timeout = 2s` on the role; each query also runs in a `READ ONLY` transaction |
| Shape | At most 100 rows returned; the validator adds or lowers `LIMIT` |
| Errors | `sql_refused` (validation, with the rule that failed) or `sql_failed` (the database refused or timed out). No partial rows |

## Legacy CMDB (SOAP 1.1)

| Concern | Contract |
|---|---|
| Endpoint | `POST /cmdb` with `SOAPAction: GetServiceRecord` and a SOAP envelope naming the service |
| Auth | `X-Api-Key: <INCIDENTOPS_CMDB_API_KEY>` |
| Parsing | `defusedxml` (DTDs, entities and external references refused). Namespaces checked |
| Normalised to | `ServiceRecord(service, owner, tier, maintenance_window)`; all four required |
| Timeout | 3 s |
| Retries | One retry on connection errors and 5xx |
| Errors | `unknown_service` (a valid "not found" answer), `unavailable` (no answer, 5xx, timeout), `invalid` (unparseable XML, a SOAP fault, a missing field). `unavailable` and `invalid` send the workflow run to review with `integration_degraded`; no owner is assumed |
