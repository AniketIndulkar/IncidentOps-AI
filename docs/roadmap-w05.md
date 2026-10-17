# W05 roadmap: knowledge graph and graph-assisted retrieval (2–6 Nov 2026)

Source: Jira epic **AIENG-6** and tasks **AIENG-34 to AIENG-38**, read 17 Oct 2026. Overall plan:
[ROADMAP.md](ROADMAP.md). Built early, stacked on W04 (branch `feat/w05-knowledge-graph`).

> **Status, 17 Oct 2026: built and measured.** Design written before any graph code; unlike W03 and
> W04 there was no approval round (the user asked for W05 to W13 back to back, reviewed PR by PR), so
> every decision below is mine unless marked otherwise.
>
> **Result:** by the rule below, retrieval stays **hybrid**: the soft anchor lost 0.196 MRR on the
> graph set and the graph gained 0.000. The graph goes into the diagnosis as structured **service
> context** instead. H13 and H16 confirmed, H14 partly, H15 refuted, H17 confirmed with two exceptions.
> Findings: [graph-findings](../evals/graph/runs/2026-10-17-graph-findings.md); decision:
> [ADR-002](adr/002-knowledge-graph.md).

## Goal of the week

Model services, incidents, runbooks, engineers and fixes as a graph, answer the questions that only a
graph can answer, and measure where graph context improves retrieval and where it does not.

**Exit criterion (from the epic):** a Neo4j graph, Cypher queries, and a comparative retrieval
evaluation.

```
data/graph/catalogue.toml ──load──▶ Neo4j (local, Docker)        AIENG-35
                                     │  Cypher: impact, history,
                                     │  ownership, fixes, runbooks  AIENG-36
incident (service) ─▶ graph: related documents ─┐
query ─▶ hybrid (open) ────────────────────────┴─▶ RRF ─▶ evidence  AIENG-37
                                         graph set + 30 questions ─▶ ADR-002  AIENG-38
```

## Why W05 matters for the failures W04 left

The questions that failed in every W04 stage (q24, q25, q29) need a *second document*: the runbook
behind a postmortem, or a neighbouring incident. Nothing in a single chunk says which other document
to look at. A graph can: an incident affected a service, a runbook covers that service, a service
depends on another one. That is the testable claim of this week.

## Decisions made by the user

| Decision | Choice |
|---|---|
| Graph database | **Local Neo4j in Docker** (`neo4j:5-community`), not Aura |
| Spend and downloads | Allowed, **$5 total cap** across W05 to W13 |

## Decisions made by me

| Decision | Choice | Why |
|---|---|---|
| Data | A **synthetic service catalogue** in `data/graph/catalogue.toml`: 15 services, 9 teams, 18 engineers, the 7 postmortem incidents, their fixes, and which runbooks cover which services | The ticket asks to "transform the synthetic relational dataset". Facts stated in the corpus (checkout-api uses the payments-gateway circuit breaker, orders-api uses orders-db, …) are kept; the rest is what a service registry would hold, consistent with the corpus. People are fictional. |
| Format | TOML read with the standard library | No new dependency; validated by tests against the corpus |
| Schema | Nodes `Service`, `Team`, `Engineer`, `Incident`, `Fix`, `Runbook`; relationships `DEPENDS_ON`, `OWNS`, `MEMBER_OF`, `AFFECTED`, `CAUSED_BY`, `RESOLVED_BY`, `CHANGES`, `COVERS` | The five node types the ticket names, plus `Team`, which ownership needs |
| Keys | One `uid = "<tenant>/<id>"` property, unique per label | Composite keys and node-key constraints are Enterprise-only; a tenant on every node is ready for W10 |
| Reference implementation | An in-memory graph with the same queries; a test holds Neo4j to it | Same pattern as pgvector vs in-memory (W03) |
| Graph in retrieval | The incident's service (a **hint**, not a filter) selects related documents in the graph; retrieval inside those documents is fused with open hybrid retrieval by RRF | A hard filter would hide the right document when it belongs to another service; fusion keeps the open list |
| Hints | `retrieve(..., hints=QueryHints(services=...))`, optional, passed through by every stage | Filters restrict; hints suggest. Mixing them would break the W03 filter semantics |
| Neighbourhood | Undirected `DEPENDS_ON`, **1 hop**, plus incidents that affected or were caused by those services, plus runbooks that cover them | Diagnosis needs dependencies (where the fault may be) and dependents (who reported it) |
| Diagnosis | The route passes the incident's service as the hint; with a graph configured the response also gets **service context** (owners, on-call, dependencies, recent incidents) as structured data, not as citations | Graph facts are not text and cannot be quoted; they are shown as context |
| Graph unavailable | Diagnosis continues without graph context and says so | The graph is an aid, not a dependency of the answer |
| Tests | Neo4j tests run against a container (`TEST_NEO4J_URI`, default `bolt://localhost:7688`), like Postgres; CI gets a Neo4j service | Real Cypher, not a mock |

