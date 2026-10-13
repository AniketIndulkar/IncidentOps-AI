# LLM failure matrix

Written 13 Oct 2026 (AIENG-23). Decisions behind it: [ADR-001](../adr/001-llm-failure-handling.md).

**Every row is an automated test** in `tests/test_llm_failure_matrix.py` (row 19 is in
`tests/test_analysis_api.py`). Each test injects the
fault at the HTTP level and drives the whole stack (API route → retry layer → real OpenAI SDK code
→ fake HTTP), then checks what the client sees, how many attempts were made and what was saved.
If this table and the tests disagree, the tests are right and this file is out of date.

Limits that apply to every row: at most **3 attempts**, at most **60 s** in total, at most
**30 s** per attempt, and no wait longer than **30 s**.

## Failures

| # | What goes wrong | How we detect it | What we do | Attempts | Client sees | Saved status |
|---|---|---|---|---|---|---|
| **Schema errors** | | | | | | |
| 1 | Reply is not JSON | Strict validation fails | Retry once, then fail | 2 | 502 `llm_invalid_output` | `invalid_output` |
| 2 | Reply has an invalid value (e.g. severity `CRITICAL`) | Strict validation fails | Retry once, then fail | 2 | 502 `llm_invalid_output` | `invalid_output` |
| 3 | Reply is missing a required field | Strict validation fails | Retry once, then fail | 2 | 502 `llm_invalid_output` | `invalid_output` |
| 4 | Reply has a wrong type (`"true"` for a boolean) | Strict validation fails; **no coercion** | Retry once, then fail | 2 | 502 `llm_invalid_output` | `invalid_output` |
| 5 | Reply cut off at the token cap | Response status `incomplete` | Retry once with **double** the cap, then fail | 2 | 502 `llm_truncated` | `truncated` |
| **Rate limits** | | | | | | |
| 6 | 429, rate limit | HTTP 429 | Wait as told by `Retry-After` (else backoff), retry | up to 3 | 503 `llm_rate_limited` + `Retry-After` | `rate_limited` |
| 7 | 429 asking us to wait more than 30 s | `Retry-After` > 30 | **Give up at once**; pass the wait on to the client | 1 | 503 `llm_rate_limited` + `Retry-After` | `rate_limited` |
| **Timeouts** | | | | | | |
| 8 | No answer within 30 s | SDK timeout | Retry with backoff | up to 3 | 504 `llm_timed_out` | `timed_out` |
| **Provider outage** | | | | | | |
| 9 | 500 / 503 from the provider | HTTP 5xx | Retry with backoff | up to 3 | 502 `llm_provider_error` | `provider_error` |
| 10 | Network unreachable | Connection error | Retry with backoff | up to 3 | 502 `llm_provider_error` | `provider_error` |
| 11 | HTML error page with status 200 (proxy) | Response is not a Response object | Retry with backoff | up to 3 | 502 `llm_provider_error` | `provider_error` |
| 12 | Broken JSON body with status 200 | JSON decode error | Retry with backoff | up to 3 | 502 `llm_provider_error` | `provider_error` |
| **Rejections: a retry cannot help** | | | | | | |
| 13 | Quota exhausted | 429 with code `insufficient_quota` | Fail at once | 1 | 500 `llm_rejected` | `rejected` |
| 14 | Invalid API key | 401 / 403 | Fail at once | 1 | 500 `llm_rejected` | `rejected` |
| 15 | Unknown model name | 404 | Fail at once | 1 | 500 `llm_rejected` | `rejected` |
| 16 | Malformed request (our bug) | 400 / 422 | Fail at once | 1 | 500 `llm_rejected` | `rejected` |
| 17 | Model refuses the content | Refusal part in the reply | Fail at once | 1 | 422 `llm_refused` | `refused` |
| 18 | Content filter blocks the reply | Status `incomplete`, reason `content_filter` | Fail at once | 1 | 422 `llm_refused` | `refused` |
| **Configuration** | | | | | | |
| 19 | No API key or model configured | Checked before any call | No call is made | 0 | 503 `llm_not_configured` | not recorded |

**In every failure row:** the client gets a problem response naming the recorded run id, **no
partial or guessed data is returned or stored**, and the response never contains incident text,
model output or stack traces.

**Why the HTTP status differs:** it tells the client whether trying again can help.
`503`/`504` = try again later. `502` = the provider or its answer was unusable; trying again may
help. `422` = this content was refused; the same text will be refused again. `500` = our own
configuration is wrong and needs an operator.

## Low confidence: not a failure

A reply can be perfectly valid and still not be safe to act on. That is decided by fixed rules
over the typed result (`extraction/review.py`), not by asking the model how sure it is.

| # | Situation | Review reason | Client sees |
|---|---|---|---|
| 20 | No service identified | `no_service_identified` | 200, `review.required = true` |
| 21 | Service inferred from context, not named | `service_inferred_not_stated` | 200, `review.required = true` |
| 22 | Environment not stated | `environment_not_stated` | 200, `review.required = true` |
| 23 | No symptoms found | `no_symptoms_found` | 200, `review.required = true` |
| 24 | 4 or more open questions | `many_open_questions` | 200, `review.required = true` |
| 25 | SEV1 suggested | `high_severity_needs_confirmation` | 200, `review.required = true` |
| 26 | Complete, unambiguous result | none | 200, `review.required = false` |

The extraction and the review decision are returned together and saved in the run record. Sending
the flagged ones to a person and waiting for approval is built in W08 (AIENG-49).

## Tool conversations

| What goes wrong | What we do | Covered in |
|---|---|---|
| Model asks for a tool that does not exist | Call rejected, never run; model is told | `tests/test_tools.py` |
| Model sends invalid arguments | Call rejected, never run; model is told which field, not the value | `tests/test_tools.py` |
| The tool itself crashes | Model is told the tool failed; error logged server-side only | `tests/test_tools.py` |
| Model keeps asking for tools | Stopped after 3 model turns → `invalid_output` | `tests/test_tools.py` |
| Provider fails mid-conversation | Same statuses and retry rules as above; calls already run are recorded | `tests/test_analysis_api.py` |

## Known gaps

- **No fallback model or second provider.** A full provider outage ends as `provider_error`. See ADR-001.
- **A hung attempt holds an HTTP request open for up to 60 s.** Acceptable for an explicit
  "analyse this" call; extraction is deliberately not part of `POST /incidents`.
- **No circuit breaker.** During an outage every request still makes its 3 attempts. Revisit in W12.
- **Semantically wrong but valid replies are not detected** (a plausible wrong service name).
  That needs evals with expected values (W11).
- **Repeating `POST …/extraction` runs the model again** and is billed again; there is no
  idempotency key on these endpoints yet.
