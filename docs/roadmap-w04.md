# W04 roadmap: hybrid retrieval, reranking and cited diagnosis (26–30 Oct 2026)

Source: Jira epic **AIENG-5** and tasks **AIENG-29 to AIENG-33**, read 16 Oct 2026. All six were To Do.
Overall plan: [ROADMAP.md](ROADMAP.md). Built early, like W02 and W03, on branch
`feat/w04-hybrid-rerank-diagnosis` (cut from the W03 tip).

> **Status, 16 Oct 2026: complete, except the `v0.1` tag, which waits for your go-ahead.** BM25, RRF,
> the reranker, the comparison and the cited diagnosis endpoint are built and measured (AIENG-29 to 33).
> The design below is unchanged from when it was approved.
>
> **Result:** by the rule fixed below, the default stack is **hybrid** (dense + BM25, RRF); the
> reranker is not adopted. Hybrid beats dense by +0.006 MRR on `fixed-2000-200` and +0.029 on
> `section-1500`+header (a net of about one question). The reranker lowers MRR on both decision
> configs (−0.006, −0.029) and, as pre-registered, is too heavy to run locally (p95 423 to 715 ms,
> 1.3 GB added), so no move to AWS is needed. Findings:
> [hybrid-findings](../evals/retrieval/runs/2026-10-16-hybrid-findings.md).
>
> Changes from this plan: (1) the reranker runs through ONNX (`fastembed`, model
> `Xenova/ms-marco-MiniLM-L-6-v2`, the ONNX export of the planned model), so no torch;
> (2) `--rerank-depth` and `--rerank-batch-size` were added to run the depth sweep the design asks for
> and the smaller-batch fallback; (3) the benchmark times question embeddings live while chunk
> vectors stay cached, otherwise the cache would hide the dense latency; (4) truncation of reranker
> input is counted exactly from the model's tokenizer; (5) the BM25 index used by the API takes its
> tenant from each query and rebuilds when a fingerprint of the stored chunks changes; (6) a citation
> shows the source's own text for the quote (not the model's wording) and the chunk's page range.
>
> Write-ups: [W04 note](notes/week-04-hybrid-retrieval-and-diagnosis.md),
> [October retrospective](notes/retro-october-2026.md), diagnosis spot check in
> [evals/diagnosis](../evals/diagnosis/2026-10-16-real-model-check.md).

## Goal of the week

Move from dense-only retrieval to **measured** hybrid retrieval and reranking, then use the result to
produce a diagnosis in which every cause is backed by a citation we have checked. Ship it as
**IncidentOps v0.1**.

**Exit criterion (from the epic):** a Dense vs BM25 vs Hybrid vs Hybrid+reranker benchmark, and a
grounded diagnosis output.

```
incident report
   │  query = title + description               AIENG-32
   ▼
dense (pgvector) ─┐
                  ├─ RRF fusion ─▶ cross-encoder rerank ─▶ top 5 evidence chunks
BM25 (in memory) ─┘   AIENG-30      AIENG-31                     │
   AIENG-29                                                      ▼
                                      LLM: causes, each with citation(s)
                                                                 │  server-side check: the cited
                                                                 │  quote must exist in that chunk
                                                                 ▼
                              cited diagnosis, or an explicit "no answer"
```

The same 30 questions score every stage (AIENG-33), so each step is judged on a number.

## What already exists

The `Retriever.retrieve(query, k, filters)` interface with a required tenant (W03). Every new stage
implements it, so the benchmark, the API and the later graph/multimodal/permission work all see one
shape. The eval harness (`evaluate_retriever`), the metrics (Recall/Precision/Hit/MRR against
evidence spans), the in-memory and pgvector dense retrievers, chunking configs with provenance,
the LLM provider layer (strict structured output, bounded retry), prompt versioning, `llm_runs`
recording, idempotency keys, the bearer token and the rate limit on the LLM endpoints. 387 offline tests.

## Decisions made by you

| Decision | Choice | Said |
|---|---|---|
| Where the cross-encoder runs | **Locally first.** If it hurts the local machine, then move it to AWS | "lets try locally if it affect local machine performance then we will push to AWS" |
| AWS for building W04 | Not needed. Build, benchmark and tests run on the laptop with the pgvector container | (answer to your question) |

