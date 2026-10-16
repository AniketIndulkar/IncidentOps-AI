# Retrieval stack comparison, 2026-10-16

- Embedding model: `text-embedding-3-small`
- Questions: 30; top 10; a chunk is relevant when it holds at least half of an evidence span
- Reproduce: `uv run python -m incidentops_ai.retrieval.stack_benchmark`
- Latency is end to end per question. Dense stages include one query-embedding call (live when the run used the API, so it reflects the network).
- Cost is an *estimate* of query-embedding API spend per 1,000 queries ($0.02 per million tokens, 4 characters per token). BM25 and a local reranker cost no API money.

## fixed-2000-200 (decision config)

| Stage | Recall@1 | Recall@5 | Precision@5 | Hit@1 | MRR@10 | Chars read@5 | mean ms | p50 ms | p95 ms | est. $/1,000 queries |
|---|---|---|---|---|---|---|---|---|---|---|
| dense | 0.800 | 0.983 | 0.260 | 0.933 | 0.961 | 7433 | 257 | 186 | 771 | 0.0004 |
| bm25 | 0.817 | 0.983 | 0.260 | 0.933 | 0.957 | 8772 | 0 | 0 | 0 | 0.0000 |
| hybrid | 0.817 | 1.000 | 0.267 | 0.933 | 0.967 | 8384 | 177 | 167 | 248 | 0.0004 |
| hybrid-rerank | 0.817 | 1.000 | 0.267 | 0.933 | 0.961 | 8346 | 825 | 824 | 895 | 0.0004 |

### Questions moved

- bm25 vs dense: improved 3 (q01, q08, q25); worse 2 (q10, q24)
- hybrid vs dense: improved 3 (q01, q08, q25); worse 1 (q24)
- hybrid vs bm25: improved 2 (q10, q24); worse 1 (q08)
- hybrid-rerank vs hybrid: improved 1 (q08); worse 2 (q15, q24)

### Reranker, local cost

- Load and first call: 0.5 s. Reranking 20 candidates: mean 645 ms, p95 715 ms.
- Memory added: 1298 MB. Candidates cut at the 512-token limit: 87 of 600 reranked.
- **Too heavy locally:** p95 715 ms is above the 300 ms bar; memory added 1298 MB is above the 500 MB bar. Bars: p95 300 ms, memory added 500 MB.
- Whole stack, end to end p95: 895 ms, within the 1000 ms budget.

### MRR by question style

| Style | n | dense | bm25 | hybrid | hybrid-rerank |
|---|---|---|---|---|---|
| identifier | 5 | 1.000 | 1.000 | 1.000 | 1.000 |
| lookup | 8 | 0.854 | 0.938 | 0.938 | 1.000 |
| multi | 5 | 1.000 | 0.840 | 0.900 | 0.867 |
| paraphrase | 8 | 1.000 | 1.000 | 1.000 | 0.938 |
| procedure | 4 | 1.000 | 1.000 | 1.000 | 1.000 |

### Misses: dense (1 of 30 questions)

Questions whose evidence is not all inside the top 5.

#### q25 (multi): Why did the February notification outage take so long to diagnose?

- first relevant at rank 1; Recall@5 0.50
- not found: `rb-notification-kafka`: “An expired broker certificate shows up in the client as NoBrokersAvailable, not as a TLS error.”
- returned #1 `pm-2026-02-kafka` › Postmortem: notifications stopped when the Kafka broker certificate expired (score 0.458): # Postmortem: notifications stopped when the Kafka broker certificate expired Date: 3 February 2026. Severity…
- returned #2 `pm-2026-08-session` › Postmortem: German users logged out after Redis failover (score 0.430): # Postmortem: German users logged out after Redis failover Date: 18 August 2026. Severity: SEV2. Duration: 47…
- returned #3 `pm-2026-06-payments` › Postmortem: card charges timing out in eu-west-1 (score 0.426): # Postmortem: card charges timing out in eu-west-1 Date: 4 June 2026. Severity: SEV1. Duration: 26 minutes. A…

### Misses: bm25 (1 of 30 questions)

Questions whose evidence is not all inside the top 5.

#### q24 (multi): Why did the double charge happen on a technical level?

- first relevant at rank 5; Recall@5 0.50
- not found: `rb-billing-duplicates`: “If the retry path builds a new idempotency key for each attempt, the payments gateway cannot tell the attempt…”
- returned #1 `rb-payments-latency` › payments-gateway latency and charge failures (score 4.454): # payments-gateway latency and charge failures payments-gateway talks to two card acquirers and to Stripe. Us…
- returned #2 `pm-2026-06-payments` › Postmortem: card charges timing out in eu-west-1 (score 4.300): # Postmortem: card charges timing out in eu-west-1 Date: 4 June 2026. Severity: SEV1. Duration: 26 minutes. A…
- returned #3 `rb-general-triage` › General incident triage and severity levels (score 4.205): # General incident triage and severity levels This runbook applies to every service. The first responder owns…

