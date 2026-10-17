# ADR-002: Where the knowledge graph belongs in IncidentOps, and where it does not

- **Status:** accepted (W05, 17 Oct 2026)
- **Context tickets:** AIENG-34 to AIENG-38
- **Evidence:** [graph findings](../../evals/graph/runs/2026-10-17-graph-findings.md),
  [W05 design and hypotheses](../roadmap-w05.md)

## Context

W04 left three questions that every retrieval stage failed, and they needed a *second* document: the
runbook behind a postmortem, or a neighbouring incident. The hypothesis for W05 was that a graph of
services, incidents, runbooks, people and fixes could say which second document to read.

We built a synthetic service catalogue (15 services, 9 teams, 18 engineers, 7 incidents, 24 fixes,
11 runbooks), loaded it into Neo4j, answered five traversal questions in Cypher, and measured graph
context in retrieval against a rule fixed before the run.

## Decision

1. **The graph answers structured questions, and the diagnosis shows them as context.** Ownership,
   on-call, dependencies and dependents, downstream impact, incident history across dependencies and
   open fixes on what a service relies on. None of this is in the document text (two ownership
   sentences aside), so retrieval cannot answer it at any quality. The diagnosis response carries a
   `service_context` beside the cited causes. It is not quoted evidence and is never presented as a
   citation.
2. **The graph does not steer retrieval by default.** `INCIDENTOPS_GRAPH_RETRIEVAL=off`. On our
   questions, open hybrid retrieval already reached Recall@5 1.000; anchoring on the incident's
   service hurt (MRR −0.196), and the graph neighbourhood only cancelled that harm (+0.000 against
   hybrid). The switch (`anchor`, `graph`) stays, because a corpus whose documents do not name their
   dependencies' symptoms could change the answer, and the harness can measure it.
3. **Never hard-filter retrieval by the incident's service.** It lost every dependency and dependent
   question (MRR 0.000). A service is a hint, not a boundary.
4. **The graph is optional at runtime.** If Neo4j is down, diagnosis continues; retrieval falls back
   to the open search and the response says `graph_status: "unavailable"`.

## Where the graph does not belong

- **As a substitute for retrieval.** It holds relationships, not explanations. "What does PAY-1042
  mean" is a passage question.
- **As a hard scope.** Shown above: the right answer often lives in another service's documents.
- **As citation evidence.** Graph facts are curated data, not quotations; they belong in a separate,
  labelled part of the response.
- **For passage ranking.** Postmortem sections were hard for every stage; that is a chunking and
  ranking problem.

## Consequences

- One more datastore in development and CI (a Neo4j container, `neo4j:5-community`). Graph tests run
  against it and against an in-memory reference that must give identical answers.
- The catalogue is data we maintain. It is validated against the corpus on every load and in tests,
  so it cannot reference a runbook or postmortem that does not exist.
- The two-search fusion doubles query embeddings when enabled (about $0.0007 more per 1,000 queries).

## Notes on the Cypher

Every node has a `uid` of `<tenant>/<id>` with a uniqueness constraint per label (Community edition
has no node-key or composite constraints). The constraint gives each query an index seek as its
starting point. `PROFILE` of T2 (incidents across checkout-api's dependencies, 2 hops):

```
ProduceResults        rows=4  dbHits=0
  Projection          rows=4  dbHits=4
    Sort              rows=4  dbHits=0
      CacheProperties rows=4  dbHits=8
        EagerAggregation rows=4 dbHits=17
          Filter      rows=10 dbHits=10
            Expand(All)  rows=10 dbHits=59      ← incident edges of the 7 services in scope
              Filter  rows=7  dbHits=7
                VarLengthExpand(Pruning,BFS,All) rows=7 dbHits=40   ← DEPENDS_ON*0..2
                  NodeUniqueIndexSeek rows=1 dbHits=2               ← the uid constraint
```

About 150 database hits for the whole question. Variable-length bounds cannot be query parameters
in Cypher, so hop limits are validated integers (0 to 4) written into the query.

## Revisit when

- the corpus grows to many services whose documents describe symptoms only from their own side;
- incidents arrive with only the reporting service's view ("checkout is slow");
- W10 adds permissions: the graph's tenant property is ready for it, but access rules on people data
  (on-call names) will need the same server-side filtering as documents.
