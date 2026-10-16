# Hybrid retrieval and reranking: findings (AIENG-29 to AIENG-31, AIENG-33)

Run on 16 Oct 2026 with `text-embedding-3-small` (1536 dimensions). Chunk vectors came from the W03
cache; the 30 question vectors were live OpenAI calls, so latency includes the network. Evidence:

- [stack comparison](2026-10-16-stack-comparison.md) and its [per-question data](2026-10-16-stack-comparison.json): Dense, BM25, Hybrid, Hybrid+rerank on three chunk configs
- reranker depth/batch sweeps (tables only): [depth 10](2026-10-16-sweep-d10-b8.md), [depth 20](2026-10-16-sweep-d20-b8.md), [depth 50](2026-10-16-sweep-d50-b8.md), all with batch size 8

Plan, hypotheses H7 to H12, the selection rule and the "too heavy locally" bars were committed before
any W04 code: [roadmap-w04.md](../../../docs/roadmap-w04.md). The dense rows reproduce the W03
baseline exactly (MRR 0.961 / 0.889 / 0.801), so the new harness is measuring the same thing.

## The choice

| | |
|---|---|
| Retrieval stack | **Hybrid**: dense (pgvector) + BM25, fused by reciprocal rank (k = 60, top 50 of each, equal weights). **No reranker by default.** |
| Chunking | Unchanged from W03: `fixed-2000-200` |
| Reranker | Built, tested and measured; stays behind the interface as an optional extra (`uv sync --extra rerank`), off by default |

By the rule fixed in advance (a stage replaces the one before it if MRR rises by at least 0.02 on
one decision config, falls by no more than 0.02 on the other, and p95 stays under 1,000 ms):

| Step | `fixed-2000-200` | `section-1500`+header | Verdict |
|---|---|---|---|
| Dense → Hybrid | 0.961 → 0.967 (+0.006) | 0.889 → 0.918 (+0.029) | **adopted**; p95 248 and 253 ms |
| Hybrid → Hybrid+rerank | 0.967 → 0.961 (−0.006) | 0.918 → 0.889 (−0.029) | **not adopted**: neither config rises, one falls by more than 0.02 |

**Read this before relying on the choice.** The evidence for hybrid is thin. On the section config
three questions improved (q06, q22, q24) and two got worse (q10, q28): a net of one question, and
Recall@5 is unchanged (0.950). On `fixed-2000-200` three moved up (q01, q08, q25) and one down (q24);
the visible effect is q25 now having its evidence in the top five, which takes Recall@5 from 0.983
to 1.000, the only config and stage (with the reranker) with no misses. Hybrid is chosen because it is
never meaningfully worse and costs no extra API money and under 1 ms (BM25) locally, not because the
gain is large. One question is 3.3 points.

## Numbers

Recall@5 / MRR@10 / Hit@1, then query latency p95 (end to end, noisy: the live embedding call has a
tail, which is why dense p95 is 771 ms on the first config but 337 and 246 ms on the others).

| Stage | `fixed-2000-200` (decision) | `section-1500`+header (decision) | `section-1500` (exploratory) |
|---|---|---|---|
| Dense | 0.983 / 0.961 / 0.933, p95 771 | 0.950 / 0.889 / 0.833, p95 337 | 0.933 / 0.801 / 0.700, p95 246 |
| BM25 | 0.983 / 0.957 / 0.933, no API | 0.900 / 0.815 / 0.733 | 0.850 / 0.764 / 0.667 |
| **Hybrid** | **1.000 / 0.967 / 0.933**, p95 248 | **0.950 / 0.918 / 0.867**, p95 253 | 0.867 / 0.793 / 0.667, p95 216 |
| Hybrid+rerank (depth 20) | 1.000 / 0.961 / 0.933, p95 895 | 0.967 / 0.889 / 0.800, p95 648 | 0.967 / **0.889** / 0.800, p95 608 |

Estimated cost: dense and hybrid pay one query embedding (about $0.0004 per 1,000 queries); BM25 and
the local reranker pay no API money. Text read for the top five: 8,384 characters (hybrid,
`fixed-2000-200`) against 2,037 (`section-1500`+header), the W03 caveat that still stands.

## Does the reranker earn its latency? Not on the decision configs