### Misses: hybrid (0 of 30 questions)

Questions whose evidence is not all inside the top 5.

### Misses: hybrid-rerank (0 of 30 questions)

Questions whose evidence is not all inside the top 5.

## section-1500+ctx (decision config)

| Stage | Recall@1 | Recall@5 | Precision@5 | Hit@1 | MRR@10 | Chars read@5 | mean ms | p50 ms | p95 ms | est. $/1,000 queries |
|---|---|---|---|---|---|---|---|---|---|---|
| dense | 0.722 | 0.950 | 0.240 | 0.833 | 0.889 | 2016 | 193 | 173 | 337 | 0.0004 |
| bm25 | 0.639 | 0.900 | 0.227 | 0.733 | 0.815 | 2019 | 0 | 0 | 0 | 0.0000 |
| hybrid | 0.739 | 0.950 | 0.240 | 0.867 | 0.918 | 2037 | 185 | 174 | 253 | 0.0004 |
| hybrid-rerank | 0.706 | 0.967 | 0.247 | 0.800 | 0.889 | 2025 | 492 | 457 | 648 | 0.0004 |

### Questions moved

- bm25 vs dense: improved 2 (q06, q22); worse 8 (q10, q13, q15, q17, q24, q25, q28, q29)
- hybrid vs dense: improved 3 (q06, q22, q24); worse 2 (q10, q28)
- hybrid vs bm25: improved 8 (q10, q13, q15, q17, q24, q25, q28, q29); worse 0
- hybrid-rerank vs hybrid: improved 3 (q10, q28, q29); worse 2 (q11, q13)

### Reranker, local cost

- Load and first call: 0.0 s. Reranking 20 candidates: mean 296 ms, p95 455 ms.
- Memory added: 11 MB. Candidates cut at the 512-token limit: 0 of 600 reranked.
- **Too heavy locally:** p95 455 ms is above the 300 ms bar. Bars: p95 300 ms, memory added 500 MB.
- Whole stack, end to end p95: 648 ms, within the 1000 ms budget.

### MRR by question style

| Style | n | dense | bm25 | hybrid | hybrid-rerank |
|---|---|---|---|---|---|
| identifier | 5 | 0.900 | 1.000 | 1.000 | 1.000 |
| lookup | 8 | 0.906 | 0.781 | 0.838 | 0.938 |
| multi | 5 | 0.850 | 0.592 | 0.900 | 0.800 |
| paraphrase | 8 | 0.917 | 0.781 | 0.917 | 0.771 |
| procedure | 4 | 0.833 | 1.000 | 1.000 | 1.000 |

### Misses: dense (3 of 30 questions)

Questions whose evidence is not all inside the top 5.

#### q24 (multi): Why did the double charge happen on a technical level?

- first relevant at rank 4; Recall@5 0.50
- not found: `rb-billing-duplicates`: “If the retry path builds a new idempotency key for each attempt, the payments gateway cannot tell the attempt…”
- returned #1 `rb-billing-duplicates` › billing-service charges customers twice (score 0.414): # billing-service charges customers twice Use this runbook when finance or Support report that customers were…
- returned #2 `pm-2026-09-billing` › Postmortem: annual subscribers charged twice > What went badly (score 0.402): ## What went badly - Detection took more than four hours. A monitor every 4 hours is too slow for charges. - …
- returned #3 `pm-2026-06-payments` › Postmortem: card charges timing out in eu-west-1 > What went well (score 0.399): ## What went well - Failover was safe because every attempt carries an idempotency key. The reconciliation jo…

#### q25 (multi): Why did the February notification outage take so long to diagnose?

- first relevant at rank 1; Recall@5 0.50
- not found: `rb-notification-kafka`: “An expired broker certificate shows up in the client as NoBrokersAvailable, not as a TLS error.”
- returned #1 `pm-2026-02-kafka` › Postmortem: notifications stopped when the Kafka broker certificate expired > Why it took so long (score 0.466): ## Why it took so long The client error `NoBrokersAvailable` does not mention TLS. Responders spent almost tw…
- returned #2 `pm-2026-09-billing` › Postmortem: annual subscribers charged twice > What went badly (score 0.463): ## What went badly - Detection took more than four hours. A monitor every 4 hours is too slow for charges. - …
- returned #3 `pm-2026-08-session` › Postmortem: German users logged out after Redis failover > What went badly (score 0.458): ## What went well - Comparing the metric per region quickly proved the problem was regional. ## What went bad…

#### q29 (multi): What went wrong with clock drift during the German login incident?

- first relevant at rank 1; Recall@5 0.50
- not found: `rb-session-invalid`: “If an app node's clock drifts by more than 60 seconds the signature check rejects fresh cookies.”
- returned #1 `pm-2026-08-session` › Postmortem: German users logged out after Redis failover > What went badly (score 0.599): ## What went well - Comparing the metric per region quickly proved the problem was regional. ## What went bad…
- returned #2 `pm-2026-08-session` › Postmortem: German users logged out after Redis failover > Root cause (score 0.575): ## Root cause Two independent faults overlapped. The Redis failover lost sessions written in the last seconds…
- returned #3 `pm-2026-08-session` › Postmortem: German users logged out after Redis failover > Summary (score 0.536): # Postmortem: German users logged out after Redis failover Date: 18 August 2026. Severity: SEV2. Duration: 47…

