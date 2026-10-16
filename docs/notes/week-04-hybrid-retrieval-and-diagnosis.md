# Week 4 write-up: hybrid retrieval, reranking and cited diagnosis (AIENG-29 to AIENG-33)

Status on 16 Oct 2026: **built and measured; the v0.1 tag is waiting for your go-ahead.** The design,
hypotheses and the rule that picks the stack were committed before any W04 code
([roadmap-w04.md](../roadmap-w04.md)); the numbers are in
[the findings](../../evals/retrieval/runs/2026-10-16-hybrid-findings.md) and the real-model spot check in
[evals/diagnosis](../../evals/diagnosis/2026-10-16-real-model-check.md).

## The result in five lines

1. **Hybrid (dense + BM25, fused by reciprocal rank) is the default stack.** It beats dense by +0.006
   MRR on `fixed-2000-200` and +0.029 on `section-1500`+header: about one question net. It costs under
   1 ms and no API money, so it is adopted; the evidence is thin and the findings say so.
2. **The reranker is built, measured and off by default.** It lowers MRR on both decision configs
   (−0.006, −0.029) and, as pre-registered, is too heavy to run locally (p95 423 to 715 ms, 1.3 GB added
   at the default batch). It does help on small chunks without a header, where the free context header
   gives dense the same MRR.
3. **No AWS needed.** You said to move it to AWS if it hurt the local machine. It missed the bars, but the
   rule does not adopt it, so there is nothing to move. A 1 GB Lightsail box could not hold it anyway.
4. **`POST /incidents/{id}/diagnosis`** answers with causes whose citations the server has checked, or an
   explicit "no answer". In a real-model spot check three on-corpus incidents were answered and two
   off-corpus ones were refused.
5. 487 tests pass offline (1 skipped: the opt-in real-reranker test). 100 more than W03.

## What exists now

```
src/incidentops_ai/retrieval/
  bm25.py            tokeniser that keeps identifiers whole and in parts; Bm25Retriever (k1 1.2, b 0.75)
  hybrid.py          reciprocal_rank_fusion, HybridRetriever (top 50 of each, k 60, equal weights)
  rerank.py          Reranker interface, FakeReranker, CrossEncoderReranker (ONNX, optional extra),
                     RerankingRetriever
  stack.py           build_retriever(dense | hybrid | hybrid-rerank); DbBm25Retriever that follows re-ingestion
  stack_benchmark.py one loop that scores every stage on the same questions and chunks; the report
src/incidentops_ai/diagnosis/
  models.py          what the model returns; the response types (a Cause cannot exist without a Citation)
  evidence.py        numbering, the prompt block, and verify(): the citation check
  service.py         retrieve, ask, check; an empty retrieval never calls the model
src/incidentops_ai/prompts/diagnose_incident/v1.md
apps/api/app/routes/analysis.py   the new endpoint (same token, rate limit and Idempotency-Key as the others)
```

## Decisions, with why and why not

| Decision | Why | Why not the alternative |
|---|---|---|
| **The stack rule was written first**: +0.02 MRR on one decision config, no more than −0.02 on the other, p95 under 1 s, otherwise the simpler stage wins | The reranker's mixed result (better on one config, worse on two) is exactly where a post-hoc story is tempting | Choosing after looking, or choosing on the exploratory config where the reranker looks best |
| **Two decision configs plus one exploratory** | `fixed-2000-200` is at the ceiling (Recall@5 0.983), so gains could not show there | Judging on the config with headroom alone, or hiding the large-chunk result |
| **BM25 written by hand**, no stemming, parameters fixed in advance | A few hundred chunks; exact tests; Postgres `ts_rank` is not BM25 | A library or full-text search I could not test to the last digit; tuning k1 and b on 30 questions |
| **Identifiers indexed whole and in parts** (`PAY-1042`, `pay`, `1042`) | The case where sparse search should win | Splitting on punctuation only |
| **RRF with k = 60, equal weights** | Rank-only fusion needs no score calibration; tuning k on 30 questions would overfit | Adding the scores, or a learned weight |
| **IDF per tenant, filters after scoring** | One tenant's text must never change another's word weights; a service filter must not re-weight words | One global corpus; recomputing statistics per filter |
| **Reranker through ONNX, optional extra** | No torch (about 2 GB), CI and the default image never load it | `sentence-transformers` in the main dependencies |
| **Latency measured with live question embeddings** while chunk vectors stay cached | A cache made dense search look free in the first draft of the harness | Reporting cached latency |
| **Citations are checked on the server**; shown provenance comes from our chunk data | The model can invent an id, misquote, or quote a different chunk | Trusting the model's reported source |
| **Words must match exactly, spacing may differ; quotes need 12+ characters** | A short phrase can sit inside almost any text | Fuzzy matching, which would accept paraphrase |
| **Empty retrieval never calls the model** | Nothing to ground on is a fact we know without asking | Letting the model decide "I have nothing" |
| **No relevance threshold** in v0.1 | RRF scores are not calibrated and a threshold needs off-corpus incidents | A number picked by feel |
| **BM25 index rebuilt when a fingerprint of the stored chunks changes** | A re-ingest must not leave the API serving old text | Rebuilding on every request, or only at startup |
| **Retrieval failure is a 502 `retrieval_failed`** with a fixed message, details in the log | A provider's error text is not for the client; the key is freed so a retry retries | An unhandled 500 |

