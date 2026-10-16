# Week 3 write-up: retrieval foundations (AIENG-24 to AIENG-28)

Status on 16 Oct 2026: **complete**. Everything was built and tested offline first; the chunking
choice and the dense baseline were then run on real embeddings (a first attempt had stopped on an
expired OpenAI key, replaced the same day). This note records what was built, every decision with
why and why not, what the offline checks did and did not show, and what the real run found. The
plan and hypotheses are in [../roadmap-w03.md](../roadmap-w03.md); the concepts are in
[retrieval-mental-model.md](retrieval-mental-model.md); the full analysis is in
[the findings](../../evals/retrieval/runs/2026-10-16-dense-baseline-findings.md).

## What exists now

```
data/corpus/            18 synthetic documents: 11 runbooks (one paged text export), 7 postmortems
evals/retrieval/        questions.jsonl: 30 questions, 42 evidence spans
src/incidentops_ai/retrieval/
  documents.py          parse front matter, clean, pages (form feeds), heading paths
  chunking.py           Chunk (exact slice + provenance); fixed, paragraph and section chunkers
  ingest.py             store documents + chunks, repeatable, one transaction
  embeddings.py         Embedder interface: OpenAI (own retry), offline fake, on-disk cache
  retriever.py          Retriever interface, RetrievalFilter (tenant required)
  memory.py, store.py   in-memory and pgvector dense retrievers; index_chunks
  evalset.py, metrics.py, evaluate.py   quotes -> spans, Recall/Precision/Hit/MRR, the scoring loop
  experiment.py, chunking_experiment.py   AIENG-26 runner and its fixed selection rule
  benchmark.py          AIENG-28 runner: report + per-question JSON
```

387 tests, all offline, against real Postgres with pgvector.

## Decisions, with why and why not

| Decision | Why | Why not the alternative |
|---|---|---|
| **Ground truth is an exact quote**, resolved to a character span | The same 30 questions judge any chunking, model or retriever | Chunk ids change with every chunking; document-level labels cannot tell a good chunk from a whole-document dump |
| A chunk is **relevant if it holds at least half** of a quote | Finding half the answer is not finding it | Any overlap rewards a chunk that touches one word; full containment punishes every chunker that cuts through the quote |
| Quotes must match **exactly once**, ignoring line wrapping | A test fails if the corpus and the questions drift apart | Matching by search score would hide a rotten label |
| **pgvector** in the existing Postgres | Tenant and service filters are a WHERE clause in the same query as the ranking | A separate vector database means two stores to keep consistent and filters applied after search |
| **No vector index** yet | Exact scan over hundreds of chunks is faster than HNSW and never misses | An approximate index adds a recall error we would then have to measure |
| **Tenant is required** in every retrieval | Permission filtering (W10) becomes a parameter that already exists | Adding it later touches every retriever and every test |
| Chunk id is a **uuid5 of tenant, document, chunker, position** | Stable across runs, so citations and embeddings stay attached | Random ids change on every re-ingest |
| Chunks are **exact slices** of the cleaned text | A citation can always be traced to characters | Chunking after rewriting text would lose the link to the source |
| Cleaning is **whitespace only** (no unwrapping of lines) | Offsets stay simple; code blocks and tables are not damaged | Joining wrapped lines breaks lists and shell commands |
| Pages are **form feeds**, headings are **markdown headings** | Matches what PDF text extractors emit (W06) and what runbooks use | A custom page marker would not survive a real PDF |
| Store the **model and the context setting** with each vector | Vectors from different models are not comparable; mixing them is a silent wrong answer | Trusting "the config probably did not change" |
| One vector size, **1536** | `text-embedding-3` models can be asked for it, so changing model does not change the schema | A column per model, or resizing in the query |
| Embedder has **its own bounded retry**, SDK retries off | Same reason as W02: visible and countable | Hidden SDK retries and a 10-minute default timeout |
| Embeddings **cached on disk by text hash**, git-ignored | Re-running an experiment costs nothing and gives identical vectors | Committing 30 MB of vectors, or paying for each run |
| The **selection rule was written before the run** | Otherwise the winner is chosen after seeing the table | "Pick whichever looks best" |
| Ablations (context header, service filter) are **outside the rule** | More runs mean more chances to find a "winner" by luck | Folding them into the decision |
| Ingestion is **one transaction**; prune is opt-in | A failure leaves the previous state; deleting documents is deliberate | Deleting by default, or committing per document |
| Offline runs carry a **banner** and are not committed | Hashed words are not a language model | Presenting plumbing checks as results |