### Misses: bm25 (4 of 30 questions)

Questions whose evidence is not all inside the top 5.

#### q10 (lookup): How often must we post updates during a SEV1 incident?

- no relevant chunk in the top 10; Recall@5 0.00
- not found: `rb-general-triage`: “| SEV1 | Customers cannot pay, log in, or use the core product, or data is lost or exposed | Page immediately…”
- returned #1 `rb-general-triage` › General incident triage and severity levels > Communication (score 8.719): ## Communication - Post a customer-facing status update within 20 minutes of declaring SEV1 or SEV2. - State …
- returned #2 `pm-2026-07-search` › Postmortem: search results stale after repeated indexer restarts > What we decided not to do (score 5.435): ## What we decided not to do We considered raising the memory limit to 4Gi. We rejected it because the nightl…
- returned #3 `rb-session-invalid` › Users get "Invalid session" after logging in > Fixes (score 5.247): ## Fixes - For a Redis failover: sessions created before the failover are gone and users must log in once mor…

#### q24 (multi): Why did the double charge happen on a technical level?

- first relevant at rank 8; Recall@5 0.00
- not found: `pm-2026-09-billing`: “The new retry helper generated a fresh idempotency key for every attempt, built from the current timestamp.”
- not found: `rb-billing-duplicates`: “If the retry path builds a new idempotency key for each attempt, the payments gateway cannot tell the attempt…”
- returned #1 `rb-session-invalid` › Users get "Invalid session" after logging in > Narrowing down the cause (score 5.530): ## Narrowing down the cause 1. **Is it one region?** Compare the `session_validation_failures` metric per reg…
- returned #2 `rb-payments-latency` › payments-gateway latency and charge failures > PAY-1042 acquirer timeouts (score 4.647): ## PAY-1042 acquirer timeouts A spike of PAY-1042 together with `StripeConnectionError` and a p99 near 9 seco…
- returned #3 `rb-general-triage` › General incident triage and severity levels > Severity levels (score 4.486): ## Severity levels | Level | Definition | Response | |-------|------------|----------| | SEV1 | Customers can…

#### q25 (multi): Why did the February notification outage take so long to diagnose?

- first relevant at rank 3; Recall@5 0.50
- not found: `rb-notification-kafka`: “An expired broker certificate shows up in the client as NoBrokersAvailable, not as a TLS error.”
- returned #1 `rb-orders-db-pool` › orders-api database connection pool exhausted > Diagnose (score 9.232): ## Diagnose 1. Count connections by state on the primary: ``` SELECT state, count(*) FROM pg_stat_activity WH…
- returned #2 `rb-notification-kafka` › notification-worker cannot reach Kafka > Escalation (score 8.538): ## Escalation Password reset emails travel through this worker. If the outage lasts more than 30 minutes, tel…
- returned #3 `pm-2026-02-kafka` › Postmortem: notifications stopped when the Kafka broker certificate expired > Why it took so long (score 8.042): ## Why it took so long The client error `NoBrokersAvailable` does not mention TLS. Responders spent almost tw…

#### q29 (multi): What went wrong with clock drift during the German login incident?

- first relevant at rank 2; Recall@5 0.50
- not found: `rb-session-invalid`: “If an app node's clock drifts by more than 60 seconds the signature check rejects fresh cookies.”
- returned #1 `pm-2026-08-session` › Postmortem: German users logged out after Redis failover > Root cause (score 10.667): ## Root cause Two independent faults overlapped. The Redis failover lost sessions written in the last seconds…
- returned #2 `pm-2026-08-session` › Postmortem: German users logged out after Redis failover > What went badly (score 9.858): ## What went well - Comparing the metric per region quickly proved the problem was regional. ## What went bad…
- returned #3 `pm-2026-08-session` › Postmortem: German users logged out after Redis failover > Action items (score 7.375): ## Action items 1. Alert on `node_timex_offset_seconds` above 30 seconds (owner: Platform, done). 2. Add a st…

### Misses: hybrid (3 of 30 questions)

Questions whose evidence is not all inside the top 5.

#### q24 (multi): Why did the double charge happen on a technical level?

- first relevant at rank 2; Recall@5 0.50
- not found: `rb-billing-duplicates`: “If the retry path builds a new idempotency key for each attempt, the payments gateway cannot tell the attempt…”
- returned #1 `pm-2026-06-payments` › Postmortem: card charges timing out in eu-west-1 > What went well (score 0.031): ## What went well - Failover was safe because every attempt carries an idempotency key. The reconciliation jo…
- returned #2 `pm-2026-09-billing` › Postmortem: annual subscribers charged twice > Root cause (score 0.030): ## Root cause The new retry helper generated a fresh idempotency key for every attempt, built from the curren…
- returned #3 `rb-billing-duplicates` › billing-service charges customers twice (score 0.030): # billing-service charges customers twice Use this runbook when finance or Support report that customers were…

