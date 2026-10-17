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
| **v0.1** | W04 | 30 Oct | Evidence-backed hybrid retrieval + grounded, cited diagnosis. Built 16 Oct; tag not created yet |
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

Detailed plan, hypotheses and decisions: [roadmap-w03.md](roadmap-w03.md). Done 16 Oct, ahead of schedule.
Findings: [dense-baseline-findings](../evals/retrieval/runs/2026-10-16-dense-baseline-findings.md).

- ✅ AIENG-24 Corpus of 18 runbooks/postmortems + **30-question eval set committed first** (ground truth = exact quotes, valid under any chunking).
- ✅ AIENG-25 Ingestion with source/page/section/char-span provenance and a tenant on every chunk; repeatable, one transaction.
- ✅ AIENG-26 Five chunking configs on real embeddings; the rule fixed in advance selects **`fixed-2000-200`** (MRR 0.961, next 0.801). Caveat: at ~1,900 characters per document that chunk is nearly a whole document and reads 3.8× more text than `section-1500`; the runner-up for long documents is `section-1500` with the context header.
- ✅ AIENG-27 pgvector column, `PgVectorRetriever` (tenant/service/type filters in SQL), held to the same ranking as the in-memory reference. Embedding model `text-embedding-3-small` adopted: `-large` gained 0 points of Recall@5 on the chosen config.
- ✅ AIENG-28 Baseline on pgvector: Recall@5 0.983, MRR@10 0.961, p95 285 ms, one question below full Recall@5. Ten failures inspected and sorted into modes; hypotheses H1–H6 judged (two refuted, two partly, two confirmed).

**W04 · Hybrid, RRF, reranking** (AIENG-5) — *exit: Dense vs BM25 vs Hybrid vs Hybrid+reranker benchmark with grounded diagnosis output*

Detailed plan, hypotheses and the stack-selection rule: [roadmap-w04.md](roadmap-w04.md). Done 16 Oct, ahead of schedule.
Findings: [hybrid-findings](../evals/retrieval/runs/2026-10-16-hybrid-findings.md); write-up: [notes/week-04-hybrid-retrieval-and-diagnosis.md](notes/week-04-hybrid-retrieval-and-diagnosis.md).

- ✅ AIENG-29 BM25 (own implementation, identifiers indexed whole and in parts, no stemming, k1 1.2 / b 0.75 fixed in advance). Ties dense on `fixed-2000-200` (MRR 0.957 vs 0.961), worse on small chunks (0.815 vs 0.889) except identifiers.
- ✅ AIENG-30 RRF fusion (k 60, top 50 each, equal weights). Hybrid: MRR 0.967 on `fixed-2000-200`, 0.918 on `section-1500`+header; Recall@5 1.000 on the first. **Adopted as the default stack** by the rule fixed in advance; the win is about one question.
- ✅ AIENG-31 Cross-encoder (`Xenova/ms-marco-MiniLM-L-6-v2`, ONNX, optional extra). **Does not earn its latency here**: MRR −0.006 and −0.029 on the two decision configs, and too heavy locally as pre-registered (p95 423 to 715 ms, 1.3 GB added at the default batch). Helps on small chunks without a header, where the free context header does the same. Built, off by default.
- ✅ AIENG-32 `POST /incidents/{id}/diagnosis`: causes with server-checked citations, explicit `no_answer` (no evidence / insufficient evidence / no supported cause); fabricated citations blocked in tests. Real-model spot check: 3 of 3 on-corpus answered, 2 of 2 off-corpus refused ([record](../evals/diagnosis/2026-10-16-real-model-check.md)).
- ✅ AIENG-33 Stack comparison table, [October retrospective](notes/retro-october-2026.md). **`v0.1` tag not created yet** (waiting for your go-ahead).

### Phase 3 — Richer evidence (W05–W06, 2–13 Nov)

**W05 · Knowledge graph** (AIENG-6) — *exit: Neo4j graph, Cypher queries and comparative retrieval evaluation*

Plan and hypotheses: [roadmap-w05.md](roadmap-w05.md). Built 17 Oct, stacked on W04. Findings:
[graph-findings](../evals/graph/runs/2026-10-17-graph-findings.md); decision: [ADR-002](adr/002-knowledge-graph.md).

