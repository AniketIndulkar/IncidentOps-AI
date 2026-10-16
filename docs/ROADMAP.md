# IncidentOps AI — Q4 2026 Roadmap

Source of truth: Jira project **AIENG** (80 issues: 13 weekly epics, 65 daily tasks, PREP, PARKING LOT).
This document is the plan derived from those tickets. Snapshot taken 2026-10-07.

## Goal

Ship **IncidentOps AI v1.0** by 1 January 2027: a production-grade incident triage and diagnosis
system that takes an incident report (text, screenshots, PDFs), extracts structured data, retrieves
grounded evidence from runbooks/postmortems/service graph/ops DB, produces a cited diagnosis, and
gates any risky action behind human approval — with evals, security, observability and failure
handling backed by evidence in the repo.

## Working rules (from tickets)

- **Timebox:** 2h per weekday ticket. Do not overrun; unfinished work rolls into the next fitting ticket.
- **WIP = 1:** no new AI repos before v1.0. New ideas go to AIENG-80 (Parking Lot); spikes ≤ 90 min under `/experiments`, with a keep/drop note.
- **One-in / one-out:** any new roadmap feature displaces an existing one.
- **Evidence over intuition:** eval sets are committed *before* tuning; choices are backed by benchmarks and ADRs.
- **Budget:** AWS ≈ £20/month with alert; LLM credit ≈ £20–30; no paid vector DB (pgvector/Qdrant local); Neo4j Aura free; Langfuse free/self-host.

## Target architecture (v1.0)

```
            ┌──────────── Auth (JWT/OAuth, RBAC, tenant context) ────────────┐
Client ──▶  FastAPI ingress (typed API, idempotency, structured errors)
              │
              ▼
        LangGraph workflow (checkpointed, bounded retries, loop detection)
        Intake → Extract → Retrieve ─┬─ Diagnose → Confidence ─┬─ Recommend
                                     │                         └─ HITL review (interrupt/approve)
              ┌──────────────────────┴───────────────┐
              ▼                ▼            ▼         ▼
       Hybrid retrieval   Graph context  Multimodal  Integrations
       dense(pgvector)    Neo4j/Aura     OCR | VLM   Jira/Slack adapter,
       + BM25 → RRF       Cypher         routing     read-only SQL + Text-to-SQL,
       → cross-encoder                               legacy SOAP adapter, MCP tool
              │
        LLM gateway (LiteLLM: routing, rate limits, fallback) → providers
              │
        Observability (Langfuse traces, latency/tokens/cost) · Evals in CI · Red-team suite in CI
        Storage: PostgreSQL (incidents, audit, checkpoints, memory) · Deploy: Docker + GitHub Actions + AWS
```

## Milestones

| Tag | Week | Date | Meaning |
|---|---|---|---|
| — | W01 | 9 Oct | Deployed typed API with persistence, idempotency, CI |
| **v0.1** | W04 | 30 Oct | Evidence-backed hybrid retrieval + grounded, cited diagnosis |
| **v0.2** | W08 | 27 Nov | Recoverable agent workflow with HITL approval and MCP trust boundary |
| **v1.0** | W13 | 1 Jan | Release: docs, reports, runbook, cost model, walkthrough |

## Phase plan

### Phase 1 — Foundation (W01–W02, 5–16 Oct)

**W01 · Production Python & FastAPI** (AIENG-1) — *exit: deployed API with tests, persistence, duplicate-request protection*

