# IncidentOps AI

IncidentOps triages production incidents. You send it an incident report; it finds the runbooks
and postmortems that bear on it, asks a model for a diagnosis that must cite them, adds what it
knows about the service (owner, on-call, dependencies), and proposes one action, a ticket, that a
person approves before anything is written.

It is built to be trusted with that job rather than to look clever:

- **Every cause cites its evidence**, and the citations are checked against the retrieved text.
- **The model never decides what happens next.** The workflow is a fixed graph; the route
  (recommend or send to review) and the recommendation are code.
- **Nothing is written without a person's approval**, and the write carries a signature over its
  exact arguments.
- **Every failure ends in a named state**: a dependency that is down or slow sends the run to review
  with a reason, never to a guess. `GET /status` says how degraded the service is.

v1.0 is the end of a thirteen-week build ([ROADMAP.md](docs/ROADMAP.md)). What it cannot do yet:
[known limitations](docs/known-limitations.md).

## Quick start (Docker)

You need Docker with Compose, and an OpenAI API key for triage.

```bash
cp .env.example .env    # set POSTGRES_PASSWORD, INCIDENTOPS_API_TOKEN (openssl rand -hex 24),
                        # OPENAI_API_KEY and INCIDENTOPS_LLM_MODEL
docker compose up -d --build
docker compose run --rm -v "$PWD/data/corpus:/corpus:ro" api \
    python -m incidentops_ai.retrieval.ingest /corpus
docker compose run --rm api python -m incidentops_ai.retrieval.index
curl -s -H "Authorization: Bearer <your token>" localhost:8000/status    # "tier": "FULL"
```

Then create an incident and triage it: [deploy.md](docs/deploy.md) walks through it, including the
optional parts (the knowledge graph, a ticket system, the CMDB, ops questions, tracing). That guide
was followed from an empty Docker volume for v1.0.

## How a triage works

```
intake → retrieve ∥ graph ∥ memory ∥ CMDB → extract ∥ diagnose → assess → recommend | review → approval → act
```

Two steps call a model: extraction (the incident's fields) and diagnosis (causes citing the
evidence). `assess` turns problems into reason codes (`no_answer`, `unsupported_causes`, `sev1`,
`model_failed`, …); any reason sends the run to review. Every run then waits at the approval gate.
A triage takes about 6.5 seconds and costs about $0.019 in model calls (at an assumed price).
The full picture, boundary by boundary: [architecture.md](docs/architecture.md).

## The API

All routes except `/health` need `Authorization: Bearer <token>`: a JWT from your identity provider
(roles `viewer`, `responder`, `approver`, `admin`) or the static machine token. Interactive docs
at `/docs`.

| Route | What it does |
|---|---|
| `POST /incidents` | create an incident (`Idempotency-Key` supported) |
| `GET /incidents/{id}` | read it |
| `POST /incidents/{id}/attachments` | attach a screenshot or PDF; its text becomes evidence |
| `POST /incidents/{id}/triage` | run the workflow; stops at the approval gate |
| `GET /incidents/{id}/triage[/{run}]` | the runs, or one run with its steps, diagnosis and proposal |
| `POST /incidents/{id}/triage/{run}/decision` | `{"decision": "approve" \| "reject"}` |
| `POST /incidents/{id}/triage/{run}/resume` | resume a failed run without repeating finished steps |
| `GET /incidents/{id}/triage/{run}/events` | the append-only audit trail |
| `POST /incidents/{id}/extraction`, `/diagnosis`, `/related-incidents` | the single steps on their own |
| `GET /memory`, `POST /memory`, `POST /memory/{note}/supersede` | reviewer notes recalled in later diagnoses |
| `POST /ops/query` | a question about operational data, answered with validated read-only SQL |
| `GET /status` | `FULL`, `REDUCED` or `MANUAL`, and each dependency's state |

Errors are `application/problem+json` with a stable `code`.

## Results

Every number links to a committed result and the command that reproduces it. The sets are small
and were written by the assistant that built the system: read them as evidence of the design, not
as accuracy on real incidents.

