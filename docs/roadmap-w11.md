# W11 roadmap: evaluation layers, a model gateway and tracing (14–18 Dec 2026)

Source: Jira epic **AIENG-12** and tasks **AIENG-64 to AIENG-68**, read 17 Oct 2026. Overall plan:
[ROADMAP.md](ROADMAP.md). Built early, stacked on W10 (branch `feat/w11-evals-gateway-observability`).

> **Status, 17 Oct 2026: built and measured.** Design written before any W11 code. Decisions are
> mine (W05 to W13 built back to back at the user's request), except tracing, which the user chose:
> an interface, a local file exporter, and a Langfuse adapter tested against a fake.
>
> **Result:** H45, H47, H48 and H49 hold; **H46 is refuted**: the judge agreed with the labels on
> 31% of faithfulness verdicts at first and 77% after a clearer rubric and a harness fix (kappa
> 0.45, in-sample), so it is reported, not gated. Findings:
> [w11-findings](../evals/generation/runs/2026-10-17-w11-findings.md); decision:
> [ADR-008](adr/008-evaluation-gateway-and-tracing.md).

## Goal of the week

Every week so far measured itself once, by hand. This week: one evaluation per layer with a
purpose and a threshold, an LLM judge whose verdicts are checked against labels before anyone
trusts them, one place where every model call is routed (with fallbacks that have been seen to
work), a trace from which any run can be rebuilt, and CI that blocks a regression.

**Exit criterion (from the epic):** regression evals, gateway routing and fallbacks, tracing, and
latency, token and cost dashboards.

## AIENG-64: four layers, each with a purpose

| Layer | Question it answers | Dataset | Metrics | Gate (CI) |
|---|---|---|---|---|
| **Retrieval** | Does the right document come back? | `evals/retrieval/questions.jsonl` (30, W03) | Recall@5, MRR@10 (BM25, offline) | MRR@10 ≥ 0.93, Recall@5 ≥ 0.95 |
| **Generation** | Is the answer supported, relevant, and silent when it should be? | `evals/generation/golden.jsonl` (16, new: answerable, off-corpus, misleading, vague, poisoned) | no-answer accuracy, context recall and precision, citation precision (deterministic); faithfulness and relevance (LLM judge, calibrated) | real-model run on prompt or model change (below) |
| **Workflow** | Does each case take its route, and does each fault end safely? | `evals/workflow/cases.jsonl` (12), fault scenarios (5) | routes with behaving models (12/12), faults ending safely (5/5) | both must hold |
| **System** | Is it fast, cheap and up? | traces and `llm_runs` | p50/p95 latency per step, tokens and cost per run and model, error rate | reported in the dashboard, not gated (no production traffic yet) |
| *Security* (from W10) | Do the controls hold? | `evals/sql/attacks.jsonl`, `evals/security/attacks.jsonl`, a PII set | validator 20/20, detector recall on the attack set, PII recall and false positives | all must hold |

**Representative failures added to the golden set:** off-corpus incidents (must not answer), an
incident whose symptoms point at a different service than the one named (misleading), a vague one,
and one whose retrieved runbook is poisoned.

## AIENG-65: RAGAS-style metrics, calibrated

The metrics follow RAGAS's definitions; the code is ours:

- **Faithfulness**: for each shown cause, is the claim supported by the evidence it quotes? (judge)
- **Answer relevance**: does the claim address this incident's symptoms? (judge)
- **Context precision**: the share of the retrieved chunks that come from a relevant document.
- **Context recall**: whether at least one relevant document was retrieved.

**Why not install RAGAS or DeepEval.** Both bring large dependency trees and their own model
calls with prompts we would not control or version; their scores would change when they update.
A judge prompt in our own package (`prompts/judge_claim/v1`) is versioned like every other prompt
and its outputs are recorded.

**Calibration.** A subset of judged causes is labelled by reading the claim against its quote.
**The labels are written by me (the assistant building this), not by the user**; the report says
so, and the user re-labelling them is the obvious next step. Judge agreement is reported as
accuracy and Cohen's kappa, with every disagreement listed. The judge's scores are reported as
signals with that agreement next to them.

## AIENG-66: one gateway for every model call

`llm/gateway.py`, over the existing `LLMProvider` interface (so the guard, recording and strict
structured output keep working):

- **Per-task routes**: `extraction`, `diagnosis`, `transcription`, `text_to_sql`, `judge`, each an
  ordered list of models (`INCIDENTOPS_LLM_ROUTES`, JSON); the default route is the configured model.
- **Fallback** to the next model on a retryable failure (timeout, rate limit, 5xx, connection),
  **never** on invalid output or a refusal (another model would only hide it) or a client error.
- **A circuit breaker** per model: after 3 consecutive retryable failures, the model is skipped for
  30 s; then one trial call decides.
- **A rate limit** per model (calls per minute) in front of the provider's own retries.
- The model that answered is recorded in the run metadata, so a fallback is visible.

**Why not LiteLLM.** It is a second provider abstraction beside ours, and our strict structured
outputs, recorded attempts and the guard are built on our interface. The routing, fallback and
circuit breaking above are a few hundred lines we can test exactly.

## AIENG-67: tracing

`observability/tracing.py`: spans with trace id, parent, name, kind (`workflow`, `step`, `model`,
`retrieval`, `tool`), times, and attributes (model, prompt name and version, tokens, estimated
cost, outcome, error type). The current span is carried in a context variable. Exporters:

- `JsonlExporter` (the default when `INCIDENTOPS_TRACE_FILE` is set): one span per line.
- `LangfuseExporter`: maps spans to Langfuse's public ingestion API (trace, span and generation
  events) over HTTP, without the SDK; tested against a fake ingestion server, not a live Langfuse.