#### q25 (multi): Why did the February notification outage take so long to diagnose?

- first relevant at rank 1; Recall@5 0.50
- not found: `rb-notification-kafka`: “An expired broker certificate shows up in the client as NoBrokersAvailable, not as a TLS error.”
- returned #1 `pm-2026-02-kafka` › Postmortem: notifications stopped when the Kafka broker certificate expired > Why it took so long (score 0.032): ## Why it took so long The client error `NoBrokersAvailable` does not mention TLS. Responders spent almost tw…
- returned #2 `pm-2026-02-kafka` › Postmortem: notifications stopped when the Kafka broker certificate expired (score 0.031): # Postmortem: notifications stopped when the Kafka broker certificate expired Date: 3 February 2026. Severity…
- returned #3 `pm-2026-06-payments` › Postmortem: card charges timing out in eu-west-1 > Root cause (score 0.030): ## Root cause The acquirer reported a database outage on their side. Our gateway had no circuit breaker aroun…

#### q29 (multi): What went wrong with clock drift during the German login incident?

- first relevant at rank 1; Recall@5 0.50
- not found: `rb-session-invalid`: “If an app node's clock drifts by more than 60 seconds the signature check rejects fresh cookies.”
- returned #1 `pm-2026-08-session` › Postmortem: German users logged out after Redis failover > What went badly (score 0.033): ## What went well - Comparing the metric per region quickly proved the problem was regional. ## What went bad…
- returned #2 `pm-2026-08-session` › Postmortem: German users logged out after Redis failover > Root cause (score 0.033): ## Root cause Two independent faults overlapped. The Redis failover lost sessions written in the last seconds…
- returned #3 `pm-2026-08-session` › Postmortem: German users logged out after Redis failover > Timeline (CET) (score 0.031): ## Timeline (CET) - 07:03 The primary node of the eu-central Redis cluster failed its health check. - 07:04 A…

### Misses: hybrid-rerank (2 of 30 questions)

Questions whose evidence is not all inside the top 5.

#### q24 (multi): Why did the double charge happen on a technical level?

- first relevant at rank 2; Recall@5 0.50
- not found: `rb-billing-duplicates`: “If the retry path builds a new idempotency key for each attempt, the payments gateway cannot tell the attempt…”
- returned #1 `pm-2026-06-payments` › Postmortem: card charges timing out in eu-west-1 > What went well (score -2.976): ## What went well - Failover was safe because every attempt carries an idempotency key. The reconciliation jo…
- returned #2 `pm-2026-09-billing` › Postmortem: annual subscribers charged twice > Root cause (score -4.554): ## Root cause The new retry helper generated a fresh idempotency key for every attempt, built from the curren…
- returned #3 `rb-payments-latency` › payments-gateway latency and charge failures > PAY-1042 acquirer timeouts (score -4.943): ## PAY-1042 acquirer timeouts A spike of PAY-1042 together with `StripeConnectionError` and a p99 near 9 seco…

#### q25 (multi): Why did the February notification outage take so long to diagnose?

- first relevant at rank 1; Recall@5 0.50
- not found: `rb-notification-kafka`: “An expired broker certificate shows up in the client as NoBrokersAvailable, not as a TLS error.”
- returned #1 `pm-2026-02-kafka` › Postmortem: notifications stopped when the Kafka broker certificate expired > Why it took so long (score -0.189): ## Why it took so long The client error `NoBrokersAvailable` does not mention TLS. Responders spent almost tw…
- returned #2 `pm-2026-02-kafka` › Postmortem: notifications stopped when the Kafka broker certificate expired (score -3.406): # Postmortem: notifications stopped when the Kafka broker certificate expired Date: 3 February 2026. Severity…
- returned #3 `rb-notification-kafka` › notification-worker cannot reach Kafka > Escalation (score -4.107): ## Escalation Password reset emails travel through this worker. If the outage lasts more than 30 minutes, tel…

## section-1500 (exploratory)

| Stage | Recall@1 | Recall@5 | Precision@5 | Hit@1 | MRR@10 | Chars read@5 | mean ms | p50 ms | p95 ms | est. $/1,000 queries |
|---|---|---|---|---|---|---|---|---|---|---|
| dense | 0.622 | 0.933 | 0.233 | 0.700 | 0.801 | 1961 | 191 | 186 | 246 | 0.0004 |
| bm25 | 0.589 | 0.850 | 0.213 | 0.667 | 0.764 | 2080 | 0 | 0 | 0 | 0.0000 |
| hybrid | 0.589 | 0.867 | 0.220 | 0.667 | 0.793 | 2015 | 170 | 163 | 216 | 0.0004 |
| hybrid-rerank | 0.706 | 0.967 | 0.247 | 0.800 | 0.889 | 2046 | 467 | 426 | 608 | 0.0004 |

