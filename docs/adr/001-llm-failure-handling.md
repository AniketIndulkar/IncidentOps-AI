# ADR-001: How IncidentOps handles LLM failures

- **Status:** Accepted
- **Date:** 13 Oct 2026 (AIENG-23, W02)
- **Scope:** every LLM call made through the provider layer (`src/incidentops_ai/llm/`)

## Context

IncidentOps uses an LLM to turn incident reports into structured data and to look up related
incidents. The output feeds on-call decisions, so a wrong answer that looks right is worse than
no answer.

LLM calls fail in ways ordinary API calls do not: the provider can be slow, overloaded or down;
the reply can be malformed, cut off or refused; and a reply can be valid yet not trustworthy.
In W02 we injected each of these faults and measured real behaviour (4–18 s per call on the
chosen model, and different answers for the same input across runs).

For every kind of failure we have four possible responses:

1. **Retry** the same call.
2. **Fall back** to another model or provider.
3. Send to **human review**.
4. **Reject**: fail explicitly and tell the caller.

We need one rule for choosing between them, so behaviour is predictable and testable rather than
decided case by case in each feature.

## Decision

**Choose by cause: who can fix this failure?**

| Cause | Who can fix it | Response |
|---|---|---|
| The provider is temporarily slow, limited or down | Time | **Retry**, bounded |
| The model gave an unusable reply (invalid, cut off) | Possibly another try | **Retry once**, then reject |
| Our request, key, model name or quota is wrong | An operator | **Reject** immediately |
| The model refused the content | Nobody, by retrying | **Reject** immediately |
| The reply is valid but thin or high-stakes | A person | **Human review** |

In detail:

1. **Retry only what time can fix**: timeouts, rate limits, 5xx and network errors. Use
   exponential backoff with jitter, honour `Retry-After`, and stop at whichever comes first of
   3 attempts, 60 s in total, or a requested wait above 30 s.
2. **Retry unusable output exactly once.** A second bad reply means the prompt or schema is the
   problem, and more attempts only add cost. A cut-off reply is retried with double the token cap.
3. **Never repair or coerce a reply.** Validation is strict; a wrong type is a failed reply. We
   do not fix up JSON, fill defaults or accept "close enough".
4. **Reject at once when a retry cannot help**: bad request, bad key, unknown model, exhausted
   quota, refusal, content filter.
5. **Every failure is an explicit status with no data.** The caller gets a named status
   (`timed_out`, `rate_limited`, …) and the id of the recorded run. Partial or guessed data is
   never returned or stored.
6. **Human review is decided by fixed rules over the typed result**, not by the model's own
   confidence: no service, service only inferred, no environment, no symptoms, four or more open
   questions, or a SEV1 suggestion. A flagged result is still returned, marked `review.required`
   with reasons.
7. **No fallback model or provider for now.** See the options below.
8. **Every run is recorded**, failed or not, with prompt version, model, per-attempt statuses,
   tokens and latency.

The full row-by-row behaviour is in [the failure matrix](../notes/llm-failure-matrix.md); each
row is an automated test.

## Options considered

### Retry

| Option | Verdict |
|---|---|
| Let the SDK retry (its default: 2 silent retries, 600 s timeout) | **Rejected.** Attempts are invisible, cannot be counted or tested, and total time is unbounded for an API request. |
| Retry everything a few times | **Rejected.** Retrying a bad key, exhausted quota or a refusal wastes time and money and hides the real cause. |
| **Our own bounded retry, by cause** | **Chosen.** Explicit limits on attempts, total time and wait; each attempt recorded. |

### Fallback

| Option | Verdict |
|---|---|
| Fall back to another model at the **same provider** | **Rejected for now.** Outages and rate limits are mostly account- or provider-wide, so the fallback fails for the same reason. It would also double the prompts to maintain and evaluate. |
| Fall back to a **second provider** | **Deferred to W11** (AIENG-66, LLM gateway). It needs a second account, key, cost budget and per-provider prompt testing; without evals (W11) we cannot show the fallback's output is good enough to trust. |
| Fall back to a **non-LLM path** (regex extraction, defaults) | **Rejected.** It produces data that looks like a model result but is weaker, which is the silent wrong answer this system must avoid. |
| **No fallback: fail explicitly** | **Chosen for now.** The incident itself is always stored without the LLM; only the analysis is unavailable, and the caller is told so. |

### Human review

| Option | Verdict |
|---|---|
| Ask the model for a confidence score and review below a threshold | **Rejected.** Self-reported confidence is not calibrated and changes between runs; a threshold on it is not testable. |
| Review everything | **Rejected.** Removes the benefit of automation and trains reviewers to approve without reading. |
| **Fixed rules over the typed result** | **Chosen.** Deterministic, explainable to the reviewer, and unit-testable. Rules can be tuned later against eval data. |

### Rejection

| Option | Verdict |
|---|---|
| Return whatever fields parsed, with gaps | **Rejected.** Partial data is indistinguishable from complete data downstream. |
| Raise exceptions from the provider layer | **Rejected.** Vendor exception types would leak into every caller, and a forgotten `except` becomes a 500. |
| **A result value with an explicit status and no data** | **Chosen.** Callers must check the status; the API maps it to an HTTP status that says whether retrying can help. |

## Consequences

**Good**

- Behaviour under failure is predictable and covered by tests that run offline, in CI, at no cost.
- Worst-case time and cost per call are bounded: 3 attempts, 60 s.
- No silent wrong answers from repair, coercion or weak fallbacks.
- Failed runs are visible in `llm_runs`, so failure rates can be measured later.

**Costs we accept**

- A provider outage makes LLM features unavailable; there is no second path until W11.
- A request can be held open for up to 60 s. LLM analysis is therefore its own endpoint and is
  never part of creating an incident.
- Strictness rejects some replies a lenient parser would have used.
- Review rules are simple and will flag more than necessary (for example, every report without
  an environment). That is the safe direction; tune with eval data.
- No circuit breaker: during an outage each request still makes its attempts.

## Revisit when

- **W08 (AIENG-49):** human review becomes a real approval step; the rules here decide what enters it.
- **W11 (AIENG-64–66):** evals give expected values, making a second provider and tuned review
  rules possible; the gateway provides fallback routing.
- **W12 (AIENG-70, 72):** failure injection against all dependencies; add a circuit breaker and
  define the degraded modes (FULL → REDUCED → MANUAL) if measurements call for it.
