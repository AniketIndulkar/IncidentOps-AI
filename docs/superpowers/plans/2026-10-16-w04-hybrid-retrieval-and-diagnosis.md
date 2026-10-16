# W04 Hybrid Retrieval and Cited Diagnosis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add BM25, RRF fusion and a cross-encoder reranker behind the `Retriever` interface, measure Dense vs BM25 vs Hybrid vs Hybrid+rerank on the 30-question eval set, and serve a diagnosis whose every cause carries a verified citation.

**Architecture:** Every stage implements `Retriever.retrieve(query, *, k, filters)`; stages wrap each other (`Hybrid(dense, bm25)`, `Reranking(hybrid)`). One benchmark module scores any stack through the existing `run_benchmark`. The diagnosis endpoint retrieves with the chosen stack, numbers the evidence `E1…En`, asks the LLM for causes with `(evidence_id, quote)` citations, and checks every quote against the chunk text on the server before showing anything.

**Tech Stack:** Python 3.13, numpy, SQLAlchemy async + pgvector, FastAPI, pytest. Reranker runtime (optional extra `rerank`): `fastembed` (ONNX) first, `sentence-transformers` as fallback.

**Spec:** [docs/roadmap-w04.md](../../roadmap-w04.md) (approved 16 Oct 2026, commit f0f99c8). Read it first; this plan only adds names, signatures and tests.

## Global Constraints

- BM25: `k1 = 1.2`, `b = 0.75`, not tuned. IDF computed per tenant over that tenant's whole corpus; filters restrict candidates after scoring.
- RRF: `score = Σ 1/(60 + rank)`, equal weights, each list cut at its top **50**, fused by `chunk.id`.
- Rerank: top **20** of the hybrid list, return top 10. Model `cross-encoder/ms-marco-MiniLM-L-6-v2`. Input = `chunk.embedding_text(with_context=True)`, cut at 512 tokens.
- Reranker is an **optional extra** (`rerank`); CI and the default image never import it. All tests are offline: fake embedder, fake reranker, no API key, no model download.
- "Local too heavy" bars: p95 added latency > **300 ms** for 20 candidates, or memory added > **500 MB**.
- Stack choice rule: adopt a stage over the previous one if MRR rises ≥ **0.02** on at least one decision config and falls ≤ 0.02 on the other, and end-to-end p95 < **1,000 ms**; otherwise the simpler stage wins. Decision configs: `fixed-2000-200` and `section-1500`+context header. Plain `section-1500` is exploratory only.
- Diagnosis: service filter **off**; no score threshold; no new eval questions; prompt `diagnose_incident/v1`; run operation `diagnose_incident`.
- Ask the user before the first Hugging Face model download (about 90 MB). Never `git push`, tag, or touch Jira without being asked. Stage files **by path** (never `git add -A` or `docs/` as a whole; `docs/blog/` and `.codegraph/` stay untracked).
- Code style: ruff, line length 100, docstring comments in the style of neighbouring files. Every commit message ends with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- Test command (Postgres 14 has no pgvector; the container `incidentops-pg-test` on port 5433 must be running):
  `TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5433/incidentops_test uv run pytest`

## Review Focus

Behaviours the spec implies but a first draft would miss, most likely first. Each has a test in the task named.

1. A query that is empty or only stop words must give `[]` from BM25, not an error or a division by zero (Task 1).
2. `k` larger than the candidates, and a chunk present in both fused lists, must not produce fewer-than-possible or duplicate results (Task 3).
3. A citation quote that differs from the chunk only in line breaks or runs of spaces must be accepted; one that differs in case, is too short, or is only in a *different* chunk must be rejected (Task 6).
4. After a document is re-ingested, the API's BM25 index must not keep serving the old text (Task 7).
5. A reranker returning the wrong number of scores must raise, never silently misorder (Task 4).

---

### Task 1: BM25 retriever (AIENG-29)

**Files:**
- Create: `src/incidentops_ai/retrieval/bm25.py`
- Test: `tests/test_retrieval_bm25.py` (reuse the `chunks_of` helper pattern from `tests/test_retrieval_retriever.py`)