### Questions moved

- bm25 vs dense: improved 4 (q06, q07, q22, q25); worse 9 (q04, q10, q13, q15, q16, q17, q24, q28, q29)
- hybrid vs dense: improved 5 (q06, q07, q22, q24, q25); worse 4 (q10, q15, q16, q28)
- hybrid vs bm25: improved 10 (q04, q10, q13, q15, q16, q17, q24, q25, q28, q29); worse 3 (q06, q07, q22)
- hybrid-rerank vs hybrid: improved 8 (q06, q07, q10, q15, q16, q22, q28, q29); worse 2 (q11, q13)

### Reranker, local cost

- Load and first call: 0.0 s. Reranking 20 candidates: mean 278 ms, p95 423 ms.
- Memory added: 0 MB. Candidates cut at the 512-token limit: 0 of 600 reranked.
- **Too heavy locally:** p95 423 ms is above the 300 ms bar. Bars: p95 300 ms, memory added 500 MB.
- Whole stack, end to end p95: 608 ms, within the 1000 ms budget.

### MRR by question style

| Style | n | dense | bm25 | hybrid | hybrid-rerank |
|---|---|---|---|---|---|
| identifier | 5 | 0.850 | 0.900 | 0.900 | 1.000 |
| lookup | 8 | 0.854 | 0.750 | 0.786 | 0.938 |
| multi | 5 | 0.617 | 0.607 | 0.800 | 0.800 |
| paraphrase | 8 | 0.833 | 0.675 | 0.708 | 0.771 |
| procedure | 4 | 0.800 | 1.000 | 0.833 | 1.000 |

### Misses: dense (4 of 30 questions)

Questions whose evidence is not all inside the top 5.

#### q22 (procedure): How do I check whether checkout 502 errors come from a keepalive mismatch?

- first relevant at rank 5; Recall@5 0.50
- not found: `rb-checkout-502`: “The setting that must hold is: the application keepalive timeout must be longer than the sidecar's `keepalive…”
- returned #1 `rb-checkout-502` › checkout-api returns 502 Bad Gateway (score 0.559): # checkout-api returns 502 Bad Gateway Use this runbook when the edge load balancer reports 502 responses for…
- returned #2 `rb-checkout-502` › checkout-api returns 502 Bad Gateway > Symptoms (score 0.532): ## Symptoms - The load balancer dashboard shows `HTTPCode_ELB_502_Count` rising while checkout-api pods still…
- returned #3 `rb-checkout-502` › checkout-api returns 502 Bad Gateway > First checks (five minutes) (score 0.527): ## First checks (five minutes) 1. Open the Deploys dashboard and look for a checkout-api release in the last …

#### q24 (multi): Why did the double charge happen on a technical level?

- first relevant at rank 4; Recall@5 0.50
- not found: `rb-billing-duplicates`: “If the retry path builds a new idempotency key for each attempt, the payments gateway cannot tell the attempt…”
- returned #1 `pm-2026-06-payments` › Postmortem: card charges timing out in eu-west-1 > What went well (score 0.425): ## What went well - Failover was safe because every attempt carries an idempotency key. The reconciliation jo…
- returned #2 `rb-billing-duplicates` › billing-service charges customers twice (score 0.410): # billing-service charges customers twice Use this runbook when finance or Support report that customers were…
- returned #3 `pm-2026-09-billing` › Postmortem: annual subscribers charged twice > What went badly (score 0.391): ## What went badly - Detection took more than four hours. A monitor every 4 hours is too slow for charges. - …

#### q25 (multi): Why did the February notification outage take so long to diagnose?

- first relevant at rank 3; Recall@5 0.50
- not found: `rb-notification-kafka`: “An expired broker certificate shows up in the client as NoBrokersAvailable, not as a TLS error.”
- returned #1 `pm-2026-08-session` › Postmortem: German users logged out after Redis failover > What went badly (score 0.479): ## What went well - Comparing the metric per region quickly proved the problem was regional. ## What went bad…
- returned #2 `pm-2026-09-billing` › Postmortem: annual subscribers charged twice > What went badly (score 0.469): ## What went badly - Detection took more than four hours. A monitor every 4 hours is too slow for charges. - …
- returned #3 `pm-2026-02-kafka` › Postmortem: notifications stopped when the Kafka broker certificate expired > Why it took so long (score 0.466): ## Why it took so long The client error `NoBrokersAvailable` does not mention TLS. Responders spent almost tw…

#### q29 (multi): What went wrong with clock drift during the German login incident?

- first relevant at rank 2; Recall@5 0.50
- not found: `rb-session-invalid`: “If an app node's clock drifts by more than 60 seconds the signature check rejects fresh cookies.”
- returned #1 `pm-2026-08-session` › Postmortem: German users logged out after Redis failover > Summary (score 0.543): # Postmortem: German users logged out after Redis failover Date: 18 August 2026. Severity: SEV2. Duration: 47…
- returned #2 `pm-2026-08-session` › Postmortem: German users logged out after Redis failover > What went badly (score 0.486): ## What went well - Comparing the metric per region quickly proved the problem was regional. ## What went bad…
- returned #3 `pm-2026-08-session` › Postmortem: German users logged out after Redis failover > Root cause (score 0.486): ## Root cause Two independent faults overlapped. The Redis failover lost sessions written in the last seconds…

