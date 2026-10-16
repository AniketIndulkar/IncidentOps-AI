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

Ingestion is repeatable: unchanged documents are skipped. The default chunker is `fixed-2000-200`,
chosen by the chunking experiment (see the
[findings](evals/retrieval/runs/2026-10-16-dense-baseline-findings.md), including its caveat for long
documents); choose another with `--chunker`. Dense baseline on this corpus: Recall@5 0.983, MRR 0.961.
Add `--fake` to the benchmark and
experiment commands for an offline wiring check (clearly labelled; not a measurement).

## Hybrid retrieval and cited diagnosis (W04)

Dense search and BM25 are fused by reciprocal rank (the default stack, chosen by a rule fixed before the
run; see the [findings](evals/retrieval/runs/2026-10-16-hybrid-findings.md) and
[docs/roadmap-w04.md](docs/roadmap-w04.md)). A cross-encoder reranker is built and measured but off by
default: it did not earn its cost on this corpus.

```bash
uv run python -m incidentops_ai.retrieval.stack_benchmark --fake              # offline wiring check, all stages
uv run python -m incidentops_ai.retrieval.stack_benchmark --stages dense,bm25,hybrid   # real run, no model download
uv sync --extra rerank                                                        # optional: the local reranker (~87 MB model on first use)
uv run --extra rerank python -m incidentops_ai.retrieval.stack_benchmark      # all four stages, three chunk configs
```

`POST /incidents/{id}/diagnosis` (same bearer token, rate limit and `Idempotency-Key` as the other LLM
routes) retrieves evidence, asks the model for likely causes and returns only causes whose citation the
server has checked against the stored chunk, with the source document, section, page and character span.
With nothing relevant it answers `no_answer`, never a guess. It needs the corpus ingested and indexed
(commands above), `OPENAI_API_KEY`, `INCIDENTOPS_LLM_MODEL` and `INCIDENTOPS_API_TOKEN`;
`INCIDENTOPS_RETRIEVAL_STACK` picks `dense`, `hybrid` (default) or `hybrid-rerank`.

## Layout

- `apps/api/app` — HTTP layer (FastAPI app factory, routes, dependencies)
- `src/incidentops_ai` — domain models, persistence, config (reused by workers/evals later)
  - `llm/`, `extraction/`, `tools/` — provider layer, structured extraction, read-only tools (W02)
  - `retrieval/` — documents, chunking, embeddings, retrievers, metrics, benchmark (W03); BM25, hybrid, reranker, stacks (W04)
  - `diagnosis/` — evidence numbering, citation check, cited diagnosis (W04)
- `data/corpus` — runbooks and postmortems (synthetic) for retrieval
- `evals` — extraction samples (W02), retrieval questions and benchmark runs (W03, W04), diagnosis spot check (W04)
- `migrations` — Alembic schema migrations
- `docs` — roadmap, notes, ADRs
