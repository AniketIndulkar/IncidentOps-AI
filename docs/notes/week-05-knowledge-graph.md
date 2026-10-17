# Week 5 write-up: knowledge graph and graph-assisted retrieval (AIENG-34 to AIENG-38)

Status on 17 Oct 2026: **built and measured**, as a pull request stacked on W04. Design and hypotheses
were committed before any graph code ([roadmap-w05.md](../roadmap-w05.md)); the numbers are in
[the findings](../../evals/graph/runs/2026-10-17-graph-findings.md) and the decision in
[ADR-002](../adr/002-knowledge-graph.md).

## The result in five lines

1. **The graph answers what retrieval cannot**: who owns a service and who is on call, what breaks if a
   database goes down, which incidents touched a service or its dependencies, which fixes are still
   open upstream. Five Cypher queries, tested against Neo4j and an in-memory reference.
2. **The graph does not improve retrieval on this corpus.** Hybrid already finds every graph question's
   evidence in the top five. Anchoring on the incident's service hurts (MRR −0.196); the graph
   neighbourhood cancels that harm and adds nothing (+0.000). Retrieval stays hybrid.
3. **A hard service filter is the worst option**: it loses every dependency question. A service is a
   hint, not a boundary.
4. The diagnosis now returns **service context** from the graph beside the cited causes, and keeps
   working (`graph_status: "unavailable"`) when Neo4j is down.
5. 563 tests pass (1 skipped), now against Postgres and Neo4j containers.

## What exists now

```
data/graph/catalogue.toml       15 services, 9 teams, 18 engineers, 7 incidents, 24 fixes, 11 runbooks
evals/graph/questions.jsonl     12 graph-sensitive questions with exact-quote evidence
src/incidentops_ai/graph/
  catalogue.py      load + validate (references, cycles, one on-call per team) and against the corpus
  store.py          GraphStore interface: T1–T5, related documents, service context; GraphUnavailableError
  memory.py         the in-memory reference
  neo4j_store.py    loader (uid constraints, replace a tenant in one transaction) and the Cypher
  retriever.py      QueryHints-driven AnchoredRetriever: OwnDocuments or GraphNeighbourhood, fused by RRF
  load.py           python -m incidentops_ai.graph.load
  benchmark.py      five stages × two question sets × two chunkings, and the fixed rule as code
docs/adr/002-knowledge-graph.md
```

## Decisions, with why and why not

| Decision | Why | Why not the alternative |
|---|---|---|
| **A synthetic catalogue** consistent with the corpus, validated on every load | The corpus only states a few relationships; a registry would hold the rest | Extracting relationships from text with an LLM: costly, unverifiable, and the corpus mostly does not contain them |
| **Local Neo4j in Docker** (your choice) | No account, CI can run it as a service | Aura free: not reachable from CI |
| **`uid = tenant/id` with a uniqueness constraint per label** | Index seek per query; tenant on every node for W10 | Composite or node-key constraints are Enterprise-only |
| **Replace a tenant's graph in one transaction** | Readers see the old graph or the new one; removed facts disappear | MERGE-only upserts, which leave deleted facts behind |
| **An in-memory reference held to the same tests** | The answers are defined once and checked in two engines | Mocking the driver, which tests nothing about Cypher |
| **Hints, not filters**, for the incident's service | A filter hides documents of other services; W03 had already shown its cost | A `services` filter, which turned out to lose every dependency question |
| **Fusion of an open and a scoped search** | A document outside the scope can still win | Only searching inside the scope |
| **Hop bounds validated, then written into Cypher** | Variable-length bounds cannot be parameters | String-building with unchecked input |
| **Service context as structured data, not citations** | Graph facts are curated, not quotations | Turning them into fake "evidence" chunks |
| **Graph outage degrades, misconfiguration fails at boot** | The graph is context; a wrong setting should be found at startup | Failing diagnoses when Neo4j blips, or silently ignoring `graph` mode without Neo4j |

## What the numbers taught

- The interesting failure was the **anchor**, not the graph. Telling retrieval "this is a checkout-api
  incident" made it prefer checkout's own documents, which is exactly wrong when the cause is in a
  dependency. The graph's neighbourhood fixed the anchor's mistake, but simply not anchoring was as
  good.
- The graph-sensitive questions turned out not to be hard for search: they name the dependency's
  symptoms in its own words. That is a property of how I wrote them, and it is the first thing to
  change in a harder set.
- One question (q24) wanted the opposite: focus on the anchor's own documents, away from a
  neighbouring incident. The right scope depends on the question.
- H13 and H16 held, H14 held only partly, H15 was refuted (no neighbourhood noise), and H17 held apart
  from two ownership sentences in the corpus.

## What went wrong on the way

- A Python closure bug: assigning to `graph_store` inside the lifespan made it a local variable. Caught by
  the first test that started the app.
- Nine lint findings in the loader from implicit string concatenation inside tuples; parenthesised.
- A suspicion worth checking: "graph vs hybrid: 0 questions moved" looked like the graph doing nothing.
  It was not: all 12 top-ten lists change, just below the evidence.

## What is still weak

- The graph, the questions and the catalogue are all mine.
- Twelve questions, so a single question moves Recall by 8 points.
- There is no relevance gate yet (W04 risk 23), and no claim check (risk 22).

## Where to look

- `uv run python -m incidentops_ai.graph.load` then `uv run python -m incidentops_ai.graph.benchmark`.
- `POST /incidents/{id}/diagnosis` now returns `service_context` and `graph_status` when Neo4j is
  configured (`INCIDENTOPS_NEO4J_URI`, `INCIDENTOPS_NEO4J_PASSWORD`).