### Misses: bm25 (7 of 30 questions)

Questions whose evidence is not all inside the top 5.

#### q10 (lookup): How often must we post updates during a SEV1 incident?

- no relevant chunk in the top 10; Recall@5 0.00
- not found: `rb-general-triage`: “| SEV1 | Customers cannot pay, log in, or use the core product, or data is lost or exposed | Page immediately…”
- returned #1 `rb-general-triage` › General incident triage and severity levels > Communication (score 8.201): ## Communication - Post a customer-facing status update within 20 minutes of declaring SEV1 or SEV2. - State …
- returned #2 `rb-payments-latency` › payments-gateway latency and charge failures > Stripe unavailable (PAY-1050) (score 5.471): ## Stripe unavailable (PAY-1050) If status.stripe.com shows an incident, run `payctl fallback enable stripe`.…
- returned #3 `pm-2026-07-search` › Postmortem: search results stale after repeated indexer restarts > What we decided not to do (score 5.250): ## What we decided not to do We considered raising the memory limit to 4Gi. We rejected it because the nightl…

#### q16 (paraphrase): Writes are timing out because we ran out of database connections. Can we just raise the pool size during the incident?

- first relevant at rank 2; Recall@5 0.50
- not found: `pm-2026-05-orders-pool`: “Raising the pool size made things worse.”
- returned #1 `rb-orders-db-pool` › orders-api database connection pool exhausted (score 15.441): # orders-api database connection pool exhausted Use this runbook when orders-api writes time out and the logs…
- returned #2 `rb-orders-db-pool` › orders-api database connection pool exhausted > Fix (score 14.013): ## Fix - Terminate sessions that have been idle in transaction for more than 5 minutes with `SELECT pg_termin…
- returned #3 `rb-orders-db-pool` › orders-api database connection pool exhausted > How the pool is configured (score 12.452): ## How the pool is configured orders-api opens at most 15 connections to `orders-db-primary` per pod: `pool_s…

#### q22 (procedure): How do I check whether checkout 502 errors come from a keepalive mismatch?

- first relevant at rank 1; Recall@5 0.50
- not found: `pm-2026-03-checkout`: “The release replaced the application server's default keepalive timeout of 120 seconds with an explicit value…”
- returned #1 `rb-checkout-502` › checkout-api returns 502 Bad Gateway > Likely causes and fixes > Keepalive mismatch between nginx and the app (score 11.489): ### Keepalive mismatch between nginx and the app The nginx sidecar keeps upstream connections open for 75 sec…
- returned #2 `pm-2026-03-checkout` › Postmortem: checkout-api 502 errors after release v2.31.0 > Summary (score 9.519): # Postmortem: checkout-api 502 errors after release v2.31.0 Date: 12 March 2026. Severity: SEV2. Duration: 38…
- returned #3 `rb-checkout-502` › checkout-api returns 502 Bad Gateway (score 8.099): # checkout-api returns 502 Bad Gateway Use this runbook when the edge load balancer reports 502 responses for…

#### q24 (multi): Why did the double charge happen on a technical level?

- first relevant at rank 5; Recall@5 0.50
- not found: `rb-billing-duplicates`: “If the retry path builds a new idempotency key for each attempt, the payments gateway cannot tell the attempt…”
- returned #1 `rb-session-invalid` › Users get "Invalid session" after logging in > Narrowing down the cause (score 5.338): ## Narrowing down the cause 1. **Is it one region?** Compare the `session_validation_failures` metric per reg…
- returned #2 `pm-2026-06-payments` › Postmortem: card charges timing out in eu-west-1 > What went well (score 4.747): ## What went well - Failover was safe because every attempt carries an idempotency key. The reconciliation jo…
- returned #3 `rb-general-triage` › General incident triage and severity levels > Severity levels (score 4.354): ## Severity levels | Level | Definition | Response | |-------|------------|----------| | SEV1 | Customers can…

#### q25 (multi): Why did the February notification outage take so long to diagnose?

- first relevant at rank 2; Recall@5 0.50
- not found: `rb-notification-kafka`: “An expired broker certificate shows up in the client as NoBrokersAvailable, not as a TLS error.”
- returned #1 `rb-orders-db-pool` › orders-api database connection pool exhausted > Diagnose (score 7.522): ## Diagnose 1. Count connections by state on the primary: ``` SELECT state, count(*) FROM pg_stat_activity WH…
- returned #2 `pm-2026-02-kafka` › Postmortem: notifications stopped when the Kafka broker certificate expired > Why it took so long (score 6.530): ## Why it took so long The client error `NoBrokersAvailable` does not mention TLS. Responders spent almost tw…
- returned #3 `rb-notification-kafka` › notification-worker cannot reach Kafka > Escalation (score 5.538): ## Escalation Password reset emails travel through this worker. If the outage lasts more than 30 minutes, tel…