The fallback ladder if local is too heavy is in [Reranker: local first](#reranker-local-first).

## Decisions made by me (my calls, change any of them)

| Decision | Choice | Why |
|---|---|---|
| BM25 | **Own implementation**, in memory, no new dependency. k1 = 1.2, b = 0.75, fixed now and not tuned | A few hundred chunks. ~80 lines, easy to test exactly. Postgres full-text `ts_rank` is not BM25 (no document-length normalisation) and would make the table misleading. |
| Tokeniser | Lowercase; split on non-alphanumerics; **also keep the joined form of identifiers** (`PAY-1042`, `INDEXER_BATCH_SIZE`, `kafka.errors.NoBrokersAvailable`) next to their parts; small English stop list; no stemming | Identifiers are the case where sparse search should win. Fixed before the run. |
| IDF | Computed over the whole tenant corpus once; filters restrict candidates after scoring, not the statistics | A service filter must not change what a word is worth. |
| RRF | `score = Σ 1 / (60 + rank)`, equal weights, each list cut at its top 50, fused by chunk id | k = 60 is the standard value. With 30 questions, tuning k would overfit. A sweep over k is reported as exploratory only. |
| Chunk configs compared | **Two decision configs:** `fixed-2000-200` (adopted) and `section-1500` + context header (the runner-up). **One exploratory:** plain `section-1500`, outside the selection rule | W03 left Recall@5 at 0.983 on the first, so gains cannot show there. ROADMAP risk 21 asks for the second. Plain `section-1500` is where W03 saw the BM25-shaped misses (q22). |
| How the comparison runs | In memory on both configs (cached embeddings, like the chunking experiment); the **database path is checked to rank the same** on the adopted config | One config at a time lives in Postgres. W03 already proved memory and pgvector agree; repeat that test for the hybrid. |
| Reranker model | `cross-encoder/ms-marco-MiniLM-L-6-v2` (22M parameters, about 90 MB) | The standard small cross-encoder, fast on CPU. A bigger model is a W11 question. |
| Reranker input | Context header (`title > section`) plus chunk text, cut to the model's 512-token limit; the number of cut candidates is reported | The header helps the dense model; the same should hold here. |
| Candidates reranked | Top **20** of the hybrid list, return the top 10 | Depth 10 and 50 are reported as exploratory (latency vs quality). |
| Runtime for the reranker | Try the lighter ONNX route first; fall back to `sentence-transformers` (torch) if it does not work. Chosen by measurement: install size, load time, memory, ms per query | Torch is about 2 GB and would bloat Docker and the 1 GB Lightsail box. Install as an **optional extra** (`rerank`) so CI and the default image do not carry it. |
| Tests | All offline. A fake reranker and fake embedder; the real model only in an opt-in test | Keeps CI fast and key-free, as in W03. |
| Service filter in diagnosis | **Off** by default | W03: within noise (H5). A mis-recorded service would hide the right runbook. Revisit with graph context (W05) and permissions (W10). |
| New eval questions | **None in W04** | Eval sets are committed before tuning, and the roadmap puts harder questions in W11 (AIENG-64). The ceiling effect is reported as a finding, not fixed by adding questions mid-week. |
| Tag | `v0.1` is created only when you say so, on the merge commit if possible; never pushed without you | Your rule: no push without asking. |
| Jira | Not touched unless you ask | Outward-facing. |

## Reranker: local first

Questions the AIENG-31 ticket must answer in numbers: does the reranker earn its latency?

Measured on this machine (Apple M3 Pro, 12 cores, 36 GB), for 20 candidates:

- load time (cold start), peak memory added, install size;
- ms per query: mean, p50, p95;
- CPU while running the 30-question benchmark.

**Local is "too heavy" if any of these hold** (fixed now): p95 added latency above **300 ms**, or
memory added above **500 MB**, or the machine is visibly slowed while it runs. Then, in order:
(1) smaller depth (10) and an int8 ONNX model, re-measured; (2) a hosted reranker API, with its
price per 1,000 queries in the table; (3) running it on AWS.

Be aware before choosing (3): a 1 GB Lightsail `micro` cannot hold a torch-based model, and the
instance that could costs more than the £20/month budget allows. That is why the lighter runtime and
the hosted option come first. The model file is downloaded from Hugging Face once (about 90 MB); I
will ask you before the first download.

## Hypotheses, written before any code or number

If these are wrong, that is a finding, not a failure. They follow the failure modes in the
[W03 findings](../evals/retrieval/runs/2026-10-16-dense-baseline-findings.md).

- **H7.** BM25 alone is worse than dense overall (MRR), worst on `paraphrase`, and **no better than
  dense on `identifier`** (W03 refuted "dense is weak on identifiers"; BM25 should tie there, not win).
- **H8.** BM25 repairs the "rare content word" miss: on plain `section-1500` (exploratory run) q22's
  "keepalive" sentence is in BM25's top 5 though dense misses it. On `section-1500` + header dense
  already finds q22, so there hybrid is not expected to gain on q22; it must simply never score
  lower than dense on Recall@5.
- **H9.** Hybrid is at least as good as the better single retriever, within 0.02 MRR, on both
  configs. On `fixed-2000-200` the gain is invisible (ceiling). Hybrid does **not** fix q25 and q29
  (second source shares no words with the question).
- **H10.** The reranker improves MRR on `section-1500` + header by **at least 0.02** by fixing
  "right topic, wrong passage" cases. Known examples from W03: q06 (mention beats definition), q05
  (same-document near-tie), q01 (neighbouring runbook); which of them still fail on this exact config
  is not known beforehand. It does nothing for q25/q29 (the second source never reaches the
  candidates).
- **H11.** The reranker does **not** help, and may hurt, on `fixed-2000-200`: chunks near 500 tokens
  are cut at the model limit, and a small fact diluted in a big chunk (q08) stays diluted.
- **H12.** The reranker adds under 300 ms p95 for 20 candidates on this machine (the "too heavy"
  bar above), so the local-first plan holds.

## How the stack is chosen (fixed before the experiment)

Stages in order: Dense, BM25, Hybrid, Hybrid + rerank. For each chunking config separately:

1. A stage is **adopted over the one before it** if MRR rises by at least **0.02** on at least one
   config, MRR falls by no more than **0.02** on the other, and p95 latency stays inside the budget
   below.
2. Otherwise the **simpler stage wins**. A tie goes to the simpler stage.
3. Latency budget: end-to-end p95 of the whole stack under **1,000 ms** (the dense baseline is 285 ms,
   almost all the query embedding call).
4. Every regression is read, not only counted: list the questions that got worse at each stage and
   say why.

Honest caveat, written now: 30 questions make 0.02 MRR smaller than one question's weight, and W03
called differences under 5 points noise. So the table also shows **how many questions moved**, and
the write-up says when a "win" is one question.

## Diagnosis grounded in evidence (AIENG-32)

New endpoint `POST /incidents/{id}/diagnosis`, in the same router, behind the same bearer token,
rate limit and `Idempotency-Key` as the other two LLM endpoints, recorded in `llm_runs`
(operation `diagnose_incident`, prompt `diagnose_incident/v1`).

1. Query = incident title + description. Retrieve with the winning stack, tenant-filtered, top 5.
2. Evidence is numbered `E1`…`E5`; the prompt shows each chunk with its source, section and page.
   Retrieved text is **data, not instructions** (full defence is W10).
3. The model returns `causes`, each with a `claim` and `citations` (`evidence_id` + a verbatim
   `quote`), or `answer_status = "insufficient_evidence"`.
4. **The server checks every citation** before anything is shown: the `evidence_id` must be one we
   gave, and the quote must occur in that chunk's text (whitespace ignored). A cause with no valid
   citation is **dropped** and counted in the response. If none survive, the result is `no_answer`.
5. Provenance in the response comes from **our** chunk data (document, title, source path, section,
   page, character span of the quote), never from the model's words.
6. No-answer paths: no chunks retrieved (nothing ingested or tenant empty); the model says the
   evidence is insufficient; every cause fails the citation check. In the first case the model is not
   called.

Not in W04 (known limitation, stated now): a relevance **score threshold** for "nothing relevant was
retrieved". RRF scores are not calibrated, and a threshold needs a set of off-corpus incidents,
which is W11 (AIENG-64). For now an off-corpus incident reaches the model, which must answer
`insufficient_evidence`; this is checked by hand with the real model on two off-corpus incidents.

**Done when (ticket):** tests show a fabricated citation, a quote not in the cited chunk, an unknown
evidence id and an uncited cause are all blocked, and every cause in a response has provenance.

## Day by day

Each ticket is timeboxed to 2 hours.

### AIENG-29 · BM25 sparse retrieval

`Bm25Retriever` behind the `Retriever` interface; tokeniser and parameters as above; index built from
the tenant's stored chunks (with or without the context header, like the dense index). Run on the
30 questions on both configs. **Done when** BM25 metrics and miss examples are recorded beside the
dense baseline.

### AIENG-30 · RRF fusion

`HybridRetriever(dense, sparse)`; a test that the in-memory and pgvector versions rank the same.
**Done when** hybrid metrics are directly comparable to both baselines (same questions, same report
shape) and latency is recorded.

### AIENG-31 · Cross-encoder reranking

`Reranker` interface, `FakeReranker` (offline), `CrossEncoderReranker` (optional extra), and
`RerankingRetriever`. The local-performance measurements above. **Done when** the write-up states
whether the reranker earns its latency on this corpus, using the rule above.

### AIENG-32 · Grounded diagnosis

As described above, with the offline tests and a recorded real-model run on three stored incidents
plus two off-corpus ones.

### AIENG-33 · Comparison, v0.1, October retrospective

One command (`uv run python -m incidentops_ai.retrieval.stack_benchmark`) writes the table for both
configs: Recall@1/5, Precision@5, Hit@1, MRR@10, questions that moved, chars read@5, p50 and p95
latency, API cost per 1,000 queries, extra memory. Plus a hand-written findings file with the
regressions explained and each hypothesis H7 to H12 confirmed or refuted. Then: set the default stack
in the API, update `ROADMAP.md` ("Current state") and the README, write the W04 note and the
**October retrospective** (`docs/notes/retro-october-2026.md`). Tag `v0.1` when you say so.

## Risks

1. **Ceiling effect.** The adopted config is at Recall@5 0.983, so hybrid and rerank may look identical
   there. The `section-1500` + header comparison is the safeguard; report both, never only the good one.
2. **Small, one-author eval set.** 0.02 MRR is below the noise. Mitigation: show counts of questions
   moved, name the single questions behind any difference, expand the set in W11.
3. **Domain shift.** The reranker was trained on web search, not runbooks. It may rank a vague
   postmortem above a precise runbook line. The regression list is how this would show.
4. **Truncation.** A 512-token limit cuts the end of 2,000-character chunks, which may remove the
   evidence it is meant to rank (H11). Reported as a count.
5. **Tuning on the test set.** BM25 parameters, RRF k and rerank depth are fixed in this document;
   sweeps are labelled exploratory and cannot change the choice.
6. **Heavy dependency.** Torch in the default image would bloat Docker and CI. Optional extra, and the
   default image never loads the model unless the reranker is switched on.
7. **Stale BM25 index.** The API holds the index in memory; a re-ingest must not leave it serving old
   chunks. A cheap fingerprint check (chunk count and latest ingest) per request, or rebuild on change.
8. **A diagnosis that sounds right.** The model can quote a real sentence and still draw the wrong
   conclusion from it. The citation check proves the quote exists, not that the claim follows. The
   findings file says so; claim-level checking is W11 (generation evals).
9. **Cost of the real runs.** Embeddings for both configs are cached from W03; the new real-model
   spend is the query embeddings (30 per run, fractions of a cent) and the diagnosis checks (five
   calls).

## Carried into W05

The `Retriever` interface with the hybrid + rerank stack behind it, the stack table, and a diagnosis
endpoint that takes its evidence from any retriever. The graph (W05) adds a retriever behind the
same interface; the table gets a row, and q25/q29 (second source shares no words) are its first
test.