**Interfaces:**
- Consumes: `Chunk`, `Chunk.embedding_text(with_context=)`, `RetrievalFilter.allows`, `RetrievedChunk`.
- Produces:
  - `def tokenize(text: str) -> list[str]`
  - `class Bm25Retriever:` with `name = "bm25"`, `__init__(self, chunks: Sequence[Chunk], *, with_context: bool = False, k1: float = 1.2, b: float = 0.75)`, `async def retrieve(self, query: str, *, k: int, filters: RetrievalFilter) -> list[RetrievedChunk]`

- [ ] **Step 1: Write failing tests**
  - `test_tokenize_keeps_identifiers_whole_and_in_parts`: `tokenize("PAY-1042 INDEXER_BATCH_SIZE")` contains `pay-1042`, `pay`, `1042`, `indexer_batch_size`, `indexer`, `batch`, `size`; all lowercase.
  - `test_tokenize_drops_stop_words`: `"what is the memory limit"` → no `what`, `is`, `the`; keeps `memory`, `limit`.
  - `test_rare_term_beats_common_term`: three chunks, only one contains `keepalive`; query `"keepalive mismatch"` ranks it first, ranks are `[1,2,3]`, scores descending.
  - `test_shorter_chunk_wins_when_term_counts_tie`: length normalisation (`b`) puts the short chunk above the long one.
  - `test_identifier_query_matches_the_joined_form`: query `PAY-1042` ranks the chunk containing `PAY-1042` first.
  - `test_empty_and_stop_word_only_queries_return_nothing`: `""`, `"   "`, `"what is the"` → `[]` (Review Focus 1).
  - `test_tenant_isolation_in_results_and_in_statistics`: another tenant's chunks never returned, and adding them does not change the first tenant's scores.
  - `test_service_filter_does_not_change_scores`: a chunk's score is identical with and without a service filter (IDF is corpus-wide); service and doc-type filters narrow the candidates.
  - `test_k_must_be_positive_and_ties_keep_corpus_order`: `k=0` → `ValueError`; two identical chunks come back in corpus order.
  - `test_context_header_is_searchable_only_when_enabled`: a word that only appears in the title matches with `with_context=True` and not otherwise.
- [ ] **Step 2: Run** `uv run pytest tests/test_retrieval_bm25.py -v` → FAIL (module missing).
- [ ] **Step 3: Implement** `bm25.py`. Tokeniser: lowercase; find runs of `[a-z0-9]+` joined by `-`, `_` or `.`; emit the joined run when it has a joiner, plus each part; drop a fixed English stop list (about 40 words, defined as a module constant). No stemming. Build per-tenant statistics (document frequency, average length) in `__init__`; score only chunks passing `filters.allows`; the Robertson-Spärck Jones IDF `ln(1 + (N - n + 0.5)/(n + 0.5))` so it never goes negative. Skip zero-score chunks.
- [ ] **Step 4: Run** the new tests and `uv run ruff check . && uv run ruff format --check .` → PASS.
- [ ] **Step 5: Commit** `git add src/incidentops_ai/retrieval/bm25.py tests/test_retrieval_bm25.py` — `AIENG-29: BM25 retriever with identifier-aware tokeniser`.

---

### Task 2: Stack benchmark runner, dense and BM25 stages (AIENG-29)

**Files:**
- Create: `src/incidentops_ai/retrieval/stack_benchmark.py`
- Modify: `src/incidentops_ai/retrieval/benchmark.py` (make `_percentile` and `_snippet` public: `percentile`, `snippet`; update their uses)
- Test: `tests/test_retrieval_stack_benchmark.py`