## What the offline checks showed

The fake embedder (hashed words) is for testing wiring, not for judging retrieval. One thing was
still worth noticing: on the five pre-registered configs it ranked the 2,000-character chunking
first on MRR and Recall, simply because a 2,000-character chunk is close to a whole document in this
corpus. That is a property of the metric and the corpus, not of the fake. So the report now shows
**Chars read@5** beside the quality columns, the write-up must say so if a very large chunk wins, and
the rule was left as written. The real run then selected the same config for the same reason.

The pgvector retriever and the in-memory one rank identically on all 30 questions, with and without
the service filter (within float32 rounding), so benchmarks run on either mean the same thing.

## What went wrong on the way (and was fixed)

- Three of my own tests had wrong assumptions, found by running them before the code existed or
  right after: a paragraph in the test data is 61 characters, not 70; a chunk that starts on page 2
  and ends on page 3 does not make page 3 a "start page"; and a chunk that starts at a title line
  reports the title's section, not a deeper one. All three were test errors and the tests were
  corrected; none changed the behaviour I had specified. (Separately, the section chunker labels a
  chunk that joined a bare title line by its main section, by design, with its own test.)
- Local Postgres is Homebrew 14 and cannot load pgvector. Tests run against a
  `pgvector/pgvector:pg17` container; CI and compose switched to the same image.
- The OpenAI key expired (401 `expired_secret_key`) at the first real run. Everything after it was
  built and tested offline, nothing was written into `evals/retrieval/runs/`, and the real runs
  followed once the key was replaced.

## Results (real embeddings, `text-embedding-3-small`)

| | |
|---|---|
| Chunking chosen by the pre-registered rule | `fixed-2000-200`: MRR 0.961, Recall@5 0.983, next best MRR 0.801 |
| Embedding model | `-small` kept: `-large` gained 0 points of Recall@5 on that config |
| Dense baseline on pgvector | Recall@1/3/5/10 0.800 / 0.983 / 0.983 / 1.000, MRR@10 0.961, one question below full Recall@5, mean query 210 ms |
| Hypotheses | H1 partly, H2 half, H3 refuted, H4 refuted, H5 directionally yes, H6 confirmed |
| Cost of the choice | 7,433 characters read for the top five, against 1,961 for `section-1500` |

What to remember:

- The chosen chunk is nearly a whole document in this corpus, so the win says "documents are easy
  to find", not "2,000 characters is the right size". Use `section-1500` with the context header
  (MRR 0.889, 2,016 characters) once documents get long.
- The failures that remain are about sources and passages, not scores (ten inspected cases in the
  findings): a second source with no shared words, one document crowding out another, a definition
  ranked below its usages, a small fact diluted in a large chunk.
- The benchmark is near its ceiling on the chosen config, so W04 may not show gains there.

## Still open

1. CI has not run on the pgvector image yet.
2. The AIENG tickets (24 to 28) are not updated in Jira.
3. The eval set needs harder, multi-source questions (W11, AIENG-64); q24 and q25 are the template.

## Hand-off to W04

The `Retriever` interface, the eval harness and the benchmark runner are what W04 plugs into: BM25,
RRF fusion and a cross-encoder arrive as more retrievers, and are scored on the same 30 questions
with the same report. Identifier questions (error codes, commands) are where dense retrieval is
expected to be weakest (H4), so they are the first thing to look at in the baseline.
