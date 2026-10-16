# W03 roadmap: retrieval engineering foundations (19–23 Oct 2026)

Source: Jira epic **AIENG-4** and tasks **AIENG-24 to AIENG-28**, read 16 Oct 2026. All six were To Do.
Overall plan: [ROADMAP.md](ROADMAP.md). Built early, like W02.

## Goal of the week

Build the dense retrieval foundation and measure it, so that W04 (hybrid search, reranking, cited
diagnosis) improves on a number, not a feeling.

**Exit criterion (from the epic):** an ingestion pipeline, metadata, chunking experiments and a
reproducible retrieval benchmark exist.

```
corpus (runbooks, postmortems)
   │  parse + clean                 AIENG-25
   ▼
documents ──▶ chunks (+ provenance: source, section, page, char span, tenant)
   │  chunker chosen from evidence  AIENG-26
   ▼
embeddings ──▶ pgvector ──▶ Retriever(query, k, filters) ──▶ ranked chunks     AIENG-27
   │
   ▼
eval harness: Recall@K, Precision@K, MRR against 30 questions                  AIENG-28
```

## What already exists (from W01–W02)

PostgreSQL + Alembic, async SQLAlchemy, the `LLMProvider` layer, 143 offline tests, CI, extraction
output (service, error codes) that becomes retrieval metadata. Nothing in W03 changes the API.

## Decisions made up front

| Decision | Choice | Why |
|---|---|---|
| Vector store | **pgvector** in the existing Postgres | One fewer service; metadata filters and tenant isolation (W10) in the same database. Roadmap recommendation, now adopted. |
| Local/CI Postgres | `pgvector/pgvector:pg17` image | Local Homebrew Postgres is 14 without pgvector; CI and compose used `postgres:17-alpine`, which has no pgvector. |
| Embeddings | OpenAI `text-embedding-3-small`, 1536 dimensions, compared with `-large` shortened to the same size | Account already exists; no new heavy dependency. A fake embedder keeps every test offline. |
| Ground truth | **Exact evidence quotes**, not chunk ids | Chunking changes every chunk id. A quote stays valid under any chunking. |
| "Relevant chunk" | Chunk covers at least half of an evidence span | Overlap by one character should not count as finding the answer. |
| Corpus | 18 synthetic documents (11 runbooks, 7 postmortems), ~35k characters | Public incident reports do not match our services. Written to match the W02 sample services. |
| Retriever interface | `retrieve(query, k, filters)` with a **required tenant** | W05 graph, W06 multimodal and W10 permissions reuse it (ROADMAP hard dependency 2). |
| ANN index | None yet: exact scan | At hundreds of chunks an exact scan is faster than HNSW and has perfect recall. Revisit near ~10k chunks. |

## Hypotheses, written before any retrieval code or number

If these are wrong, that is a finding, not a failure.

- **H1.** Section-aware chunks (split at headings) beat fixed-size windows on MRR, because a runbook
  step lives under its heading.
- **H2.** Very small chunks (500 characters) have the best Precision@K but lose Recall@K on
  procedure and multi-span questions, because the answer is split.
- **H3.** Very large chunks (2000 characters) lower MRR: one embedding for several topics blurs.
- **H4.** Dense retrieval is weakest on `identifier` questions (error codes, commands, variable
  names), which BM25 should win. This motivates AIENG-29.
- **H5.** Passing the incident's service as a filter raises Precision@K on service-scoped questions.
- **H6.** `text-embedding-3-large` beats `-small` by less than 5 points of Recall@5, so it does not
  earn about six times the price.

## How the chunking config is chosen (fixed before the experiment)

1. Run every config on all 30 questions, unfiltered, same embedding model.
2. Pick the highest **MRR**.
3. If two configs are within 0.02 MRR, pick the one with the higher Recall@5, then the one with the
   smaller mean chunk size (less context to pay for later).
4. Record at least three qualitative failures per config class before writing the decision.

## Day by day

Each ticket is timeboxed to 2 hours.

### AIENG-24 · Corpus, eval set, mental model

- **Corpus:** `data/corpus/runbooks/` (10 markdown + 1 paged text export) and `data/corpus/postmortems/`.
  Front matter carries `id`, `type`, `title`, `service`.
- **Eval set:** `evals/retrieval/questions.jsonl`, 30 questions, 42 evidence spans. Categories:
  8 `lookup`, 5 `identifier`, 8 `paraphrase`, 4 `procedure`, 5 `multi` (answer needs two places).
  Each question has an optional `service` for the filter experiment.
- **Note:** [notes/retrieval-mental-model.md](notes/retrieval-mental-model.md).
- **Done when:** the eval set is committed before any retrieval tuning. It was: this ticket's commit
  contains no retrieval code.
- The reading part of the ticket (retrieval, chunking and embedding chapters) is yours; the note
  records what the implementation assumes, so you can check it against the book.

### AIENG-25 · Ingestion and metadata

Parse, clean, chunk, persist. Every chunk keeps source path, document id and type, service, tenant,
section path, page and character span. `incidentops_ai.retrieval.ingest` is repeatable: unchanged
documents are skipped, changed ones are replaced.

### AIENG-26 · Chunking experiments

Five configs against the 30 questions with an in-memory retriever; decision rule above.

### AIENG-27 · Embeddings, vector store, dense retriever

Embedder interface (OpenAI and fake), `chunks.embedding vector(1536)`, `PgVectorRetriever` with
tenant, service and document-type filters, and a parity test against the in-memory retriever.

### AIENG-28 · Benchmark and findings

Recall@K, Precision@K, MRR; at least five misses inspected; written baseline in
`evals/retrieval/runs/`. Re-runnable with one command.

## Risks

1. **The corpus and the questions have one author.** Synthetic data and a small set overfit
   easily. Mitigation: paraphrase and identifier categories; W11 (AIENG-64) expands the golden set.
2. **Precision@K is capped.** Most questions have one or two evidence spans, so Precision@5 cannot
   exceed about 0.4. Compare configs with it, do not read it as an absolute score.
3. **Embedding results can drift** when the provider updates a model. Mitigation: model name and run
   date go in every report; embeddings are cached by content hash.
4. **The Postgres image changes** (alpine to Debian) for compose and CI. A reused data volume could
   have collation differences; the Lightsail deploy is recreated each time, so this is acceptable.
5. **Scope creep into hybrid search.** BM25 and fusion are W04.

## Carried into W04

The retriever interface, the eval harness and the baseline table. W04 adds BM25, RRF and a
cross-encoder behind the same interface and re-runs the same 30 questions.
