# Dense retrieval baseline: findings (AIENG-26 and AIENG-28)

Run on 16 Oct 2026 with `text-embedding-3-small` (1536 dimensions). Evidence:

- [chunking experiment, small](2026-10-16-chunking-text-embedding-3-small.md) and its [per-question data](2026-10-16-chunking-text-embedding-3-small.json)
- [chunking experiment, large](2026-10-16-chunking-text-embedding-3-large.md) (for H6)
- [dense baseline on pgvector](2026-10-16-dense-baseline.md) and its [per-question data](2026-10-16-dense-baseline.json)

Plan, hypotheses and the selection rule were committed before any retrieval code: [roadmap-w03.md](../../../docs/roadmap-w03.md).

## The choice

| | |
|---|---|
| Chunking | **`fixed-2000-200`**, chosen by the rule fixed in advance: highest MRR (0.961, next best 0.801) |
| Embedding | **`text-embedding-3-small`**, 1536 dimensions; `-large` gained 0 points of Recall@5 on this config, under the 5-point bar |
| Search | exact cosine scan in pgvector, tenant filter always, service filter optional |
| Context header | not used: it lowers MRR for this config (0.961 to 0.950) |

**Read this before relying on the choice.** The average document is about 1,900 characters, so a
2,000-character chunk is close to a whole document. "Retrieve the right chunk" then means "retrieve
the right document", which is easy, and it costs the most text: **7,433 characters read for the top
five, against 1,961 for `section-1500`** (3.8 times more). The rule was not changed after seeing
that. The runner-up to use when documents get long (W06 PDFs, W11 expansion) is **`section-1500`
with the context header** (`ingest --chunker section-1500`, `index --context`): MRR 0.889, Recall@5
0.950, 2,016 characters read.

## Numbers

| Config (small model) | Recall@5 | Precision@5 | MRR | Chars read@5 |
|---|---|---|---|---|
| fixed-500-50 | 0.917 | 0.260 | 0.690 | 2,332 |
| fixed-1000-100 | 0.967 | 0.273 | 0.673 | 4,198 |
| **fixed-2000-200** | **0.983** | 0.260 | **0.961** | 7,433 |
| paragraph-1000 | 0.933 | 0.227 | 0.725 | 3,659 |
| section-1500 | 0.933 | 0.233 | 0.801 | 1,961 |
| section-1500 + context header | 0.950 | 0.240 | 0.889 | 2,016 |

Baseline on pgvector (the chosen config): Recall@1/3/5/10 = 0.800 / 0.983 / 0.983 / 1.000, Hit@1 =
0.933, MRR@10 = 0.961, **one** question (q25) with evidence outside the top five. Query latency
(embedding call plus search): mean 210 ms, p50 198 ms, p95 285 ms, almost all of it the embedding
request. pgvector and the in-memory experiment give the same MRR (0.961), so the two paths agree on
real vectors, not only on the fake ones.

Thirty questions: one question is 3.3 points. Differences under about 5 points are noise.

## Hypotheses

| | Said beforehand | Result |
|---|---|---|
| H1 | Section-aware beats fixed windows on MRR | **Partly.** `section-1500` (0.801) beats `fixed-500` (0.690) and `fixed-1000` (0.673) while reading the same or less text. It loses to `fixed-2000` (0.961), which is nearly whole documents. |
| H2 | Small chunks: best precision, worse recall on procedure and multi-span | **Half.** Recall@5 is the lowest of all (0.917) and multi-span MRR is poor (0.567). But Precision@5 is not the best (0.260; `fixed-1000` has 0.273). |
| H3 | 2,000-character chunks lower MRR | **Refuted** here (0.961, the highest), for the corpus-size reason above. The real cost shows elsewhere: see q08. |
| H4 | Dense is weakest on identifier questions | **Refuted** at this size. Identifier MRR is 1.000 for the chosen config and is never the lowest style; multi-span is. The identifier failure that exists is of another kind (q06). |
| H5 | A service filter raises Precision@K | **Directionally yes, within noise.** Precision@5 0.260 to 0.267, MRR 0.961 to 0.983, Recall@5 0.983 to 1.000. It also halves the text read (7,433 to 3,664). |
| H6 | `-large` is under 5 points better on Recall@5 | **Confirmed** for the chosen config: 0.983 vs 0.983. Only `paragraph-1000` gained the full 5 points (0.933 to 0.983). |

