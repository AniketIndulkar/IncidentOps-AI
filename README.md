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

## Knowledge graph (W05)

Services, teams, engineers, incidents, fixes and runbook coverage live in a Neo4j graph built from
`data/graph/catalogue.toml` (synthetic, validated against the corpus). The diagnosis response adds a
`service_context` from it: owning team, on-call engineer, dependencies, dependents and recent
incidents. The comparison found no retrieval gain from graph context, so retrieval does not use it by
default ([findings](evals/graph/runs/2026-10-17-graph-findings.md), [ADR-002](docs/adr/002-knowledge-graph.md)).

```bash
docker run -d --name incidentops-neo4j -p 7687:7687 -e NEO4J_AUTH=neo4j/<password> neo4j:5-community
uv run python -m incidentops_ai.graph.load          # needs INCIDENTOPS_NEO4J_URI and _PASSWORD
uv run python -m incidentops_ai.graph.benchmark     # graph vs no graph on two question sets
```

Tests need a second container for the graph (port 7688, as in CI):

```bash
docker run -d --name incidentops-neo4j-test -p 7688:7687 -e NEO4J_AUTH=neo4j/incidentops-test neo4j:5-community
```

## Multimodal evidence (W06)

Incidents take attachments (PNG, JPEG or PDF, up to 5 MB): `POST /incidents/{id}/attachments` with a
multipart `file`. Text is extracted at upload (PDF text layer, OCR for scans, a vision-model
transcript for images) and the diagnosis cites it as evidence `A1..An`
([findings](evals/multimodal/runs/2026-10-17-multimodal-findings.md), [ADR-003](docs/adr/003-multimodal-routing.md)).

```bash
uv sync --extra ocr                                            # local OCR (RapidOCR, ~250 MB with OpenCV)
uv run python -m incidentops_ai.multimodal.assets              # regenerate the benchmark assets
uv run --extra ocr python -m incidentops_ai.multimodal.benchmark   # OCR vs vision on 28 questions
```

## Incident workflow (W07)

`POST /incidents/{id}/triage` runs the triage workflow (LangGraph): intake, retrieval and service
context, extraction and diagnosis side by side, then a routing rule in code that sends the incident to
**recommend** (cited runbooks, on-call, open fixes) or **review** with reason codes. Every step is
checkpointed in PostgreSQL; `GET /incidents/{id}/triage/{run_id}` shows the state and step log, and
`POST /incidents/{id}/triage/{run_id}/resume` continues a failed run without repeating finished steps
([findings](evals/workflow/runs/2026-10-17-workflow-findings.md), [ADR-004](docs/adr/004-agentic-vs-deterministic-steps.md)).
The checkpoint tables are created by LangGraph on first use, not by Alembic.

```bash
uv run python -m incidentops_ai.workflow.benchmark --overhead   # LangGraph vs a plain function, scripted (free)
uv run python -m incidentops_ai.workflow.benchmark              # 12 incidents, real models (scratch DB, costs)
```

## Layout

- `apps/api/app` — HTTP layer (FastAPI app factory, routes, dependencies)
- `src/incidentops_ai` — domain models, persistence, config (reused by workers/evals later)
  - `llm/`, `extraction/`, `tools/` — provider layer, structured extraction, read-only tools (W02)
  - `retrieval/` — documents, chunking, embeddings, retrievers, metrics, benchmark (W03); BM25, hybrid, reranker, stacks (W04)
  - `diagnosis/` — evidence numbering, citation check, cited diagnosis (W04)
  - `graph/` — service catalogue, Neo4j store and Cypher, graph-anchored retrieval (W05)
  - `multimodal/` — attachments, PDF text, OCR, vision transcripts, the multimodal benchmark (W06)
  - `workflow/` — the LangGraph triage workflow, routing rule, runs and checkpoints, benchmark (W07)
- `data/corpus` — runbooks and postmortems (synthetic) for retrieval
- `data/graph` — the service catalogue the knowledge graph is built from (synthetic)
- `evals` — extraction samples (W02), retrieval questions and benchmark runs (W03, W04), diagnosis spot check (W04), graph questions (W05), multimodal benchmark (W06), workflow cases and runs (W07)
- `migrations` — Alembic schema migrations
- `docs` — roadmap, notes, ADRs
