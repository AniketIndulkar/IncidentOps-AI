# Week 11 write-up: evaluation layers, a gateway and tracing (AIENG-64 to AIENG-68)

Status on 17 Oct 2026: **built and measured**, as a pull request stacked on W10. Design and
hypotheses were committed before any W11 code ([roadmap-w11.md](../roadmap-w11.md)); results in
[the findings](../../evals/generation/runs/2026-10-17-w11-findings.md); the decision in
[ADR-008](../adr/008-evaluation-gateway-and-tracing.md); the numbers in
[the dashboard](../../evals/dashboard.md).

## The result in five lines

1. **Four evaluation layers** (retrieval, generation, workflow, system, plus security), each with
   its dataset and metrics; the offline ones have thresholds, and today's tree passes them all.
2. **The generation layer** answered or declined correctly on 15 of 16 golden cases, with every
   citation from a relevant document.
3. **The LLM judge was not trustworthy out of the box**: 31% agreement with the labels at first,
   77% after a clearer rubric and a harness fix. It is reported with its agreement, not gated.
4. **Every model call goes through a gateway** with per-task routes, fallback on retryable
   failures only, and a circuit breaker; a real fallback was seen to work.
5. **Every run is traced**, and **a CI gate** fails retrieval, routing and security regressions,
   and any prompt change without a fresh real-model run. 929 tests pass (2 opt-in skipped).

## What exists now

```
src/incidentops_ai/evals/       generation.py (metrics, judge, calibration), layers.py, gate.py
src/incidentops_ai/llm/         gateway.py, gateway_check.py
src/incidentops_ai/observability/  tracing.py, langfuse.py, report.py
prompts judge_claim/v1, v2
evals/generation/               golden.jsonl (16), labels.jsonl (13), runs/
evals/thresholds.toml, evals/baseline.json, evals/dashboard.md
evals/security/                 injection_set.jsonl, pii_set.jsonl
.github/workflows/ci.yml        "Eval regression gate" step
```

## Decisions, with why and why not

| Decision | Why | Why not the alternative |
|---|---|---|
| **Layers with their own purpose** | A failure says which part broke | One accuracy number |
| **RAGAS-style metrics in our code** | Versioned prompts, recorded outputs | RAGAS/DeepEval libraries |
| **Judge reported, not gated** | 77% agreement is not enough to block a change | Trusting the judge |
| **In-house gateway** | Our structured output and guard depend on our interface | LiteLLM |
| **Langfuse over HTTP** | A documented batch API | The SDK |
| **Prompt fingerprint in the gate** | Forces a real run when a prompt changes | Real-model evals on every push |

## What the numbers taught

- An LLM judge disagrees with you for reasons you have to read: a strict rubric, a missing field
  in its input, and sometimes because it is right and your label was lenient.
- Two of the three changes that improved agreement were not about the model at all.
- A relevance score where both raters always say yes measures nothing.
- A gate can only insist that a prompt change be evaluated; it cannot evaluate it without a key.

## What went wrong on the way

- The first judge input left out the incident's service; the judge pointed it out itself.
- Strict structured output needs `extra="forbid"` on every schema, including a ten-line check's.
- The tracer at first marked the approval pause (LangGraph raises to pause) as an error.
- The fake provider reports no tokens; a trace test assumed it did.

## What is still weak

- The labels are mine, and there are thirteen of them.
- The Langfuse adapter has never met a live Langfuse.
- The system layer has no thresholds until there is production traffic.

## Where to look

- `uv run python -m incidentops_ai.evals.gate` and `evals/dashboard.md`
- `uv run python -m incidentops_ai.observability.report traces.jsonl --trace <run id without dashes>`