- ✅ AIENG-34 Schema (Service, Team, Engineer, Incident, Fix, Runbook), a synthetic catalogue validated against the corpus, 5 traversal questions, 12 graph-sensitive questions written first.
- ✅ AIENG-35 Local Neo4j (`neo4j:5-community`), uid uniqueness constraint per label, a tenant's graph replaced in one transaction; `python -m incidentops_ai.graph.load`.
- ✅ AIENG-36 Cypher for impact, history, ownership, open upstream fixes and runbook coverage; identical answers from an in-memory reference; `PROFILE` of T2 in the ADR (~150 db hits).
- ✅ AIENG-37 `QueryHints` (a service suggests, never filters), `AnchoredRetriever` (own documents or graph neighbourhood, fused by RRF), switchable with `INCIDENTOPS_GRAPH_RETRIEVAL`; diagnosis returns `service_context`, degrades when Neo4j is down.
- ✅ AIENG-38 **Graph does not help retrieval here**: hybrid already reaches Recall@5 1.000 on the graph set; the soft anchor costs 0.196 MRR, the graph +0.000, a hard filter loses every dependency question. ADR-002: the graph answers structured questions and is shown as context.

**W06 · Multimodal** (AIENG-7) — *exit: benchmarked multimodal ingestion over screenshots, diagrams and complex PDFs*

Plan and hypotheses: [roadmap-w06.md](roadmap-w06.md). Built 17 Oct, stacked on W05. Findings:
[multimodal-findings](../evals/multimodal/runs/2026-10-17-multimodal-findings.md); decision: [ADR-003](adr/003-multimodal-routing.md).

- ✅ AIENG-39 14 generated assets (screenshots, charts, diagrams, text and scanned PDFs), 28 questions, hypotheses H18–H23 and two rules, committed before extraction code.
- ✅ AIENG-40 PDF text layer (pypdfium2) and OCR (RapidOCR, optional extra) with a pixel box per line.
- ✅ AIENG-41 Image inputs in the provider; vision transcript (citable) and direct answers (ceiling). OCR 1/6 on charts and 2/6 on diagrams; the transcript 6/6; ~$0.24 for the whole benchmark at an assumed price.
- ✅ AIENG-42 `POST/GET /incidents/{id}/attachments`; attachment evidence `A1..An` in the diagnosis with box-level provenance for OCR; six multimodal incidents end to end with real models.
- ✅ AIENG-43 Routing by file type: text layer → text, scan → OCR, image → vision transcript (OCR fallback). The coverage router written in advance was refuted (9/14).

### Phase 4 — Orchestration & agents (W07–W08, 16–27 Nov) → **v0.2**

**W07 · LangGraph** (AIENG-8)
- AIENG-44 State design: Intake→Extract→Retrieve→Diagnose→Confidence→Recommend/Review; mark deterministic vs model-driven steps.
- AIENG-45 Nodes/edges/conditional routing on existing functions.
- AIENG-46 Durable checkpoints; resume after restart without repeating steps.
- AIENG-47 Parallelize graph lookup ‖ doc retrieval; measure latency, prove correctness unchanged.
- AIENG-48 E2E task success + **ADR: agentic vs deterministic steps**.

✅ Built 17 Oct, stacked on W06: [roadmap-w07](roadmap-w07.md). Task success 11/12 with real models; the two
model calls share a step (−34% median); checkpoints in PostgreSQL, resume without repeating model calls;
LangGraph +6 ms (+38 ms with checkpoints). Findings:
[workflow-findings](../evals/workflow/runs/2026-10-17-workflow-findings.md); decision: [ADR-004](adr/004-agentic-vs-deterministic-steps.md).

**W08 · HITL, memory, MCP** (AIENG-9)
- AIENG-49 Interrupt + approve/reject/resume, audited.
- AIENG-50 Scoped long-term memory; cannot mutate audit history.
- AIENG-51 Step/retry budgets, duplicate-action detection, loop test terminates safely.
- AIENG-52 One MCP ticket/search tool; writes require approval.
- AIENG-53 Fault-inject (tool timeout, dup call, invalid state, model failure, rejection); **tag v0.2**.