**Interfaces:**
- Consumes: `run_benchmark(retriever, documents, questions, *, k, use_service_filter) -> BenchmarkResult`; `ExperimentConfig`; `InMemoryDenseRetriever.build`; `Bm25Retriever`; `load_corpus`; `load_questions`; `CachingEmbedder`, `FakeEmbedder`, `build_embedder`.
- Produces:
  - `@dataclass(frozen=True) class StageRun: stage: str; config: str; result: BenchmarkResult; mean_chars_read_at_5: float; extras: dict[str, float]` (`extras` holds per-stage measurements such as rerank load seconds)
  - `STAGES = ("dense", "bm25", "hybrid", "hybrid-rerank")`
  - `async def run_stages(config: ExperimentConfig, documents, questions, embedder, *, stages: Sequence[str], reranker: Reranker | None = None, k: int = 10) -> list[StageRun]`
  - `def questions_moved(before: StageRun, after: StageRun) -> tuple[list[str], list[str]]` — ids whose reciprocal rank improved / worsened.
  - `def render_stack_report(runs_by_config: Mapping[str, Sequence[StageRun]], *, embedding_model: str, run_date: str) -> str` and `write_stack_report(...)` writing `<date>-<name>.md` and `.json` to `evals/retrieval/runs/`.
  - CLI: `python -m incidentops_ai.retrieval.stack_benchmark [--stages dense,bm25,…] [--configs fixed-2000-200,section-1500+ctx,section-1500] [--name stack-comparison] [--date D] [--out-dir evals/retrieval/runs] [--fake]`

- [ ] **Step 1: Write failing tests** (all with `FakeEmbedder`, a small inline corpus or `data/corpus`):
  - `test_run_stages_returns_one_run_per_requested_stage_in_order`.
  - `test_unknown_stage_is_rejected_with_the_valid_names`.
  - `test_questions_moved_lists_improved_and_worse_ids`: build two `StageRun`s by hand where q1 improves and q2 worsens; q3 unchanged is in neither list.
  - `test_report_has_a_row_per_stage_per_config_and_lists_bm25_misses`: contains `| bm25 |`, a `Misses` heading, and the Recall@5, MRR, p50 and p95 columns.
  - `test_fake_embedder_report_carries_the_offline_banner`: same wording rule as `render_benchmark_report` (numbers from fake vectors are a wiring check).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** Build chunks with the config's chunker, the dense retriever with `InMemoryDenseRetriever.build(..., with_context=config.with_context)`, BM25 with the same `with_context`. Wrap each retriever in `run_benchmark`. Report: per config a table (Stage, Recall@1, Recall@5, Precision@5, Hit@1, MRR, chars read@5, mean/p50/p95 ms, est. embedding cost per 1,000 queries), a "questions moved vs previous stage" line with counts and ids, by-category table, and every miss. Cost: `EMBEDDING_USD_PER_MILLION_TOKENS = 0.02` times `len(query)/4` tokens, labelled **estimate**; BM25 and rerank rows cost 0.
- [ ] **Step 4: Run** tests and lint → PASS. Then a wiring check: `uv run python -m incidentops_ai.retrieval.stack_benchmark --fake --stages dense,bm25 --name wiring-check --out-dir <scratchpad>`; **do not commit its output**.
- [ ] **Step 5: Commit** both source files, the test and `benchmark.py` by path — `AIENG-29: stack benchmark runner with dense and BM25 stages`.

---

### Task 3: RRF hybrid retriever (AIENG-30)

**Files:**
- Create: `src/incidentops_ai/retrieval/hybrid.py`
- Modify: `src/incidentops_ai/retrieval/stack_benchmark.py` (add the `hybrid` stage)
- Test: `tests/test_retrieval_hybrid.py`

**Interfaces:**
- Consumes: `Retriever`, `Bm25Retriever`, `InMemoryDenseRetriever`, `PgVectorRetriever`.
- Produces:
  - `RRF_K = 60`, `CANDIDATE_DEPTH = 50`
  - `def reciprocal_rank_fusion(rankings: Sequence[Sequence[RetrievedChunk]], *, k: int = RRF_K) -> list[RetrievedChunk]` — fused list, ranks renumbered from 1, `score` = RRF score, descending; ties broken by the better of the two source ranks, then first appearance.
  - `class HybridRetriever:` `name = "hybrid-rrf"`, `__init__(self, dense: Retriever, sparse: Retriever, *, depth: int = CANDIDATE_DEPTH)`, `async def retrieve(...)` running both with `asyncio.gather`, each at `depth`, then truncating to `k`.

