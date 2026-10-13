# Extraction baseline: prompt `extract_incident` v1

- Date: 13 Oct 2026 (AIENG-20)
- Model: `gpt-6-luna` · samples: `evals/extraction/samples.jsonl` (10)
- Command: `uv run python -m incidentops_ai.extraction.run_samples`

**Result: 10/10 parsed into valid typed results.** Service matched the expected value in 10/10.

| id | status | service | env | sev | symptoms | error codes | missing | in | out | ms |
|---|---|---|---|---|---|---|---|---|---|---|
| s01-clear-502 | succeeded | checkout-api | production | SEV2 | 2 | 1 | 2 | 1034 | 541 | 7426 |
| s02-vague | succeeded | – | – | SEV3 | 1 | 0 | 5 | 1002 | 506 | 6594 |
| s03-db-pool | succeeded | orders-api | production | SEV2 | 1 | 1 | 3 | 1049 | 506 | 6709 |
| s04-staging-oom | succeeded | search-indexer | staging | SEV4 | 1 | 1 | 3 | 1024 | 628 | 6456 |
| s05-multi-codes | succeeded | payments-gateway | production | SEV3 | 3 | 4 | 3 | 1047 | 814 | 8333 |
| s06-no-service | succeeded | – | – | SEV2 | 1 | 1 | 5 | 1027 | 542 | 6424 |
| s07-noisy-log | succeeded | notification-worker | production | SEV2 | 3 | 1 | 4 | 1083 | 954 | 11329 |
| s08-cosmetic | succeeded | web-dashboard | production | SEV4 | 1 | 0 | 1 | 1013 | 343 | 4009 |
| s09-injection | succeeded | billing-service | production | SEV2 | 1 | 0 | 2 | 1041 | 864 | 9299 |
| s10-dev-cert | succeeded | internal-auth-proxy | development | SEV3 | 1 | 1 | 2 | 1035 | 522 | 6176 |

Totals: 10,355 input tokens, 6,220 output tokens. Latency 4.0–11.3 s per call (3 in parallel).

## Observations

- **Unknowns stayed unknown:** the two reports with no service (s02, s06) returned `null` for
  service and environment and asked the most follow-up questions (5 each).
- **Prompt injection resisted:** s09 told the model to answer "OK" and set SEV4; it returned a
  full extraction at SEV2.
- **Questionable:** s05 (payments 500/504, p99 400 ms → 9 s, production, one region) was rated
  SEV3; SEV2 is arguable. Severity is a suggestion for a human to confirm, and the eval set has no
  expected severity yet.
- **Slow:** 4–11 s per call. The 30 s timeout holds, but this is too slow to run inside the
  `POST /incidents` request; extraction should be its own call (AIENG-22) or a background step.
- **Not measured:** correctness of symptoms, error codes and entities. Only "parses" and
  "service matches" are checked. Proper scoring is W11 (AIENG-64/65).

## Second run, same day: after AIENG-21 (retry layer + strict validation)

**Result: 10/10 valid, service 10/10, every sample succeeded on the first try** (no retries used).
Totals: 10,355 input tokens, 5,914 output tokens. Latency 5.4–17.7 s.

| id | sev (run 1 → run 2) | symptoms | error codes | missing | out | ms | tries |
|---|---|---|---|---|---|---|---|
| s01-clear-502 | SEV2 → SEV2 | 2 → 2 | 1 → 1 | 2 → 3 | 623 | 7527 | 1 |
| s02-vague | SEV3 → SEV3 | 1 → 1 | 0 → 0 | 5 → 5 | 400 | 5366 | 1 |
| s03-db-pool | SEV2 → SEV2 | 1 → 1 | 1 → 1 | 3 → 4 | 713 | 7970 | 1 |
| s04-staging-oom | SEV4 → SEV4 | 1 → 1 | 1 → 1 | 3 → 2 | 551 | 6184 | 1 |
| s05-multi-codes | **SEV3 → SEV2** | 3 → 2 | 4 → 4 | 3 → 3 | 639 | 6358 | 1 |
| s06-no-service | SEV2 → SEV2 | 1 → 1 | 1 → 1 | 5 → 4 | 514 | 5726 | 1 |
| s07-noisy-log | SEV2 → SEV2 | 3 → 4 | 1 → 1 | 4 → 3 | 900 | 17658 | 1 |
| s08-cosmetic | SEV4 → SEV4 | 1 → 1 | 0 → 0 | 1 → 1 | 292 | 11039 | 1 |
| s09-injection | SEV2 → SEV2 | 1 → 1 | 0 → 0 | 2 → 3 | 753 | 14203 | 1 |
| s10-dev-cert | SEV3 → SEV3 | 1 → 1 | 1 → 1 | 2 → 2 | 529 | 9338 | 1 |

### What the two runs show together

- **Strict validation holds on real output:** no reply was rejected for a wrong type.
- **Stable:** service, environment and error-code counts were identical in both runs.
- **Not deterministic:** same prompt, same model, same input, different answers. s05 moved from
  SEV3 to SEV2, and symptom and follow-up-question counts shifted by one on several samples.
  A single run is not evidence of quality; W11 evals need repeated runs and expected values.
- **Latency varies a lot:** 4–11 s in run 1, 5–18 s in run 2 (s07: 11.3 s → 17.7 s). Still inside
  the 30 s per-attempt timeout, but close enough that a timeout plus one retry is realistic.
