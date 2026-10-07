# IncidentOps AI

Production-grade incident triage and diagnosis service. Roadmap: [docs/ROADMAP.md](docs/ROADMAP.md).

## Local development

Requires Python 3.13, [uv](https://docs.astral.sh/uv/) and PostgreSQL.

```bash
uv sync
createdb incidentops && createdb incidentops_test
cp .env.example .env
uv run alembic upgrade head
uv run uvicorn app.main:app --app-dir apps/api --reload
```

API docs: http://localhost:8000/docs

## Tests

```bash
uv run pytest
```

Tests run Alembic migrations against `incidentops_test` (override with `TEST_DATABASE_URL`).

## Layout

- `apps/api/app` — HTTP layer (FastAPI app factory, routes, dependencies)
- `src/incidentops_ai` — domain models, persistence, config (reused by workers/evals later)
- `migrations` — Alembic schema migrations
- `docs` — roadmap, notes, ADRs
