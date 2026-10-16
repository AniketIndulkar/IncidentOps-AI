# October 2026 retrospective (W01 to W04, v0.1)

A draft written from the repository's own record: the roadmap, the week notes, the findings and the
commit history. The personal parts (how long things actually took you, what the reading changed in
how you think, what you would like to be different) are yours to add under "Your notes" at the end.

## What shipped

| Week | Exit criterion | State on 16 Oct |
|---|---|---|
| W01 Production Python and FastAPI | Deployed API with tests, persistence, duplicate-request protection | Done: typed API, PostgreSQL, idempotency, Docker, CI, AWS deploy guide |
| W02 LLM application engineering | Typed extraction that fails safely on malformed output and timeouts | Done: provider layer, strict extraction, fault injection, one tool, ADR-001 |
| W03 Retrieval foundations | Ingestion, metadata, chunking experiments, reproducible benchmark | Done: 18-document corpus, 30-question eval set, pgvector, dense baseline |
| W04 Hybrid, RRF, reranking | Dense vs BM25 vs hybrid vs hybrid+rerank, with grounded diagnosis | Done: hybrid default, reranker measured, cited diagnosis endpoint. **v0.1 not tagged yet** |

By the roadmap's own milestone, v0.1 is "evidence-backed hybrid retrieval plus grounded, cited
diagnosis". Both exist and are measured; the tag is the only thing left, and it is yours to say.

Numbers: 487 offline tests; 30 retrieval questions with 42 evidence spans; 10 extraction samples.
Write-ups: ADR-001, the LLM failure matrix, the dense-baseline findings and the hybrid findings.

## What held up

- **Hypotheses and selection rules before code.** Used in W03 and W04. Of the twelve hypotheses, four were
  refuted outright (H3, H4, H10, H12) and four held only in part (H1, H2, H7, H9). Each of those is a
  result a post-hoc story would have hidden: "large chunks hurt" (no), "identifiers are the dense weak
  spot" (no), "the reranker lifts the section config" (no), "the reranker is under 300 ms" (no).
- **Offline-first.** No test needs a key or a model; real runs are small, explicit and recorded. The
  fake embedder, fake reranker and scripted provider found real bugs in my own code before any API call.
- **Ground truth as a quote, not a chunk id.** One eval set has now judged six chunkings, two embedding
  models and four retrieval stages without being rewritten.
- **Reading the failures, not the averages.** The averages say "hybrid +0.029"; the questions-moved list
  says "one question net". Both belong in the write-up.
- **Putting limits next to results.** Every findings file ends with what it cannot show.

## What did not go to plan

- **The calendar.** W02, W03 and W04 were each built well ahead of their weeks (W03 and W04 on 16 Oct,
  for 19 to 23 Oct and 26 to 30 Oct). That leaves slack, but the "2 hours a ticket, one ticket a day"
  rule and the Friday measure-and-write cadence were not what actually happened; the roadmap still
  describes them. Decide whether the rule or the practice should change.
- **Jira is behind the repository.** AIENG-5 and 29 to 33 are still To Do, and the roadmap notes the
  W03 tickets were never updated either. The repository is the source of truth right now, not Jira.
- **CI has not run on the new Postgres image** (`pgvector/pgvector:pg17`, risk 19). Local tests use the same
  image, so the risk is small, but it is unverified since W03.
- **The eval set is the weak link.** 30 questions by one author: one question is 3.3 points, and most of
  this month's differences are one or two questions. The hardest ones (q24, q25, q29) fail in every
  stage for the same reason (a second source), which no amount of re-ranking fixes.
- **A reranker that did not earn its place.** Worth building, because now the answer is a measurement:
  not on this corpus, not locally at this size. It will matter again with long documents (W06).
- **Some of my own mistakes.** Several tests with the wrong expectation, an equality test
  that did not allow for float32 ties, and a cache that would have hidden latency. All were caught by
  running things before trusting them.

## What to change for November

1. **Grow the golden set before more tuning** (AIENG-64 is W11; consider pulling a first slice forward):
   add multi-source questions, off-corpus incidents with a "no answer" label, and a handful of
   paraphrases of the identifier questions. Everything else is measured against it.
2. **Try BM25 stemming and a relevance gate** on that bigger set; both were deliberately left out of v0.1.
3. **Let the graph take the source-level failures** (W05): q24, q25 and q29 are its first test, and the
   table gets a row.
4. **Decide the cadence honestly:** keep "2 h a day" and slow down, or write the month as it is being
   done (built early, measured on Friday).
5. **Update Jira and run CI** on the current branch before building on it.
6. **Revisit chunk size when documents get long** (W06 PDFs): `section-1500`+header is the runner-up,
   and the reranker's case is strongest where chunks cannot carry a header.

## Open items

- Tag `v0.1`. Suggested: tag the merge commit on `main`, after the branch is reviewed.
- Open the pull request for `feat/w04-hybrid-rerank-diagnosis` (it also contains the W03 commits until
  that branch is merged).
- Update the AIENG tickets: AIENG-24 to 28 (W03) and AIENG-29 to 33 (W04), AIENG-4 and AIENG-5.
- Risks 17 and 21 in the roadmap are updated; risk 19 (CI) is not.

## Your notes

(What took longer than it looked, what you read that changed the design, what you want November to feel like.)
