# The incident workflow: findings (AIENG-44 to AIENG-48)

Run on 17 Oct 2026 against a scratch database (dropped afterwards): the 18-document corpus ingested
and indexed (23 chunks, `text-embedding-3-small`), retrieval stack `hybrid-rrf`, model `gpt-6-luna`
for extraction, diagnosis and the two image transcripts, the service graph from
`data/graph/catalogue.toml` in memory (the same data the Neo4j store loads; W05 tests both agree).
Evidence: [the report](2026-10-17-workflow-benchmark.md) and its
[per-run data with step logs](2026-10-17-workflow-benchmark.json). Design, cases, routing rule and
hypotheses were committed before any workflow code, and how each hypothesis is judged before the real
run: [roadmap-w07.md](../../../docs/roadmap-w07.md).

## The hypotheses

| | Prediction | Result | Verdict |
|---|---|---|---|
| **H24** | Behaving models: every case on its expected route | 12/12 (a test: scripted model, BM25 over the corpus, catalogue graph) | holds |
| **H25** | Real models: task success ≥ 10/12 | **11/12** with the concurrent layout (10/12 sequential) | holds |
| **H26** | Concurrent ≥ 30% faster, same routes and citations | median **6.52 s against 9.85 s (−34%)**; same route and cited documents in **8/12** | speed holds; "same results" holds for the layout, not for two real runs (below) |
| **H27** | A crash after diagnosis resumes without repeating a model call | 2 calls before the crash, **0 on resume** (and a test that resumes in a new process) | holds |
| **H28** | LangGraph's own overhead < 50 ms per run | **+6.4 ms** median without a checkpointer; +37.7 ms with PostgreSQL checkpoints | holds |

Task success = the expected route and, for a recommendation, at least one expected document cited.

## Where the time goes

| Step | Range over 24 runs |
|---|---|
| extract (model) | 4.6 to 9.2 s |
| diagnose (model) | 1.1 to 8.2 s |
| retrieve (hybrid, one embedding call) | 0.16 to 1.0 s |
| everything else | a few ms each |

Extraction was the slower model call in 22 of 24 runs. So the concurrent layout costs roughly
`retrieve + max(extract, diagnose)` and the sequential one `extract + retrieve + diagnose`; the saving
is about one diagnosis per run. Extraction waits for retrieval it does not need (LangGraph's
super-steps, see the roadmap), which costs about a quarter of a second.

## What the failures and the disagreements were

- **The one failure (w07, concurrent) and all three route flips between layouts (w01, w07, w08) have
  the same cause:** the W02 review rule `many_open_questions` fires at four open questions, and the
  model listed four in one run and three in the other, for the same incident. The layout did not
  change anything: with scripted models both layouts produce identical state (a test), and the step
  logs show the extraction differing. A count of items a model chose to list is a noisy input to a
  threshold. The route is deterministic *given its inputs*; it is only as stable as those inputs.
- **w04: same route, different evidence.** One diagnosis cited the postmortem, the other the runbook.
- **Two of six recommendations name no runbook** (w02, w04 concurrent): the diagnosis cited only
  postmortems, so the recommendation is the causes plus the on-call engineer. Task success counts any
  expected document; "a runbook was cited" would score 4 of 6.
- **The code caught a severity the model missed.** For w03, recorded SEV1, the extraction suggested
  SEV2 in both runs; the route went to review because `sev1` is read from the recorded severity. For
  w06 (SEV1) one extraction suggested SEV1 and one SEV2.
- **The vague case (w12) went to review for a different reason than I designed.** I expected the
  extraction to have no service. The recorded service is passed to the extraction, so the model always
  named it; the case was routed by `no_answer` and `many_open_questions` instead.
- Off-corpus cases (w10, w11) were answered `insufficient_evidence` every time.

## Two faults found while testing the crash path

- **A failing branch cancels its sibling mid-write.** When `diagnose` raises, LangGraph cancels
  `extract`, which shares its super-step. If the cancellation lands inside a database write, asyncpg
  closes that connection; the test fixture's engine had no pre-ping, so the next session got the dead
  connection and the run could not be marked `failed`. The app's engine already pre-pings; the tests
  now use the same engine, and a test pins the behaviour.
- **The cancelled step's model call is not recorded.** In the same scenario the extraction call had
  returned (and would have been paid for), but its `llm_runs` row was rolled back with the cancelled
  write. Spend and audit can under-count a call when a sibling fails. Left for W08's fault injection
  (AIENG-53), where budgets (AIENG-51) will depend on these rows.

## Overhead (scripted models, 30 interleaved runs per variant, same database work in each)

| Variant | Median | p95 | Over the plain function |
|---|---|---|---|
| plain async function | 9.9 ms | 11.9 ms | – |
| graph, no checkpointer | 16.3 ms | 18.5 ms | +6.4 ms |
| graph, in-memory checkpoints | 16.4 ms | 18.2 ms | +6.5 ms |
| graph, PostgreSQL checkpoints | 47.6 ms | 56.2 ms | +37.7 ms |

Against runs of 5 to 16 seconds, the framework and its checkpoints cost under 1%.

## Cost

48 model calls in the comparison (96,466 input, 21,556 output tokens): about **$0.46** at the assumed
$2.50 / $10 per million tokens. With the two image transcripts at upload, the crash-and-resume run and
the corpus embedding, the week's real calls come to about **$0.50** (an estimate; the provider does
not publish this model's price to us).

## Decision

[ADR-004](../../../docs/adr/004-agentic-vs-deterministic-steps.md): a fixed graph with two model
steps, the route in code, LangGraph for state, checkpoints and (W08) interrupts.

## Limits

Twelve cases, one author, two runs per case. The run-to-run variance is seen once per case; a proper
estimate needs repeated runs of the same layout (W09's evaluation work). Retrieval in H24 was BM25, not
the hybrid stack the real run used.