- On the two configs the rule uses it does not raise MRR, and it costs about 300 to 650 ms more per
  query on average (end to end p95 895 and 648 ms against 248 and 253 ms).
- It does help where hybrid is weak. On plain `section-1500` (no header) MRR goes 0.793 → 0.889 and
  misses fall from 6 to 2 questions. But dense with the **free** context header reaches the same MRR
  (0.889) with no model at all. The header is the cheaper fix; the reranker would earn its place
  only for a corpus whose chunks cannot carry a header.
- Deeper candidates do not help MRR: depth 10, 20 and 50 give identical MRR on all three configs.
  Depth 50 adds 0.017 Recall@5 on the small-chunk configs (one question's evidence enters the top
  five) at about twice the latency.

### Local cost, against the bars fixed in advance (p95 ≤ 300 ms and memory added ≤ 500 MB for 20 candidates)

Measured on an Apple M3 Pro (12 cores, 36 GB), ONNX runtime, CPU only. Rerank time is the
reranker's own time per question, not an end-to-end difference.

| Setting | `fixed-2000-200` p95 | `section-1500`+header p95 | `section-1500` p95 | Memory added when the model loads |
|---|---|---|---|---|
| depth 20, batch 64 (**pre-registered**) | 715 ms | 455 ms | 423 ms | **1,298 MB** |
| depth 20, batch 8 | 688 ms | 300 ms | 326 ms | 658 MB |
| depth 10, batch 8 | 355 ms | 192 ms | 209 ms | 650 MB |
| depth 50, batch 8 | 808 ms | 631 ms | 645 ms | 647 MB |

- **As pre-registered, the reranker is too heavy locally** on both bars: 423 to 715 ms p95 and 1.3 GB
  added. Smaller batches halve the memory (the 512-token inputs make big batches expensive), and
  depth 10 brings the small-chunk configs under the latency bar, but nothing measured gets under
  500 MB, and `fixed-2000-200` stays above 300 ms because its chunks are cut at the 512-token limit
  (87 of 600 reranked candidates, 14.5%).
- CPU: the full run kept about 2.5 cores of 12 busy for 90 seconds (the sweeps 2 to 3.6 cores for
  50 to 90 seconds). I did not see the machine slow down, but I did not measure interactive
  responsiveness either.
- A 1 GB Lightsail box cannot hold 650 MB to 1.3 GB of reranker plus the app. Because the rule does
  not adopt the reranker, **no AWS move is needed for W04**. If a later corpus needs it, the order
  set in the design is: depth 10 and a small batch, then a hosted reranker, then a bigger instance.
- The int8 model named in the design's fallback ladder is not available for this model in
  `fastembed` 0.9.0 (its int8 listing is for a 570 MB multilingual model), so that step was not run.

## Hypotheses

| | Said beforehand | Result |
|---|---|---|
| H7 | BM25 below dense overall, worst on paraphrase, no better on identifier | **Partly.** Overall a tie on `fixed-2000-200` (0.957 vs 0.961), worse on the section configs (0.815 vs 0.889; 0.764 vs 0.801). Worst style is `multi` (0.592), not paraphrase (0.781). On identifier BM25 is **better** on small chunks (1.000 vs 0.900, all from q06), a tie on large ones; one question, so noise-level. |
| H8 | BM25 repairs the rare-word miss q22 | **Confirmed, narrowly.** On plain `section-1500` the relevant chunk is rank 1 for BM25, rank 5 for dense, rank 3 for hybrid. On `section-1500`+header hybrid never scores below dense on Recall@5 (0.950 both). Outside the claim: on plain `section-1500` hybrid's Recall@5 (0.867) is below dense (0.933). |
| H9 | Hybrid within 0.02 of the better source on both configs; no gain on the big-chunk config; does not fix q25/q29 | **Mostly confirmed.** Within 0.02 on both (+0.006, +0.029). Hybrid does fix q25 on `fixed-2000-200` (Recall@5 0.983 → 1.000), which was predicted unfixable; q24, q25, q29 still miss on the section config. |
| H10 | Reranker raises MRR by at least 0.02 on `section-1500`+header; does nothing for q25/q29 | **Refuted.** 0.918 → 0.889 (−0.029). It improved q10 and q28, recovered q29 (predicted out of reach) and broke q11 and q13. |
| H11 | Reranker does not help, may hurt, on `fixed-2000-200` (512-token cut) | **Confirmed in direction, within noise.** 0.967 → 0.961; q08 better, q15 and q24 worse; 14.5% of candidates cut. The two regressions are not truncation cases (see below). |
| H12 | Reranker adds under 300 ms p95 for 20 candidates | **Refuted** as pre-registered (715, 455, 423 ms). True only for depth 10 on small chunks. |

