# IncidentOps AI

Production-grade incident triage and diagnosis service. Roadmap: [docs/ROADMAP.md](docs/ROADMAP.md).

## Local development

Requires Python 3.13, [uv](https://docs.astral.sh/uv/), and PostgreSQL 17 **with the
[pgvector](https://github.com/pgvector/pgvector) extension** (the retrieval tables need it). The
simplest way to get one is the image the project uses in CI and docker compose:

```bash
docker run -d --name incidentops-pg -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=incidentops \
  -p 5433:5432 pgvector/pgvector:pg17
```

```bash
uv sync
cp .env.example .env   # set INCIDENTOPS_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5433/incidentops
uv run alembic upgrade head
uv run uvicorn app.main:app --app-dir apps/api --reload
```

API docs: http://localhost:8000/docs

## Tests

```bash
docker exec incidentops-pg createdb -U postgres incidentops_test
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5433/incidentops_test uv run pytest
```

Tests run Alembic migrations against a real PostgreSQL with pgvector (default
`postgresql+asyncpg://localhost:5432/incidentops_test`, override with `TEST_DATABASE_URL`). They
never call a model: embeddings and LLM calls are faked, so no API key is needed.

## Retrieval (W03)

Runbooks and postmortems in `data/corpus/` are parsed, chunked, embedded into pgvector and
searched. Ground truth is in `evals/retrieval/questions.jsonl` (30 questions, each pointing at the
exact text that answers it). Design and decisions: [docs/roadmap-w03.md](docs/roadmap-w03.md).

```bash
uv run python -m incidentops_ai.retrieval.ingest data/corpus                         # parse, chunk, store
uv run python -m incidentops_ai.retrieval.index                                       # embed (needs OPENAI_API_KEY)
uv run python -m incidentops_ai.retrieval.benchmark                                   # Recall@K, Precision@K, MRR -> evals/retrieval/runs/
uv run python -m incidentops_ai.retrieval.chunking_experiment                         # compare chunking configs
```

Ingestion is repeatable: unchanged documents are skipped. The default chunker is provisional
(`fixed-1000-100`) until the chunking experiment has run with real embeddings; choose another with
`--chunker`. Add `--fake` to the benchmark and
experiment commands for an offline wiring check (clearly labelled; not a measurement).

## Layout

- `apps/api/app` — HTTP layer (FastAPI app factory, routes, dependencies)
- `src/incidentops_ai` — domain models, persistence, config (reused by workers/evals later)
  - `llm/`, `extraction/`, `tools/` — provider layer, structured extraction, read-only tools (W02)
  - `retrieval/` — documents, chunking, embeddings, retrievers, metrics, benchmark (W03)
- `data/corpus` — runbooks and postmortems (synthetic) for retrieval
- `evals` — extraction samples (W02), retrieval questions and benchmark runs (W03)
- `migrations` — Alembic schema migrations
- `docs` — roadmap, notes, ADRs