| What | Result | Details |
|---|---|---|
| Retrieval, 30 questions | Recall@5 1.000, MRR@10 0.967 (hybrid) | [evaluation](docs/reports/evaluation.md#retrieval-w03-w04) |
| Diagnosis, 16 golden incidents | answers or declines correctly 15/16; citation precision 1.000 | [evaluation](docs/reports/evaluation.md#generation-w04-w11) |
| Workflow, 12 cases, real model | task success 11/12 | [evaluation](docs/reports/evaluation.md#workflow-w07-w08) |
| Text-to-SQL, 15 questions | 14/15 correct; 20/20 attack queries refused | [evaluation](docs/reports/evaluation.md#text-to-sql-w09), [security](docs/reports/security.md) |
| Injection, real model | 0 of 6 followed | [security](docs/reports/security.md) |
| Load, 100 concurrent responders | 0 errors, triage P95 9.1 s (simulated model) | [performance](docs/reports/performance-and-cost.md) |
| The faithfulness judge | 77% agreement with labels: **reported, not gated** | [evaluation](docs/reports/evaluation.md#generation-w04-w11) |

## Development

Python 3.13, [uv](https://docs.astral.sh/uv/), and PostgreSQL 17 with
[pgvector](https://github.com/pgvector/pgvector):

```bash
docker run -d --name incidentops-pg -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=incidentops \
  -p 5433:5432 pgvector/pgvector:pg17
uv sync
cp .env.example .env   # INCIDENTOPS_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5433/incidentops
uv run alembic upgrade head
uv run python -m incidentops_ai.retrieval.ingest data/corpus
uv run uvicorn app.main:app --app-dir apps/api --reload
```

Tests use a real PostgreSQL and never call a model:

```bash
docker exec incidentops-pg createdb -U postgres incidentops_test
export TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5433/incidentops_test
uv run pytest                     # 960 tests
uv run pytest -m redteam          # the 170 security tests on their own
uv run ruff check . && uv run ruff format --check .
INCIDENTOPS_DATABASE_URL=$TEST_DATABASE_URL uv run python -m incidentops_ai.evals.gate
```

The gate checks the offline evaluation layers against `evals/thresholds.toml` and fails when a
prompt changed since the recorded real-model run. Optional extras: `uv sync --extra ocr` (OCR for
attachments), `--extra rerank` (the cross-encoder, off by default).

## Operating it

- [deploy.md](docs/deploy.md): installing, upgrading, backups.
- [runbook.md](docs/runbook.md): what users see and what to do when each dependency fails.
- [cost-model.md](docs/cost-model.md): per-triage and monthly estimates, and how to cap spend.
- [threat-model.md](docs/threat-model.md): what is defended, how, and the residual risks.
- [known-limitations.md](docs/known-limitations.md).

## Documentation

| Read this | For |
|---|---|
| [architecture.md](docs/architecture.md) | the system, boundary by boundary, with failure behaviour |
| [adr/](docs/adr/README.md) | the nine decisions and their evidence |
| [reports/](docs/reports/README.md) | evaluation, performance and cost, security |
| [ROADMAP.md](docs/ROADMAP.md) | the plan, the risk list, how each week went |
| `docs/roadmap-wNN.md` | each week's design and pre-registered hypotheses |
| `evals/*/runs/` | every recorded result, with its findings |
| [release/](docs/release/v1.0-checklist.md) | the v1.0 checklist, release notes and walkthrough script |

## Layout

- `apps/api/app`: the HTTP layer (app factory and lifespan, routes, auth dependencies, errors)
- `src/incidentops_ai`:
  - `llm/`: providers, the gateway (routes, fallback, breakers), retries, recorded runs
  - `extraction/`, `diagnosis/`: the two model steps and their checks
  - `retrieval/`: ingestion, chunking, embeddings, dense, BM25 and hybrid retrieval, benchmarks
  - `graph/`: the service catalogue in Neo4j
  - `multimodal/`: attachments, PDF text, OCR, vision transcripts
  - `workflow/`: the LangGraph triage graph, routing, the runner, checkpoints, fault injection
  - `memory/`: reviewer notes, append-only
  - `tickets/`, `integrations/`, `tools/`: the MCP ticket server and signed approvals, Jira and CMDB adapters, read-only tools
  - `ops/`: text-to-SQL behind a validator and a read-only login
  - `auth/`, `security/`: JWTs, roles, PII redaction, the model guard, injection flags
  - `evals/`, `observability/`: evaluation layers and the CI gate; tracing and exporters
  - `reliability/`: load and burst tests, the status tiers
- `data/`: the synthetic corpus and service catalogue
- `evals/`: datasets, thresholds, recorded runs and findings
- `migrations/`: Alembic
- `docs/`: everything above

## How it was built

Thirteen weekly stages, each with a design written before the code, hypotheses fixed before the
measurement, and a findings file afterwards (including the hypotheses that were refuted):

| Week | Stage | Design |
|---|---|---|
| W01 | API, incidents, idempotency, Docker, CI | [ROADMAP](docs/ROADMAP.md) |
| W02 | structured extraction, LLM failure handling | [w02](docs/roadmap-w02.md) |
| W03 | retrieval foundations | [w03](docs/roadmap-w03.md) |
| W04 | hybrid retrieval, cited diagnosis | [w04](docs/roadmap-w04.md) |
| W05 | knowledge graph | [w05](docs/roadmap-w05.md) |
| W06 | multimodal evidence | [w06](docs/roadmap-w06.md) |
| W07 | the LangGraph workflow | [w07](docs/roadmap-w07.md) |
| W08 | approval gate, memory, MCP | [w08](docs/roadmap-w08.md) |
| W09 | integrations, text-to-SQL | [w09](docs/roadmap-w09.md) |
| W10 | identity, RBAC, AI security | [w10](docs/roadmap-w10.md) |
| W11 | evaluation layers, gateway, tracing | [w11](docs/roadmap-w11.md) |
| W12 | reliability and failure engineering | [w12](docs/roadmap-w12.md) |
| W13 | the v1.0 release | [w13](docs/roadmap-w13.md) |