- [ ] **Step 1: Write failing tests**
  - `test_rrf_scores_match_the_formula`: chunk A rank 1 in both lists scores `2/61`; chunk B rank 1 in one list only scores `1/61`; A before B.
  - `test_chunk_in_both_lists_appears_once` and `test_k_larger_than_candidates_returns_all_unique_chunks` (Review Focus 2).
  - `test_tie_breaks_by_best_source_rank_then_first_seen`.
  - `test_hybrid_never_returns_other_tenants` (both sources contain an `acme` chunk).
  - `test_hybrid_with_pgvector_ranks_like_hybrid_in_memory` (needs the DB): ingest the corpus the way `tests/test_retrieval_store.py` does, with `FakeEmbedder`; for all 30 questions the chunk-id order from `HybridRetriever(PgVectorRetriever, Bm25Retriever)` equals the one from `HybridRetriever(InMemoryDenseRetriever, Bm25Retriever)`. For the BM25 side use chunks loaded from the same DB rows (add `async def load_chunks(session, tenant_id) -> list[Chunk]` to `store.py` if it does not exist).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** `hybrid.py`; add the stage to `run_stages`. **Step 4: Run** tests and lint → PASS.
- [ ] **Step 5: Commit** — `AIENG-30: reciprocal rank fusion of dense and BM25`.

---

### Task 4: Reranker (AIENG-31)

**Files:**
- Create: `src/incidentops_ai/retrieval/rerank.py`
- Modify: `src/incidentops_ai/retrieval/stack_benchmark.py` (add `hybrid-rerank`; record `load_seconds`, `rss_added_mb`, `truncated_candidates` in `StageRun.extras`; print the verdict against the two "too heavy" bars); `pyproject.toml` (optional extra); `uv.lock`
- Test: `tests/test_retrieval_rerank.py`; `tests/test_retrieval_rerank_real.py` (skipped unless the extra is installed and `RUN_REAL_RERANKER=1`)

**Interfaces:**
- Produces:
  - `class RerankerError(RuntimeError)`
  - `class Reranker(Protocol): name: str; async def score(self, query: str, passages: Sequence[str]) -> list[float]` — one score per passage, higher is better.
  - `class FakeReranker:` deterministic: fraction of the query's `tokenize()` tokens found in the passage.
  - `class CrossEncoderReranker:` `__init__(self, model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2", *, max_length: int = 512)`; loads lazily on first `score`, runs inference in `asyncio.to_thread`, exposes `load_seconds: float | None` and `truncated: int`; imports the runtime inside the loading method only.
  - `class RerankingRetriever:` `name = f"{base.name}+rerank"`, `__init__(self, base: Retriever, reranker: Reranker, *, depth: int = 20)`, `async def retrieve(...)`: base at `depth`, score `chunk.embedding_text(with_context=True)`, sort descending (stable), renumber ranks, return top `k`; `score` becomes the reranker score.

- [ ] **Step 1: Write failing tests**
  - `test_reranker_reorders_candidates_by_its_scores`: a base that returns A, B, C and a reranker preferring C → C first, ranks `[1,2,3]`.
  - `test_depth_limits_what_is_reranked`: only the first `depth` base results can appear.
  - `test_wrong_number_of_scores_raises_reranker_error` (Review Focus 5), and `test_empty_base_result_does_not_call_the_reranker`.
  - `test_other_tenants_are_still_excluded` (filters pass through to the base).
  - `test_fake_reranker_scores_overlap`.
  - Real test (skipped by default): the cross-encoder ranks the `undo` line passage of q05 above an unrelated passage.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** `rerank.py` and the benchmark additions. In `pyproject.toml` add `[project.optional-dependencies] rerank = ["fastembed>=…"]` using `uv add --optional rerank <pkg>`. If `fastembed` has no cross-encoder for this model, use `sentence-transformers` and say so in the commit message. **Step 4: Run** the full suite and lint → PASS (the real test is skipped).
- [ ] **Step 5: Commit** — `AIENG-31: cross-encoder reranker behind the retriever interface`.

---

### Task 5: Real comparison and findings (AIENG-33, part 1)

