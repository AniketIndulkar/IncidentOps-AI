# Graph-assisted retrieval: findings (AIENG-37, AIENG-38)

Run on 17 Oct 2026 with `text-embedding-3-small`; chunk vectors from the W03 cache, question vectors
live. Evidence: [graph comparison](2026-10-17-graph-comparison.md) and its
[per-question data](2026-10-17-graph-comparison.json). Design, hypotheses H13 to H17 and the rule
were committed before any graph code: [roadmap-w05.md](../../../docs/roadmap-w05.md).

## The choice

| | |
|---|---|
| Retrieval | **Unchanged: hybrid**, no graph scope. `INCIDENTOPS_GRAPH_RETRIEVAL` defaults to `off` |
| Graph in the product | **Service context** next to each diagnosis: owning team, on-call engineer, dependencies, dependents, recent incidents (structured, not cited) |
| Why | By the rule fixed in advance, neither the soft anchor nor the graph cleared the bar: anchor −0.196 MRR on the graph set, graph +0.000 |

## Numbers (production chunking, `fixed-2000-200`)

MRR@10 / Recall@5:

| Stage | 12 graph questions | 30 retrieval questions |
|---|---|---|
| **hybrid** | **0.701 / 1.000** | **0.967 / 1.000** |
| hard service filter | 0.250 / 0.333 | 0.983 / 1.000 |
| soft anchor (own documents) | 0.506 / 1.000 | 0.983 / 1.000 |
| graph, 1 hop | 0.701 / 1.000 | 0.967 / 1.000 |
| graph, 0 hops (exploratory) | 0.506 / 1.000 | 0.983 / 1.000 |

MRR by category on the graph set:

| Category | n | hybrid | hard filter | anchor | graph | graph 0 hops |
|---|---|---|---|---|---|---|
| dependency | 5 | 0.583 | 0.000 | 0.307 | 0.583 | 0.307 |
| dependent | 2 | 0.750 | 0.000 | 0.600 | 0.750 | 0.267 |
| history | 3 | 0.667 | 0.333 | 0.444 | 0.667 | 0.667 |
| same-service | 2 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |

Section chunks with the context header (the check config) tell the same story: graph-set MRR 0.345
(hybrid), 0.079 (hard filter), 0.175 (anchor), 0.346 (graph); retrieval set 0.918 / 0.934 / 0.934 /
0.918.

Questions moved against hybrid on the graph set: hard filter −8, anchor −6, graph 0. Graph against
anchor: +6 (the same six the anchor lost).

## What happened

- **Open hybrid retrieval already finds every graph question's evidence in the top five.** The
  questions describe the dependency's symptom in its own words ("hangs for 30 seconds", "idle in
  transaction"), so search goes there without being told which service to look at.
- **The incident's service is a misleading hint for dependency questions.** Boosting the anchor's own
  documents pushed the real answer down: dependency MRR 0.583 → 0.307. The hard filter, which removes
  everything else, loses all seven dependency and dependent questions.
- **The graph cancels the anchor's harm but adds nothing beyond it.** Its neighbourhood includes the
  dependencies' documents, so the six questions the anchor lost come back. It never beats doing
  nothing. The graph does reorder lower ranks (all 12 top-ten lists change), but the documents ranked
  above the evidence are already in the neighbourhood, so the evidence does not move.
- **One counterexample: q24** ("Why did the double charge happen?"). There the anchor helps (billing's
  own documents beat the neighbouring payments incident), and the graph loses that help because its
  one-hop neighbourhood includes payments-gateway. The right scope depends on the question: a
  dependency question wants breadth, a "this exact incident" question wants focus.
- **Postmortem sections are hard for every stage** on the section config (history MRR about 0.09): a
  passage problem, which the graph does not touch.

## Hypotheses

| | Said beforehand | Result |
|---|---|---|
| H13 | A hard service filter loses dependency and dependent questions | **Confirmed**, completely: MRR 0.000 on both categories, Recall@5 0.333 on the graph set |
| H14 | The graph beats the soft anchor on dependency and dependent questions by ≥ 1 question of Recall@5 | **Partly.** On the section config yes (Recall@5 0.167 → 0.667); on `fixed-2000-200` Recall@5 is 1.000 for both, and the gain shows only in MRR (+6 questions moved) |
| H15 | On same-service questions the graph adds neighbouring noise | **Refuted**: same-service MRR is identical (1.000 and 0.333) |
| H16 | On the original 30 questions the graph moves MRR by less than 0.02 | **Confirmed**: 0.967 → 0.967, no question moved |
| H17 | T1 to T5 cannot be answered by retrieval | **Confirmed**, with two exceptions: the corpus states the owner of checkout-api and of orders-db. Dependencies, downstream impact, on-call engineers and open fixes across services appear nowhere as text |

## Cost and latency

An anchored stage searches twice per question (open and scoped, concurrently), so it pays for two
query embeddings, about $0.0014 per 1,000 queries against $0.0007. p95 latency stayed within
205 to 360 ms; the hybrid row's 2,118 ms p95 on the graph set is one slow embedding call (a network
tail), not graph work. The Neo4j query for T2 is ~150 database hits (see ADR-002).

## Limits

The catalogue and both question sets are mine, so graph value is measured on a graph I wrote. Twelve
questions: one question is 8 points of Recall and up to 0.083 of MRR. With `fixed-2000-200` a chunk is
close to a whole document, so the graph mostly decides documents, not passages. The graph questions
name symptoms in the dependency's words; incidents that describe only the reporting service's view
("checkout is slow") might need the graph more, and would need a new set to test.