#### q28 (lookup): What follow-up actions came out of the March checkout 502 postmortem?

- no relevant chunk in the top 10; Recall@5 0.00
- not found: `pm-2026-03-checkout`: “Add a startup check that fails when the application keepalive is not longer than the sidecar's”
- returned #1 `pm-2026-03-checkout` › Postmortem: checkout-api 502 errors after release v2.31.0 > Summary (score 12.158): # Postmortem: checkout-api 502 errors after release v2.31.0 Date: 12 March 2026. Severity: SEV2. Duration: 38…
- returned #2 `rb-checkout-502` › checkout-api returns 502 Bad Gateway > First checks (five minutes) (score 8.447): ## First checks (five minutes) 1. Open the Deploys dashboard and look for a checkout-api release in the last …
- returned #3 `rb-checkout-502` › checkout-api returns 502 Bad Gateway > Likely causes and fixes > Payments circuit breaker open (score 8.120): ### Payments circuit breaker open When payments-gateway is slow, the circuit breaker in checkout-api opens af…

#### q29 (multi): What went wrong with clock drift during the German login incident?

- first relevant at rank 3; Recall@5 0.50
- not found: `rb-session-invalid`: “If an app node's clock drifts by more than 60 seconds the signature check rejects fresh cookies.”
- returned #1 `pm-2026-08-session` › Postmortem: German users logged out after Redis failover > Root cause (score 7.784): ## Root cause Two independent faults overlapped. The Redis failover lost sessions written in the last seconds…
- returned #2 `rb-dashboard-assets` › web-dashboard shows stale or wrong content (score 6.712): # web-dashboard shows stale or wrong content Use this runbook when users of the customer dashboard see old co…
- returned #3 `pm-2026-08-session` › Postmortem: German users logged out after Redis failover > What went badly (score 6.488): ## What went well - Comparing the metric per region quickly proved the problem was regional. ## What went bad…

### Misses: hybrid (6 of 30 questions)

Questions whose evidence is not all inside the top 5.

#### q10 (lookup): How often must we post updates during a SEV1 incident?

- first relevant at rank 8; Recall@5 0.00
- not found: `rb-general-triage`: “| SEV1 | Customers cannot pay, log in, or use the core product, or data is lost or exposed | Page immediately…”
- returned #1 `rb-general-triage` › General incident triage and severity levels > Communication (score 0.033): ## Communication - Post a customer-facing status update within 20 minutes of declaring SEV1 or SEV2. - State …
- returned #2 `rb-general-triage` › General incident triage and severity levels (score 0.030): # General incident triage and severity levels This runbook applies to every service. The first responder owns…
- returned #3 `rb-payments-latency` › payments-gateway latency and charge failures > Escalation (score 0.030): ## Escalation If more than 5% of charges fail for ten minutes, declare SEV1. Customers cannot pay, which is t…

#### q22 (procedure): How do I check whether checkout 502 errors come from a keepalive mismatch?

- first relevant at rank 3; Recall@5 0.50
- not found: `pm-2026-03-checkout`: “The release replaced the application server's default keepalive timeout of 120 seconds with an explicit value…”
- returned #1 `rb-checkout-502` › checkout-api returns 502 Bad Gateway (score 0.032): # checkout-api returns 502 Bad Gateway Use this runbook when the edge load balancer reports 502 responses for…
- returned #2 `pm-2026-03-checkout` › Postmortem: checkout-api 502 errors after release v2.31.0 > Summary (score 0.032): # Postmortem: checkout-api 502 errors after release v2.31.0 Date: 12 March 2026. Severity: SEV2. Duration: 38…
- returned #3 `rb-checkout-502` › checkout-api returns 502 Bad Gateway > Likely causes and fixes > Keepalive mismatch between nginx and the app (score 0.032): ### Keepalive mismatch between nginx and the app The nginx sidecar keeps upstream connections open for 75 sec…

#### q24 (multi): Why did the double charge happen on a technical level?

- first relevant at rank 2; Recall@5 0.50
- not found: `rb-billing-duplicates`: “If the retry path builds a new idempotency key for each attempt, the payments gateway cannot tell the attempt…”
- returned #1 `pm-2026-06-payments` › Postmortem: card charges timing out in eu-west-1 > What went well (score 0.033): ## What went well - Failover was safe because every attempt carries an idempotency key. The reconciliation jo…
- returned #2 `pm-2026-09-billing` › Postmortem: annual subscribers charged twice > Root cause (score 0.031): ## Root cause The new retry helper generated a fresh idempotency key for every attempt, built from the curren…
- returned #3 `rb-billing-duplicates` › billing-service charges customers twice > Find the duplicates (score 0.030): ## Find the duplicates Duplicates normally come from a charge being retried without the original idempotency …