**Files:**
- Create: `evals/retrieval/runs/<date>-stack-comparison.md` and `.json` (written by the CLI), `evals/retrieval/runs/<date>-hybrid-findings.md` (by hand)
- Modify: `src/incidentops_ai/config.py` (add `retrieval_stack: Literal["dense", "hybrid", "hybrid-rerank"]`, default set from this task's result)

- [ ] **Step 1: Ask the user** for permission to download the reranker model (about 90 MB, Hugging Face) and confirm `OPENAI_API_KEY` is in `.env`. Do not continue without a yes.
- [ ] **Step 2: Run** `uv sync --extra rerank`, then `uv run python -m incidentops_ai.retrieval.stack_benchmark --configs fixed-2000-200,section-1500+ctx,section-1500 --name stack-comparison`. Query and chunk embeddings come from the W03 on-disk cache; only uncached texts cost anything (cents).
- [ ] **Step 3: Apply the stack rule** from the Global Constraints exactly as written. Write the findings file: the table, the chosen default and why, every regression (questions in `questions_moved(...)[1]`) read in the retrieved text and explained, how many questions moved behind each difference, the local-performance numbers against the 300 ms and 500 MB bars, and H7 to H12 each confirmed, refuted or partly, in the style of `2026-10-16-dense-baseline-findings.md`. If local is "too heavy", stop and report the fallback ladder to the user instead of choosing.
- [ ] **Step 4:** Set the `retrieval_stack` default; update the "Status" block of `docs/roadmap-w04.md` with a one-paragraph result.
- [ ] **Step 5: Commit** the two run files, the findings, `config.py` and `docs/roadmap-w04.md` by path — `AIENG-29, AIENG-30, AIENG-31, AIENG-33: real stack comparison and findings`.

---

### Task 6: Citation-checked diagnosis core (AIENG-32)

**Files:**
- Create: `src/incidentops_ai/diagnosis/__init__.py`, `models.py`, `evidence.py`, `service.py`; `src/incidentops_ai/prompts/diagnose_incident/v1.md`
- Test: `tests/test_diagnosis.py`

**Interfaces:**
- Consumes: `Retriever`, `RetrievedChunk`, `LLMProvider.generate_structured`, `load_prompt`, `FakeProvider` (see `tests/test_extraction.py` for scripting it), `ModelConfig`, `RunMetadata`, `LLMStatus`.
- Produces:
  - `models.py` (Pydantic, `extra="forbid"` on model-facing types): `ModelCitation(evidence_id: str, quote: str)`, `ModelCause(claim: str, citations: list[ModelCitation])`, `ModelDiagnosis(answer_status: Literal["answered","insufficient_evidence"], causes: list[ModelCause])`; response types `EvidenceItem(evidence_id, doc_id, title, source_path, section: str | None, page: int, char_start: int, char_end: int)`, `Citation(EvidenceItem fields + quote: str)`, `Cause(claim: str, citations: list[Citation])`, `Diagnosis(status: Literal["answered","no_answer"], no_answer_reason: Literal["no_evidence","insufficient_evidence","no_supported_cause"] | None, causes: list[Cause], dropped_causes: int, evidence: list[EvidenceItem])`.
  - `evidence.py`: `@dataclass(frozen=True) class Evidence: id: str; chunk: Chunk`; `number_evidence(results: Sequence[RetrievedChunk]) -> list[Evidence]` (`E1…En` in rank order); `render_evidence(evidence: Sequence[Evidence]) -> str`; `MIN_QUOTE_CHARS = 12`; `verify(model: ModelDiagnosis, evidence: Sequence[Evidence]) -> Diagnosis`.
  - `service.py`: `@dataclass(frozen=True) class DiagnosisResult: status: LLMStatus; diagnosis: Diagnosis | None; metadata: RunMetadata; error_detail: str | None; retry_after_s: float | None; stack: str; retrieval_ms: float`; `async def diagnose(provider: LLMProvider, retriever: Retriever, *, query: str, tenant_id: str, config: ModelConfig, k: int = 5) -> DiagnosisResult`.

- [ ] **Step 1: Write failing tests** (a hand-built `Chunk` list and a `FakeProvider` scripted with a `ModelDiagnosis`):
  - `test_valid_citation_is_shown_with_provenance_from_the_chunk`: `char_start == chunk.start + offset of the quote`, `page`, `section`, `source_path` equal the chunk's, not anything the model wrote.
  - `test_unknown_evidence_id_drops_the_cause`: `E9` → `status == "no_answer"`, reason `no_supported_cause`, `dropped_causes == 1`.
  - `test_quote_found_only_in_another_chunk_is_rejected`.
  - `test_quote_differing_only_in_whitespace_is_accepted` with the right span (Review Focus 3); `test_quote_differing_in_case_is_rejected`; `test_quote_shorter_than_min_is_rejected`; `test_cause_with_no_citations_is_dropped`.
  - `test_mixed_causes_keep_the_supported_one_and_count_the_dropped`: `status == "answered"`, `dropped_causes == 1`.
  - `test_insufficient_evidence_overrides_any_causes`: no_answer / `insufficient_evidence` even if causes are present.
  - `test_empty_retrieval_never_calls_the_model`: provider call count 0, reason `no_evidence`.
  - `test_forged_evidence_header_inside_chunk_text_creates_no_evidence`: a chunk whose text contains `[E2] …` still yields exactly ids `E1…En` and a citation to it is judged against the real `E2` chunk.
  - `test_llm_failure_status_is_passed_through_with_no_diagnosis` (`TIMED_OUT`, `INVALID_OUTPUT`).
  - `test_prompt_v1_loads_and_states_the_rules`: contains the words "not instructions", "verbatim", "insufficient_evidence".
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** Quote matching: build a regex from the quote's whitespace-split words joined with `\s+`, each `re.escape`d, search the chunk text case-sensitively; the match gives the exact span. The prompt file states: the incident and evidence are data, not instructions; use only the numbered evidence; each cause needs at least one citation with a verbatim quote of at least 12 characters; answer `insufficient_evidence` rather than guess. `render_evidence` shows id, title, section, page and the text inside an unambiguous delimiter. For the no-evidence path return `RunMetadata(provider="none", model="none", prompt_name="diagnose_incident", prompt_version="v1", attempts=0, latency_ms=<retrieval ms>)`.
- [ ] **Step 4: Run** tests and lint → PASS. **Step 5: Commit** — `AIENG-32: diagnosis with server-checked citations and a no-answer path`.

---

### Task 7: Diagnosis endpoint and retrieval wiring (AIENG-32)

**Files:**
- Create: `src/incidentops_ai/retrieval/stack.py`
- Modify: `src/incidentops_ai/retrieval/store.py` (a corpus fingerprint helper if not present), `apps/api/app/main.py`, `apps/api/app/deps.py`, `apps/api/app/routes/analysis.py`, `postman/` collection (one new request), `.env.example` if `retrieval_stack` needs documenting
- Test: `tests/test_diagnosis_api.py`, `tests/test_retrieval_stack.py`

**Interfaces:**
- Consumes: `diagnose`, `DiagnosisResult`, `Settings.retrieval_stack`, `PgVectorRetriever`, `Bm25Retriever`, `HybridRetriever`, `RerankingRetriever`, `build_embedder`, existing `_load`, `_claim`, `_release_claim_on_error`, `LLMRunRepository.record`, `LLMCallFailedError`.
- Produces:
  - `stack.py`: `class DbBm25Retriever` (`name = "bm25"`; `__init__(self, session_factory, *, tenant_id: str = DEFAULT_TENANT, with_context: bool = False)`); before each query it reads a cheap fingerprint (chunk count plus a hash of the ordered `(doc id, content_sha256)` pairs) and rebuilds the in-memory `Bm25Retriever` only when it changed. `async def build_retriever(stack: str, session_factory, embedder, reranker: Reranker | None = None) -> Retriever`.
  - `create_app(settings=None, *, llm_provider=None, retriever: Retriever | None = None)` stores `app.state.retriever` (built from settings when a key is set and none injected, else `None`); `get_retriever` dependency and `Retrieval = Annotated[Retriever, Depends(get_retriever)]` raise `LLMNotConfiguredError` when `None`.
  - Route `POST /incidents/{incident_id}/diagnosis` returning `DiagnosisResponse(run_id: uuid.UUID, incident_id: uuid.UUID, diagnosis: Diagnosis, retrieval: {stack: str, latency_ms: float}, run: RunMetadata)`; same auth, rate limit, `Idempotency-Key` replay (`Idempotent-Replayed: true`) and problem+json errors as the other two routes; the run is recorded with operation `diagnose_incident` and `output = {"diagnosis": …, "retrieval": …}`.

- [ ] **Step 1: Write failing tests**
  - `test_retrieval_stack.py`: `test_build_retriever_returns_each_named_stack` (dense, hybrid, hybrid-rerank with `FakeReranker`, unknown name raises `ValueError`); `test_bm25_index_follows_a_re_ingested_document` (Review Focus 4: ingest, query, change one document and re-ingest, query again without rebuilding the object → new text found, old text gone).
  - `test_diagnosis_api.py` (inject an in-memory hybrid retriever and a scripted `FakeProvider` through `create_app`): `401` without token and `404` for an unknown incident (no model call in either); `503` when no retriever is configured; `200` with a valid citation and full provenance in the body; a fabricated citation → `200`, `status == "no_answer"`, `dropped_causes == 1`; empty corpus → `200` `no_evidence` with zero model calls; replay with the same `Idempotency-Key` returns the same `run_id` with the replay header and one model call; a scripted `TIMED_OUT` → problem+json `504` and the idempotency key freed; the recorded `llm_runs` row has operation `diagnose_incident` and the evidence list in its output.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** Query text is `f"{incident.title}\n\n{incident.description}"` (same as `_report_text`); tenant is `DEFAULT_TENANT`; no service filter. Mirror the structure of the `related-incidents` route; load the incident in a short session before retrieval and the model call. **Step 4: Run** the full suite and lint → PASS.
- [ ] **Step 5: Commit** — `AIENG-32: POST /incidents/{id}/diagnosis with retrieval wiring`.

---

### Task 8: Real-model check, documentation, v0.1 readiness (AIENG-32, AIENG-33)

**Files:**
- Create: `evals/diagnosis/2026-…-real-model-check.md` (hand-written record), `docs/notes/week-04-hybrid-retrieval-and-diagnosis.md`, `docs/notes/retro-october-2026.md`
- Modify: `docs/ROADMAP.md` ("Current state", W04 block, risks 17/21), `docs/roadmap-w04.md` (status line), `README.md` (stack, `--extra rerank`, benchmark and diagnosis commands)

- [ ] **Step 1: Real-model check** (needs `.env` with `OPENAI_API_KEY`, `INCIDENTOPS_LLM_MODEL`, `INCIDENTOPS_API_TOKEN`): start the API against a database with the corpus ingested and indexed; create three incidents matching corpus runbooks and two that match nothing (for example a printer driver fault); `POST …/diagnosis` for each. Record per call: status, causes with citations, dropped count, latency, tokens. The off-corpus incidents should come back `insufficient_evidence`; if one does not, record it as a finding and name the limitation (no score threshold), do not hide it.
- [ ] **Step 2: Write the W04 note** (what was built, hypotheses result, what surprised, what is still weak) and the **October retrospective** (W01 to W04: what held, what slipped, what to change for November).
- [ ] **Step 3: Update `ROADMAP.md` and `README.md`**: tick AIENG-29 to 33, replace "Current state" with the W04 state (test count from the real run), add the stack table headline, adjust risks 17 and 21 with what was measured.
- [ ] **Step 4: Verify everything**: full test suite with the container, `uv run ruff check . && uv run ruff format --check .`, `uv run python -m incidentops_ai.retrieval.stack_benchmark --fake --name wiring-check --out-dir <scratchpad>` still runs, and `docker build .` succeeds (the default image must not need the extra).
- [ ] **Step 5: Commit** each doc by path — `AIENG-33: W04 note, October retrospective and roadmap update`.
- [ ] **Step 6: Report to the user, then stop.** Say what is committed and not pushed, and ask separately about: tagging `v0.1` (suggest tagging the merge commit on `main`), opening a PR, and updating the Jira tickets (AIENG-5, 29 to 33; AIENG-24 to 28 are also still To Do).
