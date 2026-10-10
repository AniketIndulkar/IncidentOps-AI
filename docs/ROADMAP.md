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

- AIENG-19 Provider abstraction: `generate_structured` / `tool_call` + model config.
- AIENG-20 Nested extraction schema (title, service, symptoms, error codes, env, severity, entities, missing info); 10 samples parse.
- AIENG-21 Fault injection (bad JSON, schema mismatch, timeout, 429); bounded retry; explicit failure states, no coercion.
- AIENG-22 One read-only tool, arg validation, parallel tool calls, persisted prompt/model version per run.
- AIENG-23 Failure matrix + **ADR-001: retry vs fallback vs human review vs rejection**.

### Phase 2 — Retrieval (W03–W04, 19–30 Oct) → **v0.1**

**W03 · Retrieval foundations** (AIENG-4)
- AIENG-24 Corpus of runbooks/postmortems + **30-question eval set committed first**.
- AIENG-25 Ingestion with source/page provenance on every chunk.
- AIENG-26 ≥3 chunking configs vs eval set; pick from evidence.
- AIENG-27 Embedding model + pgvector/Qdrant + metadata-aware dense retriever.
- AIENG-28 Recall@K, Precision@K, MRR baseline report; inspect ≥5 misses.

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
2. **W03 retriever interface** is reused by graph (W05), multimodal (W06), and permission filtering (W10). Design it to accept filters/context from day one.
3. **W01 Postgres** carries incidents, idempotency, audit, LangGraph checkpoints and memory. Use migrations (Alembic) from W01.
4. **W01 CI** is the home for eval gates (W11) and red-team suite (W10). Keep it fast.

## Decisions to make early (recommendations)

| Decision | Recommendation | Why |
|---|---|---|
| Vector store | **pgvector** in existing Postgres | One fewer service, same DB for metadata filters and tenant isolation (W10) |
| DB access | SQLAlchemy 2.x async + asyncpg + Alembic ✅ adopted | Matches async FastAPI; migrations needed by W07/W08 |
| Errors | RFC 9457 `problem+json` with stable `code` ✅ adopted | One error shape for clients; no input echo |
| Hosting (W01–W12) | Lightsail `micro` + docker compose, deleted after use ✅ adopted | Fixed $7/mo, no hidden costs; registry/orchestrator deferred (see deployment note) |
| Repo layout | Keep `apps/api` (HTTP) thin, domain logic in `src/incidentops_ai` | Workflow, evals and CLI reuse the same code |
| Eval data | `evals/` dir with versioned datasets + results | W03, W11 and W13 all depend on reproducible runs |
| ADRs | `docs/adr/NNN-title.md`, numbered from ADR-001 (W02) | W13 needs a clean index |

## Current state (10 Oct 2026, end of W01)

- **W01 complete:** AIENG-2, 15, 16, 17, 18 done. Next: W02, starting with AIENG-19 (Mon 12 Oct).
- API: `POST /incidents` (optional `Idempotency-Key`), `GET /incidents/{id}`, `/health`; RFC 9457 errors.
- Data: PostgreSQL via async SQLAlchemy + Alembic (`incidents`, `idempotency_keys`).
- Tests: 26 pytest tests against real Postgres (local 14, CI 17).
- Docker: multi-stage image (non-root, healthcheck, migrations on start); `docker-compose.yml` with API + Postgres.
- CI: GitHub Actions lint → test → build; green on `main`.
- AWS: account secured (root MFA, IAM user `aniket-admin`, $25 budget + anomaly alerts, `aws login`).
  First deploy to Lightsail verified `/health` from the internet, then deleted (≈ $0.01). Steps: `docs/deploy-lightsail.md`.
- API testing: Postman collection in `postman/` (8 requests, 31 assertions), runnable with Newman against local or AWS.
- Notes: `docs/notes/python-async-vs-kotlin.md`, `docs/notes/deployment-docker-ci-aws.md`.

## Risks & gaps found in the tickets

1. ~~Schedule slip in W01~~ — resolved: W01 finished on time.
2. **PREP partly open:** Postgres ✅, Docker ✅ (OrbStack), AWS + budget ✅. Still open: LLM API credit (needed W02), Neo4j Aura (W05), Langfuse (W11).
3. ~~`__pycache__` committed~~ — resolved (`.gitignore` populated, files untracked).
4. ~~README empty~~ — resolved (setup/run/test steps); keep it current for AIENG-77.
5. **Heavy weeks:** W09 (3 integrations + Text-to-SQL) and W10 (RBAC + data permissions + red-team) are dense for 10h each. Pre-decide what drops first (candidate: legacy SOAP adapter AIENG-58 → known limitation).
6. **Holiday weeks:** W12 (Christmas) and W13 (New Year) are full release work. AIENG-73 is already optional; consider making AIENG-74 start in W12 Fri if energy is low.
7. **Corpus is synthetic/public.** Eval results risk overfitting a small set; W11 golden-set expansion (AIENG-64) is the mitigation — don't skip it.
8. **Data permissions retrofit (W10)** touches every retriever. Carry a `tenant_id`/ACL field in chunk metadata from W03 to make W10 cheap.
9. **AWS Paid plan, no hard spending cap.** Account moved to Paid when Identity Center/Organizations were enabled (now removed). Mitigation: budget + anomaly alerts, fixed-price Lightsail, delete resources after every session; never enable Organizations/Identity Center.
10. **Idempotency keys are global and never expire.** Scope per user once auth lands (AIENG-60) and add TTL cleanup (W12, AIENG-71).
11. **Planned improvement: prevent lost updates between sessions.** Before adding an incident update endpoint, add an Alembic migration for a non-null version counter and configure SQLAlchemy optimistic locking (`version_id_col`). Return the version to clients and require the expected version on updates, so stale client edits and concurrent session writes cannot silently overwrite another change. Return a structured HTTP `409` conflict when the version is stale; clients must reload and reconcile rather than blindly retry. Verify with a real-Postgres test using two independent sessions: both load the same version, the first commits, and the second is rejected without overwriting it. Include this scenario in W12 concurrency checks (AIENG-71).

## Weekly cadence

- **Mon:** read/design, write hypotheses or eval set before code.
- **Tue–Thu:** build, 2h each, tests with every change.
- **Fri:** measure, write note/ADR, tag release when scheduled.
- After each week: update ticket statuses in AIENG and the "Current state" section above.