Instrumented: workflow runs and steps, every model call (through the gateway), retrieval, tool
calls. `python -m incidentops_ai.observability.report` rebuilds one run from the trace file (its
steps, model calls, tokens, cost, errors) and summarises latency, tokens and cost per span name.

## AIENG-68: regression gates in CI

`python -m incidentops_ai.evals.gate` runs the offline layers (retrieval, workflow routing,
security), compares each to `evals/thresholds.toml`, writes `evals/dashboard.md` (quality, cost,
latency), and exits non-zero on any regression. Two more rules:

- **Prompt or model change**: the gate fingerprints every prompt file and the model routes. If the
  fingerprint differs from `evals/baseline.json`, the gate fails unless the baseline was refreshed
  with a real-model generation run in the same change. That is how a prompt change is forced to be
  evaluated, in a CI that has no API key.
- A test makes a **deliberate regression** in each gated layer and checks the gate fails.

## Hypotheses

- **H45.** Each layer has a dataset, metrics and a threshold, and today's tree passes every gate.
- **H46.** The judge agrees with the labels on at least 80% of faithfulness verdicts (kappa ≥ 0.6).
- **H47.** With the primary model failing, every call is answered by the fallback; after 3 failures
  the primary is skipped until the breaker's timeout; invalid output and client errors do not fall
  back; a real fallback call succeeds.
- **H48.** One workflow run is rebuilt from its trace: every step under the run's span, every model
  call with tokens and cost, matching `llm_runs`; the Langfuse adapter's batch is accepted by the fake.
- **H49.** A deliberate regression in retrieval, workflow routing or security, and a prompt change
  without a refreshed baseline, each fail the gate; the unchanged tree passes.

## Day by day

- **AIENG-64** this document, the golden set, thresholds.
- **AIENG-65** generation metrics, the judge prompt, the real run, calibration.
- **AIENG-66** the gateway, its tests, a real fallback check.
- **AIENG-67** tracing, exporters, instrumentation, the report.
- **AIENG-68** the gate, the dashboard, CI, findings, ADR-008.

## Risks

1. **Labels by the builder** are not independent; the user's labels would be.
2. **A gate on recorded results** catches a prompt change only by forcing a re-run; it cannot judge
   the new prompt itself without a key in CI.
3. **Sixteen golden cases** and thirty retrieval questions are small.
4. **Cost figures are estimates** at an assumed price.