✅ Built 17 Oct, stacked on W07: [roadmap-w08](roadmap-w08.md). Every run stops at an approval gate; audit and
memory are append-only in the database; notes are cited as `M1..M3` (3 of 3 with real models, never out of
scope); the ticket write goes over MCP with a signed approval; tool loops and resumes have budgets; five
faults end safely. Findings: [w08-findings](../evals/workflow/runs/2026-10-17-w08-findings.md); decision:
[ADR-005](adr/005-approval-gate-and-tool-trust-boundary.md). `v0.2` not tagged (your call).

### Phase 5 — Enterprise hardening (W09–W11, 30 Nov–18 Dec)

**W09 · Integration & Text-to-SQL** (AIENG-10)
- AIENG-54 Contracts for one ticket/messaging adapter + one SQL source (auth, timeouts, retries, failure behavior) before code.
- AIENG-55 Jira/Slack-style adapter, approval-gated writes.
- AIENG-56 Ops SQL schema + read-only DB role (writes fail at DB layer).
- AIENG-57 Text-to-SQL: allow-lists, SQL parse/validate, SELECT-only, row limits, timeouts.
- AIENG-58 Mock SOAP/XML legacy adapter; degraded/unavailable downstream → human review.

✅ Built 17 Oct, stacked on W08: [roadmap-w09](roadmap-w09.md). Jira-style adapter with safe retries and approved,
labelled writes; ops schema behind a reader role (12/12 writes fail); text-to-SQL 14/15 with the real model, 20/20
attacks refused; SOAP CMDB with `integration_degraded` routing. Findings:
[w09-findings](../evals/sql/runs/2026-10-17-w09-findings.md); decision: [ADR-006](adr/006-text-to-sql-and-integration-failures.md).

**W10 · IAM, RBAC, AI security** (AIENG-11)
- AIENG-59 Authn/authz model, JWT/OAuth, user/role/tenant context, threat assumptions.
- AIENG-60 Server-side RBAC (view/approve/admin).
- AIENG-61 Tenant/user filters applied in retrieval, graph and SQL **before** context assembly.
- AIENG-62 Prompt injection, system-prompt extraction, poisoned retrieval, PII (Presidio) attacks + mitigations.
- AIENG-63 Threat model + red-team cases in CI.

✅ Built 17 Oct, stacked on W09: [roadmap-w10](roadmap-w10.md). JWT principals and four roles enforced per route (a
matrix over all routes); tenant enforced in lookups, idempotency keys, retrieval, tools, notes and ops RLS; PII
redaction and a guarded provider; injection flags; 170 red-team tests in their own CI step. Findings:
[w10-findings](../evals/security/runs/2026-10-17-w10-findings.md); [threat model](threat-model.md); decision:
[ADR-007](adr/007-identity-tenancy-and-model-controls.md).

**W11 · Evals, gateway, observability** (AIENG-12)
- AIENG-64 Four eval layers: retrieval, generation, workflow/agent, system.
- AIENG-65 RAGAS/DeepEval; calibrate LLM judges vs manual labels.
- AIENG-66 LiteLLM gateway: routing, rate limits, backoff, fallback (simulated outage).
- AIENG-67 Langfuse spans for model/retrieval/tool/workflow; latency, tokens, cost.
- AIENG-68 Eval regression gates in CI; deliberate regression must block.

✅ Built 17 Oct, stacked on W10: [roadmap-w11](roadmap-w11.md). Four layers with datasets and thresholds; generation
15/16 with citation precision 1.0; the judge's agreement with labels 31% then 77% (reported, not gated); a gateway
with fallback and a breaker; traces with JSONL and Langfuse exporters; a CI gate that fails deliberate regressions
and unevaluated prompt changes. Findings: [w11-findings](../evals/generation/runs/2026-10-17-w11-findings.md);
decision: [ADR-008](adr/008-evaluation-gateway-and-tracing.md).

### Phase 6 — Reliability & release (W12–W13, 21 Dec–1 Jan) → **v1.0**

**W12 · Failure engineering** (AIENG-13)
- AIENG-69 Load test: P50/P95/P99, throughput, error rate, cost/request; top bottleneck.
- AIENG-70 Inject model / vector store / DB / integration failures; no silent wrong answers.
- AIENG-71 Concurrent duplicate submissions + orchestrator restart; no duplicate irreversible actions.
- AIENG-72 FULL → REDUCED → MANUAL degradation tiers + operator runbook (90 min).
- AIENG-73 Optional 60-min reliability retro (Christmas).