## What the numbers taught

- Dense, BM25 and hybrid are close on this corpus; the differences are one or two questions. The honest
  headline is "hybrid is never meaningfully worse and costs nothing", not "hybrid wins".
- BM25 is **better on identifiers with small chunks** (q06, the `INDEXER_BATCH_SIZE` definition) and
  **blind to "updates" vs "update"** (q10): no stemming was a pre-registered choice and it has a visible
  cost. Stemming is the next cheap experiment, on the larger golden set (W11).
- The reranker fixes "right document, wrong section" when the question echoes the section heading (q28,
  q10, q29) and breaks cases where topical overlap beats intent (q15) or the answer is one line in a
  section about something else (q13). On small chunks the free context header buys the same MRR.
- Two hypotheses were refuted outright (H10: the reranker lifts the section config; H12: it is under
  300 ms) and two held only in part (H7, H9). Writing them first made that visible, again.
- The still-failing questions (q24, q25, q29) are about *sources*, not passages: the neighbouring incident
  or the second document. That is the knowledge graph's job (W05).

## What went wrong on the way (and was fixed)

- The first harness design would have embedded questions through the same cache as the chunks, so dense
  latency would have read as ~0 ms. Noticed while building the latency column and fixed before any real run
  by embedding questions live.
- The hybrid-vs-pgvector equality test failed at q04: two chunks tied to seven decimals swap places once
  vectors are stored as float32. Not a hybrid bug; the test is now exact where the dense lists agree and
  tolerant (scores per rank within 1e-5) where they do not, and it must make at least 40 exact comparisons
  so it cannot pass by skipping.
- The first idea for "memory added" was the process's peak memory, which hides growth that stays under an
  earlier peak. It was replaced, before any run, by the process's current resident memory before and after.
- Several of my own test expectations were wrong (a regex written back to front, a case-sensitive word,
  a missing tenant on a test document). The code was right each time; the tests were fixed.
- Two hypotheses cited questions the context header already fixed for dense; caught in a self-review of
  the design before it was approved.

## What is still weak

- The evidence for hybrid is about one question. The eval set is 30 questions by one author.
- A citation proves the words exist, not that the claim follows (the Kafka answer joined a runbook fact to
  an inference from an older incident; it was hedged in the claim, but the server could not have known).
- No relevance gate: off-corpus incidents still reach the model, which refused two out of two.
- The reranker numbers are one laptop, one run each; "memory added" includes loading the model.
- `ROADMAP` risk 19 (CI on the new Postgres image) is still unverified; no CI run happened this week.

## Where to look

- Run the comparison: `uv run --extra rerank python -m incidentops_ai.retrieval.stack_benchmark`
  (`--fake` for an offline wiring check; `--stages dense,bm25,hybrid` to skip the model).
- Serve the diagnosis: ingest and index the corpus, set `OPENAI_API_KEY`, `INCIDENTOPS_LLM_MODEL` and
  `INCIDENTOPS_API_TOKEN`, then `POST /incidents/{id}/diagnosis`. `INCIDENTOPS_RETRIEVAL_STACK` picks
  `dense`, `hybrid` (default) or `hybrid-rerank`.