#### q25 (multi): Why did the February notification outage take so long to diagnose?

- first relevant at rank 1; Recall@5 0.50
- not found: `rb-notification-kafka`: “An expired broker certificate shows up in the client as NoBrokersAvailable, not as a TLS error.”
- returned #1 `pm-2026-02-kafka` › Postmortem: notifications stopped when the Kafka broker certificate expired > Why it took so long (score 0.032): ## Why it took so long The client error `NoBrokersAvailable` does not mention TLS. Responders spent almost tw…
- returned #2 `pm-2026-02-kafka` › Postmortem: notifications stopped when the Kafka broker certificate expired (score 0.031): # Postmortem: notifications stopped when the Kafka broker certificate expired Date: 3 February 2026. Severity…
- returned #3 `pm-2026-06-payments` › Postmortem: card charges timing out in eu-west-1 > Root cause (score 0.031): ## Root cause The acquirer reported a database outage on their side. Our gateway had no circuit breaker aroun…

#### q28 (lookup): What follow-up actions came out of the March checkout 502 postmortem?

- first relevant at rank 6; Recall@5 0.00
- not found: `pm-2026-03-checkout`: “Add a startup check that fails when the application keepalive is not longer than the sidecar's”
- returned #1 `pm-2026-03-checkout` › Postmortem: checkout-api 502 errors after release v2.31.0 > Summary (score 0.032): # Postmortem: checkout-api 502 errors after release v2.31.0 Date: 12 March 2026. Severity: SEV2. Duration: 38…
- returned #2 `rb-checkout-502` › checkout-api returns 502 Bad Gateway > First checks (five minutes) (score 0.030): ## First checks (five minutes) 1. Open the Deploys dashboard and look for a checkout-api release in the last …
- returned #3 `pm-2026-03-checkout` › Postmortem: checkout-api 502 errors after release v2.31.0 > Timeline (UTC) (score 0.030): ## Timeline (UTC) - 14:02 v2.31.0 finished rolling out. - 14:09 The 502 rate alert fired at 3%. - 14:14 On-ca…

#### q29 (multi): What went wrong with clock drift during the German login incident?

- first relevant at rank 2; Recall@5 0.50
- not found: `rb-session-invalid`: “If an app node's clock drifts by more than 60 seconds the signature check rejects fresh cookies.”
- returned #1 `pm-2026-08-session` › Postmortem: German users logged out after Redis failover > Root cause (score 0.032): ## Root cause Two independent faults overlapped. The Redis failover lost sessions written in the last seconds…
- returned #2 `pm-2026-08-session` › Postmortem: German users logged out after Redis failover > What went badly (score 0.032): ## What went well - Comparing the metric per region quickly proved the problem was regional. ## What went bad…
- returned #3 `pm-2026-08-session` › Postmortem: German users logged out after Redis failover > Summary (score 0.031): # Postmortem: German users logged out after Redis failover Date: 18 August 2026. Severity: SEV2. Duration: 47…

### Misses: hybrid-rerank (2 of 30 questions)

Questions whose evidence is not all inside the top 5.

#### q24 (multi): Why did the double charge happen on a technical level?

- first relevant at rank 2; Recall@5 0.50
- not found: `rb-billing-duplicates`: “If the retry path builds a new idempotency key for each attempt, the payments gateway cannot tell the attempt…”
- returned #1 `pm-2026-06-payments` › Postmortem: card charges timing out in eu-west-1 > What went well (score -2.976): ## What went well - Failover was safe because every attempt carries an idempotency key. The reconciliation jo…
- returned #2 `pm-2026-09-billing` › Postmortem: annual subscribers charged twice > Root cause (score -4.554): ## Root cause The new retry helper generated a fresh idempotency key for every attempt, built from the curren…
- returned #3 `rb-payments-latency` › payments-gateway latency and charge failures > PAY-1042 acquirer timeouts (score -4.943): ## PAY-1042 acquirer timeouts A spike of PAY-1042 together with `StripeConnectionError` and a p99 near 9 seco…

#### q25 (multi): Why did the February notification outage take so long to diagnose?

- first relevant at rank 1; Recall@5 0.50
- not found: `rb-notification-kafka`: “An expired broker certificate shows up in the client as NoBrokersAvailable, not as a TLS error.”
- returned #1 `pm-2026-02-kafka` › Postmortem: notifications stopped when the Kafka broker certificate expired > Why it took so long (score -0.189): ## Why it took so long The client error `NoBrokersAvailable` does not mention TLS. Responders spent almost tw…
- returned #2 `pm-2026-02-kafka` › Postmortem: notifications stopped when the Kafka broker certificate expired (score -3.406): # Postmortem: notifications stopped when the Kafka broker certificate expired Date: 3 February 2026. Severity…
- returned #3 `rb-notification-kafka` › notification-worker cannot reach Kafka > Escalation (score -4.107): ## Escalation Password reset emails travel through this worker. If the outage lasts more than 30 minutes, tel…