Not predicted: the context header lifts small chunks by about 0.08 MRR (`fixed-500` 0.690 to 0.774,
`section-1500` 0.801 to 0.889) and does nothing for large ones. It gives a small chunk the
document and section it has lost.

## Failure modes, from ten inspected cases

Each case was read in the retrieved text, not inferred from scores. The chosen config misses only
one question, so most cases come from the other configs and from near-misses; they are where the
chosen config will fail on a harder corpus.

| # | Case (config) | What happened | Mode |
|---|---|---|---|
| 1 | q25 (chosen), Recall@5 0.50 | "Why did the February notification outage take so long to diagnose?" The postmortem is rank 1; ranks 2 to 5 are four *other* postmortems. The runbook half never appears. The question never says Kafka, TLS or certificates. | **Second source shares no words with the question.** Generic words ("outage", "diagnose") pull in postmortem boilerplate. |
| 2 | q24 (`fixed-500`), no hit in top 10 | "Why did the double charge happen on a technical level?" Rank 1 is the payments-gateway postmortem about charge timeouts. The idempotency-key sentence is not retrieved. | **Vague question plus a neighbouring incident.** A service filter would exclude that postmortem (not measured for this config). |
| 3 | q06 (`fixed-500`, `section-1500`) | "What does INDEXER_BATCH_SIZE control, and what is the safe default?" The token appears in four places; the section that *uses* it (Prevention) scores highest, the sentence that *defines* it ranks 4th or 5th. | **Mention is not definition.** BM25 would not help (the usages repeat the token); a reranker that reads the question would. |
| 4 | q22 (`section-1500`), Recall@5 0.50 | "...checkout 502 errors come from a keepalive mismatch?" Ranks 1 to 3 are the runbook's title, Symptoms and First checks. The sentence with "keepalive" is in a section outside the top 5. | **Topic words beat the rare content word.** BM25 weights "keepalive" heavily; with the context header the same query is fully recalled. |
| 5 | q29 (`section-1500`, `paragraph-1000`), Recall@5 0.50 | "...clock drift during the German login incident?" Ranks 1 to 4 are four sections of the same postmortem; the runbook's clock-skew section is missing. | **One document crowds out another.** Needs diversity across sources or fusion. |
| 6 | q05 (`fixed-500`) | "Which command rolls back a bad checkout-api release?" The chunk holding the `undo` line is 4th, behind three chunks of the same runbook scoring 0.54, 0.54, 0.50. | **Same-document chunks are nearly tied**; the window boundary sits next to the command. |
| 7 | q01 (chosen), found at rank 2 | "How many database connections can one orders-api pod open?" Rank 1 is the *failover* runbook, which also talks about connections to the primary. | **Near-duplicate topic.** Closely related documents compete. |
| 8 | q08 (chosen), found at rank 3 | "How fast can the SMS provider send messages?" The top three scores are 0.204, 0.199, 0.195; typical correct scores are 0.4 to 0.6. | **A small fact diluted in a large chunk.** One sentence cannot move a 2,000-character embedding. This is the true cost of big chunks, hidden by corpus size. |
| 9 | q24 (`fixed-1000`), Recall@5 0.50 | Same question as case 2; half of the evidence found at rank 2. | as case 2 |
| 10 | q25 (all configs) | Recall@5 is 0.50 or 0.00 in every config. | The hardest question in the set; case 1. |

## What this means for W04

- Add BM25 where it can help: cases 4 and (partly) 8. Do not expect it to help case 3.
- A reranker earns its latency on case 3, case 6 and probably case 7, where the right *passage* is
  ranked below the right *topic*. W04's cross-encoder question is therefore testable here.
- Cases 1 and 5 are about sources, not scores. Diversity (a cap per document) and query expansion
  are candidate fixes; the knowledge graph (W05) addresses case 1 directly.
- **Headroom is small.** With the chosen config the benchmark is near its ceiling (Recall@5 0.983),
  so Dense vs BM25 vs Hybrid vs Hybrid+rerank may all look alike. Run the W04 comparison on
  `section-1500` as well, where there is room to improve, and add harder questions (W11,
  AIENG-64). Questions q24 and q25 are the ones to build on.

## Limits

One author wrote the corpus and the questions; 18 documents, 30 questions, 42 evidence spans;
single run per config (embedding vectors are cached by text hash, so a repeat gives the same
numbers); Precision@K is capped near 0.4 by the number of evidence spans.