## Regressions, read in the retrieved text

Only questions that got worse are listed; the report has every miss.

| Case | What happened | Mode |
|---|---|---|
| q10 (BM25, then hybrid, section+header) | "How often must we post updates during a SEV1 incident?" The answer row says "update every 15 minutes". BM25 has no stemming (fixed in advance), so "updates" never matches "update", and BM25 returns nothing relevant in its top 10. Hybrid lands at rank 5 (dense alone: 4). The reranker lifts it to 2, because the top sections are all siblings of one document. | **No stemming** (a design choice, not noise) plus near-tied sibling sections |
| q24 (hybrid, then rerank, `fixed-2000-200`) | "Why did the double charge happen on a technical level?" Evidence is the billing postmortem and runbook, which dense ranks 1 and 2. BM25's top two are both payments-timeout documents (the neighbouring incident); fusion lifts them to 1 and 3 and puts the billing postmortem at 2. The reranker keeps the payments documents first and puts the billing pair last (scores -5.4 and -6.7 against -7.9 and -8.2). | **Vague question plus a neighbouring incident** (W03 case 2): BM25 follows the shared words, and neither fusion nor the reranker knows that "double charge" means "duplicate idempotency key" |
| q15 (rerank, `fixed-2000-200`) | "Renewals are charging subscribers twice... What do I do first?" The runbook (gold) and the postmortem of the same incident are the top two at every stage; the reranker swaps them on a near tie (5.87 against 5.78). | **Near tie between two documents about one incident**: the question asks for the action, which favours the runbook, and the reranker does not weigh that |
| q11 (rerank, section+header) | The gold Summary section of the payments postmortem drops from rank 1 to 2 behind that postmortem's Timeline section. | **Sibling sections of one document** nearly tied |
| q13 (rerank, section+header) | "Why don't password reset emails arrive when the message broker is unreachable?" The gold sentence ("Password reset emails travel through this worker") sits in the runbook's Escalation section; the reranker prefers the sections about the broker failing, dropping it from 1 to 3. | **A small fact in a section about something else** (W03 case 8, again) |
| q28 (hybrid, section+header) | "What follow-up actions came out of the March checkout 502 postmortem?" Hybrid ranks the Summary first; the reranker puts "Action items" first by reading the question against the section header. This is the reranker working as intended. | (A win, listed for contrast) |

What the table says about when a reranker helps: it reads the question against the heading and the
passage, so it fixes "right document, wrong section" when the wording of the question echoes the
heading (q28, q10, q29). It regresses when topical overlap points at the wrong passage (q15, q24) or
when the answer is one line in a section about something else (q13).

## What this means for later weeks

- **Hybrid goes into the API** as the default stack (W04 diagnosis uses it).
- **Stemming is the cheapest next experiment for BM25.** It was left out because parameters were fixed
  before the run; trying it now would be tuning on a 30-question set. Try it when the golden set grows
  (W11, AIENG-64).
- **q24, q25 and q29 are source-level failures** (a second or neighbouring document), and the
  knowledge graph (W05) is the planned fix. They are the first test for that retriever.
- **The reranker belongs to small-chunk corpora with no header, or to long documents.** Revisit when
  W06 brings long PDFs and the chunk size has to drop.
- The context header and hybrid are cheap; the reranker is not. Order the experiments that way.

## Limits

One author wrote the corpus and the questions; 18 documents, 30 questions; one question is 3.3 points
of Recall and about 0.033 of MRR, so most differences above are one or two questions. Latency is
single-run, includes live API calls with a tail, and was measured on one laptop. "Memory added" is the
growth of resident memory across the stage, so on the first config it includes loading the model and
the ONNX workspace. The sweeps keep their tables but not their per-question data.