## Five traversal questions (AIENG-34, AIENG-36)

| | Question | Expected (from the catalogue) |
|---|---|---|
| T1 | If **orders-db** is down, which services are affected, and how far away? | orders-api (1 hop), checkout-api (2) |
| T2 | Which past incidents involved **checkout-api** or what it depends on (2 hops)? | pm-2026-03-checkout, pm-2026-05-orders-pool, pm-2026-06-payments, pm-2026-08-session |
| T3 | Who owns **payments-gateway**, and who is on call? | Payments; on call Sofia Marquez |
| T4 | Which fixes are still **open** on the services **checkout-api** depends on (2 hops)? | payments circuit breaker, payments alert labels, orders lint rule, PgBouncer, Redis failover test |
| T5 | Which runbooks cover **checkout-api** and its direct dependencies? | rb-checkout-502, rb-payments-latency, rb-orders-db-pool, rb-session-invalid |

None of these can be answered from retrieval alone: ownership, on-call and dependencies are not in the
corpus text. That is the first half of the ADR.

## Graph-sensitive question set, written before the graph retriever

`evals/graph/questions.jsonl`: 12 questions, same format as the retrieval set (evidence is an exact
quote), each with an **anchor service** (what the incident would carry). Categories:

| Category | n | What it tests |
|---|---|---|
| dependency | 5 | The answer is in a document about a service the anchor **depends on** |
| dependent | 2 | The answer is in a document about a service that **depends on** the anchor |
| history | 3 | The answer is in a past incident linked to the anchor |
| same-service | 2 | Controls: the answer is in the anchor's own documents |

## Stages compared

| Stage | What it does |
|---|---|
| S0 hybrid | W04 default, no hints |
| S1 hard filter | S0 restricted to documents whose front-matter service is the anchor (W03's H5) |
| S2 soft anchor | S0 fused by RRF with retrieval inside the anchor's own documents (metadata only, no graph) |
| S3 graph | S0 fused with retrieval inside the graph neighbourhood of the anchor |
| S3a graph, 0 hops | Exploratory: incidents and runbooks linked to the anchor only |

Run on both W04 decision chunkings (`fixed-2000-200`, `section-1500`+header), on the graph set and on
the original 30 questions (their `service` field is the anchor).

## Hypotheses, written before any graph-retrieval code

- **H13.** The hard filter (S1) loses dependency and dependent questions: Recall@5 on those 7 drops
  below S0's, because the evidence belongs to another service.
- **H14.** The graph (S3) beats the soft anchor (S2) on dependency and dependent questions by at least
  one question of Recall@5, and does not on same-service questions.
- **H15.** On same-service questions the graph adds neighbouring noise: S3 MRR ≤ S2 MRR there.
- **H16.** On the original 30 questions the graph changes MRR by less than 0.02 (most are about the
  anchor's own documents).
- **H17.** T1 to T5 cannot be answered by retrieval: the facts are not in the corpus text.

## How the stage is chosen (fixed before the run)

Evaluated on `fixed-2000-200` (production); `section-1500`+header must not contradict it.

1. S2 replaces S0 if, on the graph set, MRR rises by at least **0.04** and Recall@5 does not fall,
   and on the 30 questions MRR falls by no more than **0.02**.
2. S3 replaces the winner of step 1 by the same test.
3. Otherwise the simpler stage stays. On `section-1500`+header the adopted stage must not lose more
   than 0.02 MRR on either set.

0.04 is half a question on a set of 12; the report counts questions moved, as in W04.

## Day by day

- **AIENG-34** schema, catalogue, five traversal questions, this document, graph question set.
- **AIENG-35** loader into Neo4j with uniqueness constraints, idempotent, per tenant.
- **AIENG-36** Cypher for T1 to T5 behind a `GraphStore` interface; the in-memory reference; one query
  plan (`PROFILE`) recorded in the ADR.
- **AIENG-37** `QueryHints`, graph scope, the fused retriever, diagnosis wiring and service context.
- **AIENG-38** comparison, findings, ADR-002.

## Risks

1. **The catalogue is mine.** Graph value measured on a graph I wrote, with questions I wrote, is
   optimistic. Mitigation: controls (same-service), the original 30 questions, and saying so.
2. **Twelve questions.** One question is 8 points of Recall. Read the questions moved.
3. **Document granularity.** With `fixed-2000-200` a chunk is nearly a document, so the graph mostly
   picks documents; the section config tests passages.
4. **A second database to run** locally and in CI.