✅ Built 17 Oct, stacked on W11: [roadmap-w12](roadmap-w12.md). No errors up to 100 concurrent responders (simulated
model from real recordings); the model is the bottleneck; retrieval timeout, stale-run recovery and an advisory lock
on ticket writes fixed three faults found by forcing failures; `GET /status` and FULL/REDUCED/MANUAL tiers; a
runbook. Findings: [w12-findings](../evals/reliability/runs/2026-10-17-w12-findings.md); decision:
[ADR-009](adr/009-reliability-and-degradation.md); [retro](notes/reliability-retro.md).

**W13 · v1.0 release** (AIENG-14)
- AIENG-74 Scope freeze + release checklist (must-fix / defer / known limitation).
- AIENG-75 Final architecture diagram + ADR index.
- AIENG-76 Eval, benchmark, threat reports (reproducible).
- AIENG-77 README, deploy guide verified from clean, runbook, cost model, limitations.
- AIENG-78 10–15 min walkthrough recording; **tag v1.0**.

✅ Built 17 Oct, stacked on W12: [roadmap-w13](roadmap-w13.md). Scope frozen and every open item classified
([checklist](release/v1.0-checklist.md)); four must-fixes done (a periodic stale-run sweep; compose passing every
setting; reads and decisions no longer spending the model budget; the README); the deployment guide followed from an
empty volume to a real triage and an approved ticket; [architecture](architecture.md), [ADR index](adr/README.md),
[reports](reports/README.md), [cost model](cost-model.md), [known limitations](known-limitations.md), the
[walkthrough script](release/walkthrough-script.md) and [release notes](release/v1.0-release-notes.md).
Findings: [w13-findings](../evals/release/2026-10-17-w13-findings.md). **Not done (yours):** tags, the recording,
merging the stack, Jira.

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
| Embeddings and chunking | `text-embedding-3-small` (1536 dims) and `fixed-2000-200` ✅ adopted (W03) | `-large` was no better on the chosen config; chunking chosen by a rule fixed before the run. Revisit chunking when documents get long. |
| Vector store | **pgvector** in existing Postgres ✅ adopted (W03) | One fewer service, same DB for metadata filters and tenant isolation (W10). Needs the `pgvector/pgvector:pg17` image locally, in CI and in compose. |
| DB access | SQLAlchemy 2.x async + asyncpg + Alembic ✅ adopted | Matches async FastAPI; migrations needed by W07/W08 |
| Errors | RFC 9457 `problem+json` with stable `code` ✅ adopted | One error shape for clients; no input echo |
| Hosting (W01–W12) | Lightsail `micro` + docker compose, deleted after use ✅ adopted | Fixed $7/mo, no hidden costs; registry/orchestrator deferred (see deployment note) |
| Repo layout | Keep `apps/api` (HTTP) thin, domain logic in `src/incidentops_ai` | Workflow, evals and CLI reuse the same code |
| Eval data | `evals/` dir with versioned datasets + results | W03, W11 and W13 all depend on reproducible runs |
| ADRs | `docs/adr/NNN-title.md`, numbered from ADR-001 (W02) | W13 needs a clean index |
| Graph database | **Local Neo4j** (`neo4j:5-community`) ✅ adopted (W05) | No account, CI runs it as a service; ADR-002 limits it to structured context |
| Retrieval stack | **Hybrid**: dense (pgvector) + BM25, RRF k = 60 ✅ adopted (W04); reranker off by default | Chosen by a rule fixed before the run: hybrid +0.006 / +0.029 MRR on the two decision configs, the reranker −0.006 / −0.029. `INCIDENTOPS_RETRIEVAL_STACK` switches it. |

## Current state (17 Oct 2026, W13 complete: v1.0 ready to tag; no tags yet)