| Day | Ticket | Deliverable | Status |
|---|---|---|---|
| Mon | AIENG-2 | Repo skeleton, minimal FastAPI, asyncio vs Kotlin note | ✅ Done |
| Tue | AIENG-15 | Pydantic models, `POST /incidents`, `GET /incidents/{id}`, PostgreSQL | ✅ Done |
| Wed | AIENG-16 | Idempotency keys, structured errors, failure-path tests | ✅ Done |
| Thu | AIENG-17 | Dockerfile, GitHub Actions (lint/test/build), AWS deploy, budget alert | ✅ Done (CI green, PR #1) |
| Fri | AIENG-18 | E2E against deployed env, fix one real defect, Week 1 note | ✅ Done |

**W02 · LLM Application Engineering** (AIENG-3) — *exit: typed extraction that fails safely on malformed output/timeouts*

Detailed day-by-day plan: [roadmap-w02.md](roadmap-w02.md).

- ✅ AIENG-19 Provider abstraction: `generate_structured` / `tool_call` + model config.
- ✅ AIENG-20 Nested extraction schema (title, service, symptoms, error codes, env, severity, entities, missing info); 10 samples parse.
- ✅ AIENG-21 Fault injection (bad JSON, schema mismatch, timeout, 429); bounded retry; explicit failure states, no coercion.
- ✅ AIENG-22 One read-only tool, arg validation, parallel tool calls, persisted prompt/model version per run.
- ✅ AIENG-23 Failure matrix + **ADR-001: retry vs fallback vs human review vs rejection**.

### Phase 2 — Retrieval (W03–W04, 19–30 Oct) → **v0.1**

**W03 · Retrieval foundations** (AIENG-4) — *exit: ingestion pipeline, metadata, chunking experiments and a reproducible retrieval benchmark*

Detailed plan, hypotheses and decisions: [roadmap-w03.md](roadmap-w03.md). Built 16 Oct, ahead of schedule;
two measurements wait for a valid OpenAI key (see Current state).

- ✅ AIENG-24 Corpus of 18 runbooks/postmortems + **30-question eval set committed first** (ground truth = exact quotes, valid under any chunking).
- ✅ AIENG-25 Ingestion with source/page/section/char-span provenance and a tenant on every chunk; repeatable, one transaction.
- 🟡 AIENG-26 Five chunking configs, metrics and harness built; **selection pending** the real-embedding run (rule fixed in advance).
- ✅ AIENG-27 pgvector column, `PgVectorRetriever` (tenant/service/type filters in SQL), held to the same ranking as the in-memory reference. Embedding model `text-embedding-3-small` is provisional until the comparison runs.
- 🟡 AIENG-28 Benchmark runner (Recall@K, Precision@K, MRR, latency, per-style table, every miss listed); **baseline report and the ≥5-miss analysis pending** the real-embedding run.

**W04 · Hybrid, RRF, reranking** (AIENG-5)
- AIENG-29 BM25 → AIENG-30 RRF fusion → AIENG-31 cross-encoder (does it earn its latency?).
- AIENG-32 Diagnosis with citations + explicit no-evidence/no-answer path; unsupported claims blocked in tests.
- AIENG-33 Dense vs BM25 vs Hybrid vs Hybrid+rerank table (quality/latency/cost); **tag v0.1**; October retro.

### Phase 3 — Richer evidence (W05–W06, 2–13 Nov)

**W05 · Knowledge graph** (AIENG-6)
- AIENG-34 Schema: Service, Incident, Runbook, Engineer, Fix + 5 traversal questions.
- AIENG-35 Load into Neo4j/Aura with constraints → AIENG-36 Cypher (impact, history, ownership, fixes; one query plan).
- AIENG-37 Graph context behind retriever interface, toggleable.
- AIENG-38 Vector-only vs graph-assisted on graph-sensitive questions; **ADR: where graph belongs and where not**.

**W06 · Multimodal** (AIENG-7)
- AIENG-39 Benchmark (screenshots, diagrams, PDF pages) + hypotheses written first.
- AIENG-40 OCR/text baseline → AIENG-41 direct VLM / visual embeddings (ColPali optional).
- AIENG-42 ≥5 multimodal incidents end to end with cited evidence.
- AIENG-43 Routing decision per document type, evidence-backed.

### Phase 4 — Orchestration & agents (W07–W08, 16–27 Nov) → **v0.2**

**W07 · LangGraph** (AIENG-8)
- AIENG-44 State design: Intake→Extract→Retrieve→Diagnose→Confidence→Recommend/Review; mark deterministic vs model-driven steps.
- AIENG-45 Nodes/edges/conditional routing on existing functions.
- AIENG-46 Durable checkpoints; resume after restart without repeating steps.
- AIENG-47 Parallelize graph lookup ‖ doc retrieval; measure latency, prove correctness unchanged.
- AIENG-48 E2E task success + **ADR: agentic vs deterministic steps**.

**W08 · HITL, memory, MCP** (AIENG-9)
- AIENG-49 Interrupt + approve/reject/resume, audited.
- AIENG-50 Scoped long-term memory; cannot mutate audit history.
- AIENG-51 Step/retry budgets, duplicate-action detection, loop test terminates safely.
- AIENG-52 One MCP ticket/search tool; writes require approval.
- AIENG-53 Fault-inject (tool timeout, dup call, invalid state, model failure, rejection); **tag v0.2**.

### Phase 5 — Enterprise hardening (W09–W11, 30 Nov–18 Dec)

**W09 · Integration & Text-to-SQL** (AIENG-10)
- AIENG-54 Contracts for one ticket/messaging adapter + one SQL source (auth, timeouts, retries, failure behavior) before code.
- AIENG-55 Jira/Slack-style adapter, approval-gated writes.
- AIENG-56 Ops SQL schema + read-only DB role (writes fail at DB layer).
- AIENG-57 Text-to-SQL: allow-lists, SQL parse/validate, SELECT-only, row limits, timeouts.
- AIENG-58 Mock SOAP/XML legacy adapter; degraded/unavailable downstream → human review.

**W10 · IAM, RBAC, AI security** (AIENG-11)
- AIENG-59 Authn/authz model, JWT/OAuth, user/role/tenant context, threat assumptions.
- AIENG-60 Server-side RBAC (view/approve/admin).
- AIENG-61 Tenant/user filters applied in retrieval, graph and SQL **before** context assembly.
- AIENG-62 Prompt injection, system-prompt extraction, poisoned retrieval, PII (Presidio) attacks + mitigations.
- AIENG-63 Threat model + red-team cases in CI.

**W11 · Evals, gateway, observability** (AIENG-12)
- AIENG-64 Four eval layers: retrieval, generation, workflow/agent, system.
- AIENG-65 RAGAS/DeepEval; calibrate LLM judges vs manual labels.
- AIENG-66 LiteLLM gateway: routing, rate limits, backoff, fallback (simulated outage).
- AIENG-67 Langfuse spans for model/retrieval/tool/workflow; latency, tokens, cost.
- AIENG-68 Eval regression gates in CI; deliberate regression must block.

### Phase 6 — Reliability & release (W12–W13, 21 Dec–1 Jan) → **v1.0**

**W12 · Failure engineering** (AIENG-13)
- AIENG-69 Load test: P50/P95/P99, throughput, error rate, cost/request; top bottleneck.
- AIENG-70 Inject model / vector store / DB / integration failures; no silent wrong answers.
- AIENG-71 Concurrent duplicate submissions + orchestrator restart; no duplicate irreversible actions.
- AIENG-72 FULL → REDUCED → MANUAL degradation tiers + operator runbook (90 min).
- AIENG-73 Optional 60-min reliability retro (Christmas).

**W13 · v1.0 release** (AIENG-14)
- AIENG-74 Scope freeze + release checklist (must-fix / defer / known limitation).
- AIENG-75 Final architecture diagram + ADR index.
- AIENG-76 Eval, benchmark, threat reports (reproducible).
- AIENG-77 README, deploy guide verified from clean, runbook, cost model, limitations.
- AIENG-78 10–15 min walkthrough recording; **tag v1.0**.

## Dependency / critical path

```
W01 API+Postgres ─▶ W02 extraction ─▶ W03 dense ─▶ W04 hybrid+diagnosis (v0.1)
                                                     │
                     W05 graph ──────────────────────┤ (retriever interface)
                     W06 multimodal ─────────────────┤
                                                     ▼
                                        W07 LangGraph ─▶ W08 HITL/MCP (v0.2)
                                                     │
              W09 integrations ─▶ W10 RBAC/data permissions (touches retrieval, graph, SQL)
                                                     │
              W11 gateway + tracing + eval gates ─▶ W12 load/failure ─▶ W13 v1.0
```

Hard dependencies to protect:
1. **W02 provider abstraction** must be clean — W11 replaces it with LiteLLM. Keep a single interface so the swap is local.
2. **W03 retriever interface** is reused by graph (W05), multimodal (W06), and permission filtering (W10). Design it to accept filters/context from day one. ✅ Built in W03: `Retriever.retrieve(query, k, filters)` with a required tenant.
3. **W01 Postgres** carries incidents, idempotency, audit, LangGraph checkpoints and memory. Use migrations (Alembic) from W01.
4. **W01 CI** is the home for eval gates (W11) and red-team suite (W10). Keep it fast.

## Decisions to make early (recommendations)

| Decision | Recommendation | Why |
|---|---|---|
| Vector store | **pgvector** in existing Postgres ✅ adopted (W03) | One fewer service, same DB for metadata filters and tenant isolation (W10). Needs the `pgvector/pgvector:pg17` image locally, in CI and in compose. |
| DB access | SQLAlchemy 2.x async + asyncpg + Alembic ✅ adopted | Matches async FastAPI; migrations needed by W07/W08 |
| Errors | RFC 9457 `problem+json` with stable `code` ✅ adopted | One error shape for clients; no input echo |
| Hosting (W01–W12) | Lightsail `micro` + docker compose, deleted after use ✅ adopted | Fixed $7/mo, no hidden costs; registry/orchestrator deferred (see deployment note) |
| Repo layout | Keep `apps/api` (HTTP) thin, domain logic in `src/incidentops_ai` | Workflow, evals and CLI reuse the same code |
| Eval data | `evals/` dir with versioned datasets + results | W03, W11 and W13 all depend on reproducible runs |
| ADRs | `docs/adr/NNN-title.md`, numbered from ADR-001 (W02) | W13 needs a clean index |

## Current state (16 Oct 2026, W03 built; real-embedding runs pending)

- **W01 and W02 complete** (AIENG-2, 15–23). **W03 built ahead of schedule** (AIENG-24–28: code, tests, docs).
  Two W03 results are **not produced yet**: the chunking choice (AIENG-26) and the dense baseline
  with its miss analysis (AIENG-28). Both need real embeddings and the OpenAI key in `.env` has
  expired (401 `expired_secret_key`). Next: create a new project key, then run the commands in
  [roadmap-w03.md](roadmap-w03.md#to-finish-w03). After that: W04, AIENG-29.
- API: incidents (`POST`, `GET`, idempotency keys), `POST /incidents/{id}/extraction`,
  `POST /incidents/{id}/related-incidents`, `/health`; RFC 9457 errors. Unchanged in W03.
- LLM: provider layer (`LLMProvider`; OpenAI and fake implementations), strict structured output,
  bounded retry, tool calling with a validation gate and parallel execution, rule-based review flag.
  Model in use: `gpt-6-luna`. Prompts versioned in the package (`extract_incident/v1`, `v2`, `related_incidents/v1`).
- Retrieval (`src/incidentops_ai/retrieval/`): document parser (front matter, cleaning, form-feed
  pages, heading paths), three chunker families (fixed window, paragraph, section-aware), ingestion
  into PostgreSQL, `Embedder` interface (OpenAI with bounded retry, offline fake, on-disk cache),
  a `Retriever` interface with a **required tenant**, in-memory and pgvector dense retrievers,
  Recall@K / Precision@K / Hit@K / MRR against evidence spans, experiment and benchmark runners.
- Data: PostgreSQL via async SQLAlchemy + Alembic (`incidents`, `idempotency_keys`, `llm_runs`,
  `llm_run_keys`, `documents`, `chunks` with a 1536-dimension `vector` column).
- Tests: 387 pytest tests against real Postgres with pgvector, all offline (no API key, no model calls in CI).
- Evals: 10 extraction samples with two recorded baseline runs (`evals/extraction/`); 30 retrieval
  questions with 42 evidence spans (`evals/retrieval/questions.jsonl`), checked by a test against the corpus.
- Docker, CI (lint → test → build; green on `main` as of W01) and compose now use `pgvector/pgvector:pg17`
  (changed in W03, not yet run in CI), AWS account secured; deploy steps in `docs/deploy-lightsail.md`.
- Postman collection in `postman/` (11 requests).
- Decisions: [ADR-001](adr/001-llm-failure-handling.md) (retry vs fallback vs human review vs rejection).
- Notes: `docs/notes/` — async Python, backend stack, deployment, LLM provider design, LLM failure matrix,
  [retrieval mental model](notes/retrieval-mental-model.md), [W03 write-up](notes/week-03-retrieval-foundations.md).
- Week write-ups: [W02](notes/week-02-llm-application-engineering.md), [W03](notes/week-03-retrieval-foundations.md).

### What W03 taught us

- The metric can favour the wrong thing. With documents of about 2,000 characters, a 2,000-character
  chunk is nearly the whole document, so "did the retrieved chunk hold the answer" is easy and
  Recall/MRR reward it. An offline check showed it before any real run, so a "Chars read@5" column
  was added next to the quality numbers. The selection rule itself was not changed.
- Ground truth as a quote, not a chunk id, is what lets one eval set judge five chunkings.
- Tests caught two of my own wrong assumptions (a paragraph is 61 characters, not 70; a chunk that
  starts on page 2 and ends on page 3 does not make page 3 a "start page"), not product defects.
- The local Postgres (Homebrew 14) cannot run pgvector, so the test database is a container. Anything
  that needs the extension therefore needs that image, including CI and compose.
- An expired API key stops the measurement, not the build: everything except the two real-model
  results is finished and tested, and nothing offline is presented as a benchmark.

### What W02 taught us

- The SDK's defaults (2 silent retries, 600 s timeout) are wrong for a request path; we own both.
- Tests and fault injection found five real defects before any user did: a reply cut off at the token cap
  reported as "invalid", `"true"` silently coerced to a boolean, an HTML error page with status 200
  crashing the provider, broken JSON escaping as a raw exception, and tests able to read the real API key.
- The real-model check found two more: an incident reported as related to itself, and JSON `null`
  stored where SQL `NULL` was meant.
- The model is not deterministic: the same input gave SEV3 in one run and SEV2 in the next.
- Calls take 4–18 s, so LLM analysis is its own endpoint, never part of creating an incident.

## Risks & gaps found in the tickets

1. ~~Schedule slip in W01~~ — resolved: W01 finished on time.
2. **PREP partly open:** Postgres ✅ (pgvector container for tests), Docker ✅ (OrbStack), AWS + budget ✅. LLM API: OpenAI credit exists but **the key has expired** (see risk 16). Still open: Neo4j Aura (W05), Langfuse (W11).
3. ~~`__pycache__` committed~~ — resolved (`.gitignore` populated, files untracked).
4. ~~README empty~~ — resolved (setup/run/test steps); keep it current for AIENG-77.
5. **Heavy weeks:** W09 (3 integrations + Text-to-SQL) and W10 (RBAC + data permissions + red-team) are dense for 10h each. Pre-decide what drops first (candidate: legacy SOAP adapter AIENG-58 → known limitation).
6. **Holiday weeks:** W12 (Christmas) and W13 (New Year) are full release work. AIENG-73 is already optional; consider making AIENG-74 start in W12 Fri if energy is low.
7. **Corpus is synthetic/public.** Eval results risk overfitting a small set; W11 golden-set expansion (AIENG-64) is the mitigation — don't skip it.
8. **Data permissions retrofit (W10)** touches every retriever. ~~Carry a `tenant_id`/ACL field in chunk metadata from W03.~~ Done: every document and chunk has a tenant, and `RetrievalFilter` requires one. An ACL field is still to come (AIENG-61).
9. **AWS Paid plan, no hard spending cap.** Account moved to Paid when Identity Center/Organizations were enabled (now removed). Mitigation: budget + anomaly alerts, fixed-price Lightsail, delete resources after every session; never enable Organizations/Identity Center.
10. **Idempotency keys are global and never expire.** Scope per user once auth lands (AIENG-60) and add TTL cleanup (W12, AIENG-71).
11. **Single LLM provider, no fallback.** A provider outage disables analysis (ADR-001). Second provider and gateway: W11 (AIENG-66).
12. **LLM endpoints use one shared token, not per-user login.** Closed for a single-tenant deploy (review after W02): both LLM endpoints need `INCIDENTOPS_API_TOKEN` as a bearer token (without it they stay off, even with `OPENAI_API_KEY` set), are rate limited (`INCIDENTOPS_LLM_RATE_LIMIT_PER_MINUTE`, per process, so it assumes one uvicorn worker) and take an `Idempotency-Key` (claimed before the model is called; only successes are replayed). Real users, roles and per-user limits are still AIENG-60; with several workers the rate limit has to move to shared storage.
13. **Extraction ignored the stored fields.** Fixed with prompt `v2`: the recorded service, severity and environment are sent with the report. Not yet compared against the `v1` baseline with the real model (W11, AIENG-64).
14. **Eval set is tiny and unscored.** 10 samples, service name checked only. Expected values and repeated runs come in W11 (AIENG-64, 65).
15. **Planned improvement: prevent lost updates between sessions.** Before adding an incident update endpoint, add an Alembic migration for a non-null version counter and configure SQLAlchemy optimistic locking (`version_id_col`). Return the version to clients and require the expected version on updates, so stale client edits and concurrent session writes cannot silently overwrite another change. Return a structured HTTP `409` conflict when the version is stale; clients must reload and reconcile rather than blindly retry. Verify with a real-Postgres test using two independent sessions: both load the same version, the first commits, and the second is rejected without overwriting it. Include this scenario in W12 concurrency checks (AIENG-71).

16. **OpenAI key expired (found 16 Oct).** Blocks the real-embedding runs for AIENG-26 and AIENG-28 and the real-model parts of W04. Create a new project-scoped key, put it in `.env`, keep the spend limit and auto-recharge off.
17. **Retrieval metrics favour large chunks on a corpus of short documents.** The selection rule uses MRR and Recall@5 as fixed in advance; "Chars read@5" shows the cost. Re-check the choice when the corpus has long documents (W06 PDFs, W11 golden-set expansion).
18. **Corpus and eval set share one author and are small** (18 documents, 30 questions; one question is 3.3 points). Treat differences under about 5 points as noise; expand in W11.
19. **Postgres image changed** from `postgres:17-alpine` to `pgvector/pgvector:pg17` in compose and CI. A reused data volume could have collation differences (alpine/musl vs Debian/glibc); the Lightsail deploy is recreated each time, so it is acceptable, but CI and a clean deploy have not run on it yet.
20. **No vector index.** Exact scan is right for hundreds of chunks. Add HNSW near ~10,000 chunks (measure recall against the exact scan when you do).

## Weekly cadence

- **Mon:** read/design, write hypotheses or eval set before code.
- **Tue–Thu:** build, 2h each, tests with every change.
- **Fri:** measure, write note/ADR, tag release when scheduled.
- After each week: update ticket statuses in AIENG and the "Current state" section above.
