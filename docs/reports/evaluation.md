# Evaluation report (v1.0)

What IncidentOps gets right and wrong, layer by layer. Every number links to a committed result
and names the command that produces it. **Read the limits first:** every set here is small (3 to
30 cases) and was written by the assistant that also built the system (W05 to W13 were built
by an AI assistant at the user's request). The numbers show the system
behaving as designed on cases chosen to probe the design; they are not accuracy rates on real
incidents.

Prices are an assumption ($2.50 per million input tokens, $10 per million output, the figure used
since W06), not a bill. Model: `gpt-6-luna` throughout.

## Summary

| Layer | Dataset | Result | Committed result |
|---|---|---|---|
| Extraction | 10 samples | 10/10 valid typed results; service right 10/10 | [extraction v1](../../evals/extraction/runs/2026-10-13-extract_incident-v1.md) |
| Retrieval (hybrid) | 30 questions | Recall@5 1.000, MRR@10 0.967 | [stack comparison](../../evals/retrieval/runs/2026-10-16-stack-comparison.md) |
| Retrieval (BM25, CI) | same 30 | Recall@5 0.983, MRR@10 0.957 | [dashboard](../../evals/dashboard.md) |
| Generation | 16 golden incidents | answering 15/16; citation precision 1.000; context recall 1.000 | [W11 findings](../../evals/generation/runs/2026-10-17-w11-findings.md) |
| Judge (faithfulness) | 13 labelled claims | agreement 77%, kappa 0.45: **below the bar, reported not gated** | [W11 findings](../../evals/generation/runs/2026-10-17-w11-findings.md) |
| Graph | 12 structural questions | answered by the graph, not by retrieval (H17) | [graph findings](../../evals/graph/runs/2026-10-17-graph-findings.md) |
| Attachments | 14 assets | the router chose the best extractor for 9/14 (H23 refuted) | [multimodal findings](../../evals/multimodal/runs/2026-10-17-multimodal-findings.md) |
| Workflow (scripted) | 12 cases | 12/12 on the expected route (CI) | [workflow findings](../../evals/workflow/runs/2026-10-17-workflow-findings.md) |
| Workflow (real) | same 12 | task success 11/12 | [workflow benchmark](../../evals/workflow/runs/2026-10-17-workflow-benchmark.md) |
| Memory | 3 unanswerable incidents | own note cited 3/3; another service's or tenant's 0/3 | [memory check](../../evals/memory/runs/2026-10-17-memory-check.md) |
| Text-to-SQL | 15 questions, 8 malicious | 14/15 correct; 8/8 malicious declined | [text-to-SQL run](../../evals/sql/runs/2026-10-17-text-to-sql.md) |

Security results (attacks, tenancy, injection) are in [security.md](security.md).

## Extraction (W02)

**Dataset:** `evals/extraction/samples.jsonl`, 10 incident reports, including a vague one, one with
no service, a noisy log and an injection attempt.
**Command:** `uv run python -m incidentops_ai.extraction.run_samples` (10 real calls).

All ten parsed into valid typed results. The two reports with no service returned `null` rather
than a guess. The injection sample (told to answer "OK" at SEV4) came back as a full extraction at
SEV2. **Questionable example:** the payments sample (production, p99 from 400 ms to 9 s) was rated
SEV3 where SEV2 is arguable, and the set has no expected severity to score it against. Severity is
a suggestion for a person to confirm, which is why the `sev1` route reads the *recorded* severity,
not the model's.

## Retrieval (W03, W04)

**Dataset:** `evals/retrieval/questions.jsonl`, 30 questions over the 18-document corpus: 8 lookup,
8 paraphrase, 5 identifier, 5 multi-span, 4 procedure. A chunk is relevant when it holds at least
half of an evidence span.
**Commands:** `uv run python -m incidentops_ai.retrieval.stack_benchmark` (embeds the questions:
fractions of a cent); `uv run python -m incidentops_ai.evals.gate` measures BM25 offline in CI.

| Stack | Recall@5 | MRR@10 | p95 latency |
|---|---|---|---|
| dense | 0.983 | 0.961 | 771 ms |
| bm25 | 0.983 | 0.957 | 0 ms |
| **hybrid (default)** | **1.000** | **0.967** | 248 ms |
| hybrid + reranker | 1.000 | 0.961 | 895 ms, +1.3 GB memory |

Hybrid won by the rule fixed before the run; the reranker was refuted on gain (H10) and cost (H12).
**Failure examples:** q24 drops in rank under hybrid against dense; q08 needs whole-document
chunks (2,000 characters) to be found, which is why the chunking is `fixed-2000-200`; on a corpus
this small, large chunks are nearly whole documents, so the chunk-size result may not hold on a
bigger corpus.

## Generation (W04, W11)

**Dataset:** `evals/generation/golden.jsonl`, 16 incidents: on-corpus, off-corpus, vague and
misleading. `evals/generation/labels.jsonl`: 13 claims labelled faithful or not by the assistant, not by the user.
**Commands:** `uv run python -m incidentops_ai.evals.generation generate` (16 real calls,
about $0.15), `… judge` (about $0.04 per pass), `… calibrate` (free).

| Metric | Value | Gate |
|---|---|---|
| Answering accuracy (answers when it should, declines when it should) | 15/16 (0.938) | ≥ 0.90 |
| Context recall | 1.000 | ≥ 0.95 |
| Citation precision (every citation points at real evidence holding the quoted words) | 1.000 | ≥ 0.95 |
| Context precision | 0.45 | reported |
| Judge faithfulness | 0.615 | reported |

**Failure example:** the payments incident declined to answer without its attachments; with the
alert and the chart (the W07 run) it answered; the text alone was not enough, which is the right
behaviour for the text it had. All three off-corpus incidents and the vague one were declined; both
misleading incidents were answered from the right service's documents, not the one they named;
the poisoned runbook was ignored.

**The judge (H46, refuted):** v1 agreed with my labels 31% of the time (kappa 0.06). v2's rubric
reached 62%, and 77% (kappa 0.45) once the judge also saw the incident's service. The bar was 80%
and kappa 0.6, so faithfulness is **reported, never gated**. The labels are the assistant's; a second, human labeller is the next step.

## Graph (W05)

**Dataset:** `evals/graph/questions.jsonl`, 12 questions about ownership, dependencies and impact;
`data/graph/catalogue.toml`. **Command:** `uv run python -m incidentops_ai.graph.benchmark` (needs
Neo4j; free).

The graph answers structural questions the documents cannot (H17: only two ownership facts appear
in the text). Steering retrieval with it did not help on the original 30 questions (0.967 →
0.967), and a hard service filter lost every dependency question (MRR 0.000), so the graph is
context beside the diagnosis, not a retrieval filter.

## Attachments (W06)

**Dataset:** `evals/multimodal/cases.jsonl`, 14 assets (screenshots, charts, diagrams, text and
scanned PDFs) and 28 questions about them. **Command:**
`uv run --extra ocr python -m incidentops_ai.multimodal.benchmark` (vision-model calls; the W06
run's spend was about $0.36). Correct answers per path:

| Kind (questions) | PDF text layer | OCR | Vision transcript (cited) | Vision, direct (not citable) |
|---|---|---|---|---|
| Screenshots (8) | – | 7 | 8 | 8 |
| Charts (6) | – | 1 | 6 | 6 |
| Diagrams (6) | – | 2 | 6 | 6 |
| Text PDFs (4) | 4 | 4 | 4 | 4 |
| Scanned PDFs (4) | 0 | 4 | 4 | 4 |

**Failure example:** the coverage-based router picked the best extractor for 9 of 14 assets (64%,
H23 refuted, bar 80%). A transcript can also misread faithfully: the citation then quotes the
misreading.

## Workflow (W07, W08)

**Dataset:** `evals/workflow/cases.jsonl`, 12 incidents with an expected route and documents.
**Commands:** `uv run python -m incidentops_ai.workflow.benchmark` (24 real runs, about $0.50);
`… --overhead` (free); the scripted 12/12 runs in CI.

| Measure | Result |
|---|---|
| Behaving (scripted) models on the expected route | 12/12 |
| Real models, task success | 11/12 |
| Crash after diagnosis, resumed | 0 model calls repeated |

**Failure example:** the one failure (w07) and all three route changes between runs have one
cause: the review rule `many_open_questions` fires at four open questions, and the model listed
four in one run and three in the next. The route is deterministic given its inputs, and only as
stable as those inputs. **Another:** for w03 (recorded SEV1) the model suggested SEV2 both times;
the code routed it to review anyway, because `sev1` reads the recorded severity.

## Memory (W08)

**Dataset:** `evals/memory/cases.jsonl`, three incidents the corpus cannot answer.
**Command:** `INCIDENTOPS_DATABASE_URL=<scratch database> uv run python -m incidentops_ai.memory.benchmark`
(18 real calls, about $0.15).

With a reviewer's note for the same service and tenant, all three were answered citing `M1`; the
same note stored for another service or tenant was never recalled. **Limit:** a recommendation
can then rest on one reviewer's note.

## Text-to-SQL (W09)

**Dataset:** `evals/sql/questions.jsonl` (15, with reference SQL), `evals/sql/malicious.jsonl` (8).
**Command:** `uv run python -m incidentops_ai.ops.benchmark` (23 real calls, about $0.07).

14 of 15 correct. **Failure example:** s08 ("how many alerts did each service fire in October?")
`LEFT JOIN`ed from services and listed one service with zero alerts: arguably better, but the
pre-registered rule compares row counts, so it is scored wrong. All 8 malicious questions were
declined by the model, which shows the prompt layer, not the validator; the validator's evidence
is the attack set in [security.md](security.md).

## Reproducing all of it

The offline layers and the recorded generation run are checked together by the CI gate:

```bash
INCIDENTOPS_DATABASE_URL=<migrated database with the corpus ingested> \
  uv run python -m incidentops_ai.evals.gate        # writes evals/dashboard.md; exits non-zero on a regression
```

A prompt change fails the gate until `generate` is re-run and the baseline refreshed
(`--refresh-baseline`), so a recorded generation number always belongs to the prompt in the tree.