- **W01 to W13 complete** (AIENG-2, 15–78). W03 and W04 were built on 16 Oct, W05 to W13 on 17 Oct,
  as stacked pull requests (#3 W03 … #12 W12, W13 on top). Waiting on you: CI on the stack, merging it
  in order, the `v0.1`, `v0.2` and `v1.0` tags, the walkthrough recording, and the Jira updates
  ([checklist](release/v1.0-checklist.md)).
- Release (W13): the [README](../README.md) for a newcomer, [architecture](architecture.md) with failure
  behaviour per boundary, the [ADR index](adr/README.md), [reports](reports/README.md), [cost model](cost-model.md),
  [known limitations](known-limitations.md), a [deployment guide](deploy.md) verified from clean,
  `docker-compose.yml` passing every setting (test-guarded), a periodic stale-run sweep, and the model rate limit
  only on routes that call a model.
- Reliability (W12): `GET /status` (FULL/REDUCED/MANUAL), [runbook](runbook.md), retrieval timeout,
  stale-run recovery at startup, advisory lock around ticket writes, configurable pool sizes, the
  simulated provider and `bm25` stack for load tests, `reliability.load` and `reliability.burst`.
- Quality and operations (W11): `evals` (four layers, the generation layer and judge, the CI gate,
  `evals/dashboard.md`), `llm.gateway` (routes, fallback, breaker), `observability` (spans, JSONL and
  Langfuse exporters, the trace report). **A prompt change now needs a real-model generation run and
  a refreshed `evals/baseline.json`, or CI fails.**
- Security (W10): `auth` (JWT verification, roles, `permit` per route; the static token is a machine
  credential), tenant on incidents and idempotency keys, ops row-level security per reader login,
  `security` (PII redaction, `GuardedProvider`, injection flags → `suspicious_evidence`), the
  `redteam` marker and CI step, [threat model](threat-model.md).
- Integrations (W09): `integrations.jira` (Jira-style REST, safe retries, labelled approved writes;
  used by `act` when `INCIDENTOPS_JIRA_URL` is set), `integrations.cmdb` (SOAP CMDB, `cmdb` workflow
  step, `integration_degraded`), `ops` (schema `ops`, reader role, allow-list, validator, text-to-SQL,
  `POST /ops/query`).
- Human in the loop (W08): every triage run stops at `awaiting_approval` with a proposed ticket;
  `POST .../decision` (once, row-locked); `workflow_events` and `memories` append-only by trigger;
  reviewer notes per tenant and service, recalled as evidence `M1..M3` (prompt `diagnose_incident/v3`);
  the ticket MCP server (`incidentops_ai.tickets`, stdio) whose write needs a signed approval; tool-loop
  duplicate detection and budgets; resume budget and step limit; `workflow.faults` scenarios.
- Workflow (W07, `src/incidentops_ai/workflow/`): a LangGraph state machine over the existing steps,
  routing by reason codes in code, checkpoints in PostgreSQL (`AsyncPostgresSaver`, tables created by
  the saver), `workflow_runs` records, and the triage API: `POST /incidents/{id}/triage`, `GET` a run
  with its state and step log, `POST .../resume` for a failed run. Benchmark in `workflow.benchmark`.
- Multimodal evidence (W06, `src/incidentops_ai/multimodal/`): attachments (PNG, JPEG, PDF) checked,
  routed and stored per incident; PDF text layer, OCR with line boxes, vision transcripts; evidence
  `A1..An` in the diagnosis (prompt `diagnose_incident/v2`); a generated benchmark and its runner.
- Knowledge graph (W05, `src/incidentops_ai/graph/`): catalogue loader and validation, Neo4j store with
  uid constraints, Cypher for five traversal questions plus related documents and service context, an
  in-memory reference, graph-anchored retrieval (off by default), and the `graph.benchmark` harness.
- API: incidents (`POST`, `GET`, idempotency keys), `POST /incidents/{id}/extraction`,
  `POST /incidents/{id}/related-incidents`, **`POST /incidents/{id}/diagnosis`** (W04), `/health`; RFC 9457
  errors (new: `retrieval_failed`, 502). The diagnosis route uses the same bearer token, rate limit and
  `Idempotency-Key` as the other two LLM routes.
- LLM: provider layer (`LLMProvider`; OpenAI and fake implementations), strict structured output,
  bounded retry, tool calling with a validation gate and parallel execution, rule-based review flag.
  Model in use: `gpt-6-luna`. Prompts versioned in the package (`extract_incident/v1`, `v2`, `related_incidents/v1`).
- Retrieval (`src/incidentops_ai/retrieval/`): document parser (front matter, cleaning, form-feed
  pages, heading paths), three chunker families (fixed window, paragraph, section-aware), ingestion
  into PostgreSQL, `Embedder` interface (OpenAI with bounded retry, offline fake, on-disk cache),
  a `Retriever` interface with a **required tenant**, in-memory and pgvector dense retrievers,
  Recall@K / Precision@K / Hit@K / MRR against evidence spans, experiment and benchmark runners.
  **W04:** `Bm25Retriever`, reciprocal-rank-fusion `HybridRetriever`, `Reranker` interface with a
  local ONNX cross-encoder (optional extra `rerank`), `build_retriever` (dense / hybrid / hybrid-rerank),
  a BM25 index that follows re-ingestion, and `stack_benchmark` (every stage on the same questions).
- Diagnosis (`src/incidentops_ai/diagnosis/`, prompt `diagnose_incident/v1`): numbered evidence, causes with
  `(evidence id, verbatim quote)` citations, a server-side check that the quote is in the cited chunk
  (words exact, spacing free, 12+ characters), provenance rebuilt from stored chunk data, explicit `no_answer`.
- Data: PostgreSQL via async SQLAlchemy + Alembic (`incidents`, `idempotency_keys`, `llm_runs`,
  `llm_run_keys`, `documents`, `chunks` with a 1536-dimension `vector` column).
- Tests: 491 pytest tests (1 skipped: the opt-in real-reranker test) against real Postgres with pgvector, all offline (no API key, no model calls, no model download in CI).
- Evals: 10 extraction samples with two recorded baseline runs (`evals/extraction/`); 30 retrieval
  questions with 42 evidence spans (`evals/retrieval/questions.jsonl`), checked by a test against the corpus.
  Dense baseline (`text-embedding-3-small`, `fixed-2000-200`, pgvector): Recall@5 0.983, Precision@5 0.260,
  MRR@10 0.961, mean query latency 210 ms. Reports and per-question data in `evals/retrieval/runs/`.
  **W04 stack comparison** (same questions; `fixed-2000-200` / `section-1500`+header, MRR@10): dense
  0.961 / 0.889, BM25 0.957 / 0.815, **hybrid 0.967 / 0.918**, hybrid+rerank 0.961 / 0.889. Diagnosis spot
  check with the real model in `evals/diagnosis/`.
- Docker (image builds with the new dependencies), CI (lint → test → build; green on `main` as of W01) and
  compose now use `pgvector/pgvector:pg17` (changed in W03, CI not yet run on it), AWS account secured; deploy steps in `docs/deploy-lightsail.md`.
- Postman collection in `postman/` (11 requests).
- Decisions: [ADR-001](adr/001-llm-failure-handling.md) (retry vs fallback vs human review vs rejection).
- Notes: `docs/notes/` — async Python, backend stack, deployment, LLM provider design, LLM failure matrix,
  [retrieval mental model](notes/retrieval-mental-model.md), [W03 write-up](notes/week-03-retrieval-foundations.md),
  [dense baseline findings](../evals/retrieval/runs/2026-10-16-dense-baseline-findings.md),
  [hybrid findings](../evals/retrieval/runs/2026-10-16-hybrid-findings.md),
  [W04 write-up](notes/week-04-hybrid-retrieval-and-diagnosis.md), [October retrospective](notes/retro-october-2026.md).
- Week write-ups: [W02](notes/week-02-llm-application-engineering.md), [W03](notes/week-03-retrieval-foundations.md),
  [W04](notes/week-04-hybrid-retrieval-and-diagnosis.md).

### What W03 taught us

- The best config by the pre-registered rule is the one that is closest to "retrieve the whole
  document" (`fixed-2000-200`, MRR 0.961), on a corpus of ~1,900-character documents. It reads 3.8×
  more text than `section-1500`. We kept the rule and published the cost next to the score.
- Two of the six hypotheses were refuted outright (large chunks do not hurt; identifier questions are
  not the weak spot), two held in part, two held. Writing them down first made that visible.
- A context header (`title > section`) lifts small chunks by ~0.08 MRR and does nothing for large ones.
- The real failures are about *sources and passages*, not scores: a second document that shares no
  words with the question, one postmortem crowding out its runbook, a definition ranked below its
  usages, a small fact diluted inside a large chunk. W04's BM25, fusion and reranker each map to one.
- The baseline is near its ceiling on the chosen config (Recall@5 0.983), leaving W04 little room to
  show improvement. Run the W04 comparison on `section-1500` too, and add harder questions.
- Ground truth as a quote, not a chunk id, is what let one eval set judge five chunkings and two
  embedding models.
- The local Postgres (Homebrew 14) cannot run pgvector, so the test database is a container, and so
  are CI and compose now.
- An expired API key stopped the measurement, not the build; the offline runs were never presented
  as results, and the real runs cost a few cents.

### What W04 taught us

- Dense, BM25 and hybrid are within one or two questions of each other on this corpus. "Hybrid is never
  meaningfully worse and costs nothing" is the honest claim; "hybrid wins" is not.
- BM25 beats dense on identifiers when chunks are small (q06) and is blind to "updates" vs "update"
  (q10): no stemming was a pre-registered choice with a visible cost.
- A reranker reads the question against the heading and the passage. It fixes "right document, wrong
  section" (q28, q10, q29) and breaks cases where topical overlap beats intent (q15) or the answer is one
  line in a section about something else (q13). On small chunks a free context header buys the same MRR.
- As pre-registered the reranker is too heavy locally (423 to 715 ms p95, 1.3 GB added at the default
  batch). A batch of 8 halves the memory and depth 10 brings small chunks under 300 ms; none gets under
  500 MB. A 1 GB Lightsail box cannot hold it, so no AWS move was needed because the rule did not adopt it.
- A citation proves the quote exists, not that the claim follows. Claim-level checking is W11.
- The remaining failures (q24, q25, q29) are about sources, not passages: the graph (W05) is the planned fix.

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
2. **PREP partly open:** Postgres ✅ (pgvector container for tests), Docker ✅ (OrbStack), AWS + budget ✅. LLM API ✅ (OpenAI; the key expired on 16 Oct and was replaced, see risk 16). Still open: Neo4j Aura (W05), Langfuse (W11).
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

16. ~~OpenAI key expired (found 16 Oct)~~ — resolved the same day with a new project-scoped key; keep the spend limit on and auto-recharge off.
17. **Retrieval metrics favour large chunks on a corpus of short documents — confirmed in W03; W04 reports both.** The rule picked `fixed-2000-200` (≈ whole documents; 7,433 characters read for the top five vs 1,961 for `section-1500`). Re-check the choice when the corpus has long documents (W06 PDFs, W11 expansion); runner-up `section-1500` + context header. W04 ran every stage on both configs; hybrid is the default on either.
18. **Corpus and eval set share one author and are small** (18 documents, 30 questions; one question is 3.3 points). Treat differences under about 5 points as noise; expand in W11.
19. **Postgres image changed** from `postgres:17-alpine` to `pgvector/pgvector:pg17` in compose and CI. A reused data volume could have collation differences (alpine/musl vs Debian/glibc); the Lightsail deploy is recreated each time, so it is acceptable, but CI and a clean deploy have not run on it yet.
20. **No vector index.** Exact scan is right for hundreds of chunks. Add HNSW near ~10,000 chunks (measure recall against the exact scan when you do).
21. **The dense baseline is near its ceiling** (Recall@5 0.983, MRR 0.961, one miss), so W04's hybrid and reranking gains may be invisible on the chosen config. Confirmed: the only visible change there is one question (q25). W04 ran `section-1500`+header as the second decision config; add harder multi-source questions (q24, q25, q29 are the template) in W11.
22. **A citation proves the words exist, not that the claim follows.** The server checks that the quote is in the cited chunk; it cannot tell that a claim goes beyond it (the Kafka answer in the spot check joined a runbook fact to an inference from an older incident, hedged only by the model's own wording). Claim-level checks: W11 (AIENG-64, 65).
23. **No relevance gate for "nothing relevant was retrieved".** Off-corpus incidents still retrieve five chunks and reach the model, which refused 2 of 2 in the spot check. A score threshold needs off-corpus incidents to calibrate against: W11 (AIENG-64).
24. **BM25 without stemming** misses "updates" vs "update" (q10). Left out on purpose (parameters fixed before the run); try it on the larger golden set (W11).
25. **The reranker is too heavy to run in the 1 GB Lightsail deploy** (0.65 to 1.3 GB). It is off by default and an optional extra, so the default image is unaffected. If a later corpus needs it: depth 10 and a small batch, then a hosted reranker, then a bigger instance.
26. **The graph is measured on my own catalogue and questions.** Graph value could be under- or over-stated for incidents that describe only the reporting service's view. A new question set would be needed to test that (W11).
27. **Neo4j adds a second datastore** to development and CI (and to the deploy, if service context is wanted there). The API runs without it; with it down, diagnosis degrades to `graph_status: "unavailable"`.
28. **Vision transcripts can misread and still be cited faithfully** (`mbznw` for `mbzrw` in the W06 run). A citation of an attachment proves what the transcript says. Candidate mitigation: OCR the image too and flag identifiers the two disagree on.
29. **OCR can drop whole lines silently**, with high confidence on the lines it keeps. Do not treat OCR text as complete.
30. **The routing rule was read off 14 synthetic assets.** Real, noisy screenshots and scans are needed to trust it (W11 golden set).
31. **The workflow route is deterministic about noisy inputs.** Three of 12 cases changed route between two real runs because the extraction listed 3 open questions once and 4 once (`many_open_questions` fires at 4). Decide in W08/W09 whether a count a model chooses belongs in the route.
32. ~~**A model call cancelled by a failing sibling step is not recorded**~~ Fixed in W08: the record is a shielded task the runner waits for.
33. **Checkpoints hold incident data** in the incident database, with no retention yet (W12/W13).
34. ~~**Reviewer identity is self-declared**~~ Fixed in W10: the reviewer is the authenticated principal.
35. **A recommendation can rest on a single reviewer's note** (2 of 3 in the W08 memory check). The gate still requires a person; nothing flags it yet (W09/W10).
36. **The approval secret mints approvals**: whoever holds `INCIDENTOPS_APPROVAL_SECRET` can sign a ticket write.
37. **Runs can wait at the gate indefinitely**: no expiry or reminder yet.
38. **Catalog reads depend on the SQL validator alone.** The reader role can read `information_schema`, `pg_user` (role names) and call `set_config`; PostgreSQL grants those to every role. Fix: the ops data in its own database or cluster.
39. **The adapters are tested against fakes.** Real Jira and a real CMDB paginate, version and fail in more ways; run the contract tests against sandboxes before production.
40. ~~**Ops data is not filtered per tenant**~~ Fixed in W10: row-level security per reader login.
41. **The static API token is an admin credential with no expiry.** Development and automation only.
42. **PII detection is pattern-based**: names and addresses are not redacted.
43. **The injection detector is a phrase list**; the real-model run found a phrasing it missed. It routes to review, it does not block.
44. **No real identity provider has been connected**; tokens were minted for the tests.
45. **The LLM judge is not calibrated enough to gate** (77% agreement with 13 labels written by the assistant). The user's labels are the next step.
46. **The Langfuse adapter has only met a fake** ingestion server.
47. **Prompt changes cost a real-model run** before CI passes (about $0.15 for generation).
48. ~~**Stale runs are recovered only at startup**~~ Fixed in W13: a periodic sweep (`INCIDENTOPS_STALE_RUN_SWEEP_S`).
49. **Real provider limits are unmeasured**: the load test simulated the model; a real account's tokens-per-minute limit is likely the first wall at ~9 triages/s.
50. **Ticket deduplication leans on the ticket system's search**: the advisory lock serialises our writers, but a lagging search index could still miss a just-created issue.
51. ~~**Compose passed a hand-picked list of settings**~~ Fixed in W13 (found by the clean deployment): every setting reaches the container, test-guarded.
52. ~~**Reads and decisions spent the model budget**~~ Fixed in W13: only routes that call a model take the rate limit.
53. **The Langfuse send runs on the event loop** (bounded at 5 s): a slow Langfuse can stall other requests. Deferred; move it to a background thread before connecting one.
54. **`GET /status` does not check that the corpus is ingested**: an empty corpus sends every run to review. Deferred.

## Weekly cadence

- **Mon:** read/design, write hypotheses or eval set before code.
- **Tue–Thu:** build, 2h each, tests with every change.
- **Fri:** measure, write note/ADR, tag release when scheduled.
- After each week: update ticket statuses in AIENG and the "Current state" section above.
