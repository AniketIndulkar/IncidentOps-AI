# Deploying IncidentOps AI v1.0 (Docker Compose)

One server (or a laptop): the API and PostgreSQL with pgvector, optionally Neo4j. Verified from a
clean start on 17 Oct 2026 (see [the W13 findings](../evals/release/2026-10-17-w13-findings.md)).
For AWS Lightsail specifics (instance, firewall, costs) see [deploy-lightsail.md](deploy-lightsail.md);
the steps on the server are the ones below.

## 1. Prerequisites

- Docker with Compose v2 (`docker compose version`).
- This repository.
- For triage: an OpenAI API key and a model name. Without them the API runs, incidents can be
  stored, and `GET /status` says `MANUAL`.

## 2. Configure

```bash
cp .env.example .env
openssl rand -hex 24      # use one value for POSTGRES_PASSWORD
openssl rand -hex 24      # and another for INCIDENTOPS_API_TOKEN
```

In `.env`, set at least:

| Variable | Value |
|---|---|
| `POSTGRES_PASSWORD` | the first random value |
| `INCIDENTOPS_API_TOKEN` | the second (a machine credential with admin rights: keep it secret) |
| `OPENAI_API_KEY` | your key |
| `INCIDENTOPS_LLM_MODEL` | the model name, e.g. `gpt-6-luna` |

`INCIDENTOPS_DATABASE_URL` in `.env` is for running outside Docker; Compose sets its own. People
should sign in with JWTs from your identity provider (`INCIDENTOPS_JWT_SECRET` or
`INCIDENTOPS_JWT_PUBLIC_KEY`, issuer `INCIDENTOPS_JWT_ISSUER`, roles `viewer`, `responder`,
`approver`, `admin`); the static token is for automation.

## 3. Start

```bash
docker compose up -d --build
docker compose ps                        # db healthy, api up
curl -s localhost:8000/health            # {"status":"ok"}
```

Migrations run when the API container starts.

## 4. Load your runbooks and postmortems

Documents are Markdown files with front matter (`id`, `type: runbook | postmortem`, `title`,
`service`); `data/corpus/` has eighteen examples.

```bash
docker compose run --rm -v "$PWD/data/corpus:/corpus:ro" api \
    python -m incidentops_ai.retrieval.ingest /corpus      # parse, chunk, store
docker compose run --rm api python -m incidentops_ai.retrieval.index   # embed (needs the key)
```

Re-run both after changing documents. With `INCIDENTOPS_RETRIEVAL_STACK=bm25` the index step (and
its embedding cost) is not needed.

## 5. Check

```bash
TOKEN=<your INCIDENTOPS_API_TOKEN>
curl -s -H "Authorization: Bearer $TOKEN" localhost:8000/status
```

Expect `"tier": "FULL"` with the database, model, retrieval and triage `ok`, and the optional
integrations `off`. Anything else: [the runbook](runbook.md).

## 6. A first triage

```bash
INCIDENT=$(curl -s -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"title":"Orders cannot be placed","description":"Order writes time out after 30 seconds; the app waits for a pool connection.","service":"orders-api","severity":"SEV2","environment":"production"}' \
  localhost:8000/incidents | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
curl -s -X POST -H "Authorization: Bearer $TOKEN" localhost:8000/incidents/$INCIDENT/triage
```

It takes about ten seconds (two model calls, about $0.02). The run stops at `awaiting_approval`
with a cited diagnosis and a proposed ticket. This short example usually goes to `route: "review"`
with the reason `extraction_needs_review`: the description is too thin for every field to be
extracted with confidence, and the workflow says so instead of guessing.

Approve or reject with the run id from the response (`run.id`):

```bash
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"decision": "approve"}' localhost:8000/incidents/$INCIDENT/triage/<run id>/decision
```

Without a ticket system the decision is recorded and the run ends `approved`, with no ticket.
With one (step 7) it ends `actioned`, and `action_result` names the ticket.

## 7. Optional parts

| Part | Turn it on |
|---|---|
| Knowledge graph (W05) | `NEO4J_PASSWORD` and `INCIDENTOPS_NEO4J_URI=bolt://neo4j:7687` and `INCIDENTOPS_NEO4J_PASSWORD` in `.env`; `docker compose --profile graph up -d`; then `docker compose run --rm -v "$PWD/data:/data:ro" api python -m incidentops_ai.graph.load --catalogue /data/graph/catalogue.toml --corpus /data/corpus` |
| Ticket system (W09) | `INCIDENTOPS_JIRA_URL`, `INCIDENTOPS_JIRA_TOKEN` (and `INCIDENTOPS_JIRA_PROJECT`) |
| Or the bundled ticket server (W08) | `INCIDENTOPS_TICKETS_DB=/var/lib/incidentops/tickets.db` and `INCIDENTOPS_APPROVAL_SECRET=<openssl rand -hex 24>` |
| CMDB (W09) | `INCIDENTOPS_CMDB_URL`, `INCIDENTOPS_CMDB_API_KEY` |
| Ops questions (W09/W10) | create a login and map it to a tenant (below), then `INCIDENTOPS_OPS_DATABASE_URL` |
| Fallback models (W11) | `INCIDENTOPS_LLM_ROUTES={"diagnosis":["model-a","model-b"]}` |
| Tracing (W11) | `INCIDENTOPS_TRACE_FILE=/var/lib/incidentops/traces.jsonl`, or `INCIDENTOPS_LANGFUSE_HOST` and both keys |

After changing `.env`, run `docker compose up -d` again: the API container is recreated with the
new settings. Every setting in `.env.example` reaches the container; a blank one means its
default. Files the API writes go in the `appdata` volume, mounted at `/var/lib/incidentops`.

The ops reader login (run in the database, e.g. `docker compose exec db psql -U incidentops`):

```sql
CREATE ROLE ops_reader LOGIN PASSWORD '<random>' IN ROLE incidentops_ops_reader;
ALTER ROLE ops_reader SET default_transaction_read_only = on;
ALTER ROLE ops_reader SET statement_timeout = '2s';
INSERT INTO ops.tenant_readers VALUES ('ops_reader', 'default');
```

## 8. Operate

- Health and degraded modes: `GET /status`, [runbook.md](runbook.md).
- Upgrades: `git pull && docker compose up -d --build` (migrations run on start).
- Backups: `docker compose exec db pg_dump -U incidentops incidentops > backup.sql`; with the
  bundled ticket server, also copy its database out of the `appdata` volume
  (`docker compose cp api:/var/lib/incidentops/tickets.db .`).
- Stop: `docker compose down` keeps the data; `docker compose down -v` deletes it.
