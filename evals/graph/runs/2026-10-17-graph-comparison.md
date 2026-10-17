# Graph-assisted retrieval comparison, 2026-10-17

- Embedding model: `text-embedding-3-small`
- Questions: 12; top 10; a chunk is relevant when it holds at least half of an evidence span
- Reproduce: `uv run python -m incidentops_ai.graph.benchmark`
- Latency is end to end per question. Dense stages include one query-embedding call (live when the run used the API, so it reflects the network).
- Cost is an *estimate* of query-embedding API spend per 1,000 queries ($0.02 per million tokens, 4 characters per token). BM25 and a local reranker cost no API money.

## fixed-2000-200 · graph questions (decision config)

| Stage | Recall@1 | Recall@5 | Precision@5 | Hit@1 | MRR@10 | Chars read@5 | mean ms | p50 ms | p95 ms | est. $/1,000 queries |
|---|---|---|---|---|---|---|---|---|---|---|
| hybrid | 0.500 | 1.000 | 0.200 | 0.500 | 0.701 | 8557 | 395 | 199 | 2118 | 0.0007 |
| hard-filter | 0.167 | 0.333 | 0.067 | 0.167 | 0.250 | 3435 | 166 | 180 | 216 | 0.0007 |
| anchor | 0.250 | 1.000 | 0.200 | 0.250 | 0.506 | 8258 | 202 | 195 | 270 | 0.0014 |
| graph | 0.500 | 1.000 | 0.200 | 0.500 | 0.701 | 8072 | 195 | 194 | 205 | 0.0014 |
| graph-0hop | 0.250 | 1.000 | 0.200 | 0.250 | 0.506 | 8147 | 208 | 188 | 359 | 0.0014 |

### Questions moved

- hard-filter vs hybrid: improved 0; worse 8 (g01, g02, g03, g05, g06, g09, g11, g12)
- anchor vs hybrid: improved 0; worse 6 (g01, g02, g03, g06, g09, g11)
- graph vs hybrid: improved 0; worse 0
- graph vs anchor: improved 6 (g01, g02, g03, g06, g09, g11); worse 0
- graph-0hop vs anchor: improved 1 (g11); worse 1 (g12)

### MRR by question style

| Style | n | hybrid | hard-filter | anchor | graph | graph-0hop |
|---|---|---|---|---|---|---|
| dependency | 5 | 0.583 | 0.000 | 0.307 | 0.583 | 0.307 |
| dependent | 2 | 0.750 | 0.000 | 0.600 | 0.750 | 0.267 |
| history | 3 | 0.667 | 0.333 | 0.444 | 0.667 | 0.667 |
| same-service | 2 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |

### Misses: hybrid (0 of 12 questions)

Questions whose evidence is not all inside the top 5.

### Misses: hard-filter (8 of 12 questions)

Questions whose evidence is not all inside the top 5.

#### g01 (dependency): checkout-api pods keep failing readiness because the payments circuit breaker is open. How do I tell whether the payment problem is limited to one region?

- no relevant chunk in the top 10; Recall@5 0.00
- not found: `rb-payments-latency`: “Check whether only one region is affected using the `region` label on the charge latency panel.”
- returned #1 `rb-checkout-502` › checkout-api returns 502 Bad Gateway > Likely causes and fixes > Keepalive mismatch between nginx and the app (score 0.033): moment nginx reuses it, nginx returns a 502. The setting that must hold is: the application keepalive timeout…
- returned #2 `rb-checkout-502` › checkout-api returns 502 Bad Gateway (score 0.032): # checkout-api returns 502 Bad Gateway Use this runbook when the edge load balancer reports 502 responses for…
- returned #3 `pm-2026-03-checkout` › Postmortem: checkout-api 502 errors after release v2.31.0 (score 0.032): # Postmortem: checkout-api 502 errors after release v2.31.0 Date: 12 March 2026. Severity: SEV2. Duration: 38…

#### g02 (dependency): Placing an order from checkout-api hangs for 30 seconds and then fails, while browsing still works. What is the most common cause behind that?

- no relevant chunk in the top 10; Recall@5 0.00
- not found: `rb-orders-db-pool`: “A large number of `idle in transaction` sessions means the application opened a transaction and did not finis…”
- returned #1 `rb-checkout-502` › checkout-api returns 502 Bad Gateway (score 0.033): # checkout-api returns 502 Bad Gateway Use this runbook when the edge load balancer reports 502 responses for…
- returned #2 `pm-2026-03-checkout` › Postmortem: checkout-api 502 errors after release v2.31.0 (score 0.032): # Postmortem: checkout-api 502 errors after release v2.31.0 Date: 12 March 2026. Severity: SEV2. Duration: 38…
- returned #3 `rb-checkout-502` › checkout-api returns 502 Bad Gateway > Likely causes and fixes > Keepalive mismatch between nginx and the app (score 0.032): moment nginx reuses it, nginx returns a 502. The setting that must hold is: the application keepalive timeout…

#### g03 (dependency): orders-api cannot reach its primary database in eu-west-2. When are we allowed to switch to the standby?

- no relevant chunk in the top 10; Recall@5 0.00
- not found: `rb-orders-db-failover`: “Fail over only when the primary has been unreachable for more than 3 minutes AND replication lag on the stand…”
- returned #1 `pm-2026-05-orders-pool` › Postmortem: orders-api writes failing, connection pool exhausted (score 0.033): # Postmortem: orders-api writes failing, connection pool exhausted Date: 21 May 2026. Severity: SEV1. Duratio…
- returned #2 `rb-orders-db-pool` › orders-api database connection pool exhausted (score 0.033): # orders-api database connection pool exhausted Use this runbook when orders-api writes time out and the logs…
- returned #3 `rb-orders-db-pool` › orders-api database connection pool exhausted > Fix (score 0.032): in transaction mode in front of the primary, which the team is tracking. - If a deploy introduced the leak, r…

#### g05 (dependency): billing-service retries are rejected on the payment side with a code saying the key was reused. What does that code mean and how urgent is it?

- no relevant chunk in the top 10; Recall@5 0.00
- not found: `rb-payments-latency`: “| PAY-1017 | Idempotency key reused with different body | Client bug, ticket only |”
- returned #1 `pm-2026-09-billing` › Postmortem: annual subscribers charged twice (score 0.033): # Postmortem: annual subscribers charged twice Date: 11 to 12 September 2026. Severity: SEV1. Duration: 6 hou…
- returned #2 `rb-billing-duplicates` › billing-service charges customers twice (score 0.032): # billing-service charges customers twice Use this runbook when finance or Support report that customers were…

#### g06 (dependency): Dashboard users enter the right password and are sent straight back to the login page. Where is their session stored and checked?

- no relevant chunk in the top 10; Recall@5 0.00
- not found: `rb-session-invalid`: “A successful login creates a session record in the regional Redis cluster and returns a signed cookie.”
- returned #1 `rb-dashboard-assets` › web-dashboard shows stale or wrong content (score 0.033): # web-dashboard shows stale or wrong content Use this runbook when users of the customer dashboard see old co…

#### g09 (dependent): payments-gateway is seeing the same invoice charged twice by one of its callers, each attempt with a different key. Has a caller done this before, and what produced the keys?

- no relevant chunk in the top 10; Recall@5 0.00
- not found: `pm-2026-09-billing`: “The new retry helper generated a fresh idempotency key for every attempt, built from the current timestamp.”
- returned #1 `rb-payments-latency` › payments-gateway latency and charge failures (score 0.033): # payments-gateway latency and charge failures payments-gateway talks to two card acquirers and to Stripe. Us…
- returned #2 `rb-payments-latency` › payments-gateway latency and charge failures > Stripe unavailable (PAY-1050) (score 0.032): card-not-present payments to the secondary acquirer. It must be disabled again by hand when Stripe recovers, …
- returned #3 `pm-2026-06-payments` › Postmortem: card charges timing out in eu-west-1 (score 0.032): # Postmortem: card charges timing out in eu-west-1 Date: 4 June 2026. Severity: SEV1. Duration: 26 minutes. A…

#### g11 (history): The orders primary database ran out of connections during a write outage. Which database setting was changed afterwards?

- no relevant chunk in the top 10; Recall@5 0.00
- not found: `pm-2026-05-orders-pool`: “Set `idle_in_transaction_session_timeout` to 120 seconds on the orders database (done).”
- returned #1 `rb-orders-db-failover` › (no section) (score 0.033): all pods together exhausts the connection limit of the new primary. VERIFICATION AND FAILBACK Verification. A…
- returned #2 `rb-orders-db-failover` › (no section) (score 0.033): ORDERS DATABASE FAILOVER RUNBOOK Document owner: Database Reliability. Last reviewed: 2025-11-03 Exported fro…

#### g12 (dependent): Kafka consumer lag is growing after one of the consuming services restarted repeatedly. How quickly does the product search consumer catch up once its pods are stable?

- no relevant chunk in the top 10; Recall@5 0.00
- not found: `rb-search-indexer-oom`: “It catches up at about 2,000 documents per second, so a one hour outage clears in roughly ten minutes.”

### Misses: anchor (0 of 12 questions)

Questions whose evidence is not all inside the top 5.

### Misses: graph (0 of 12 questions)

Questions whose evidence is not all inside the top 5.

### Misses: graph-0hop (0 of 12 questions)

Questions whose evidence is not all inside the top 5.

## fixed-2000-200 · retrieval questions (decision config)

| Stage | Recall@1 | Recall@5 | Precision@5 | Hit@1 | MRR@10 | Chars read@5 | mean ms | p50 ms | p95 ms | est. $/1,000 queries |
|---|---|---|---|---|---|---|---|---|---|---|
| hybrid | 0.817 | 1.000 | 0.267 | 0.933 | 0.967 | 8384 | 182 | 179 | 220 | 0.0004 |
| hard-filter | 0.833 | 1.000 | 0.267 | 0.967 | 0.983 | 3726 | 184 | 181 | 209 | 0.0004 |
| anchor | 0.833 | 1.000 | 0.267 | 0.967 | 0.983 | 8250 | 192 | 189 | 251 | 0.0008 |
| graph | 0.817 | 1.000 | 0.267 | 0.933 | 0.967 | 8034 | 184 | 181 | 208 | 0.0008 |
| graph-0hop | 0.833 | 1.000 | 0.267 | 0.967 | 0.983 | 8146 | 204 | 193 | 293 | 0.0008 |

### Questions moved

- hard-filter vs hybrid: improved 1 (q24); worse 0
- anchor vs hybrid: improved 1 (q24); worse 0
- graph vs hybrid: improved 0; worse 0
- graph vs anchor: improved 0; worse 1 (q24)
- graph-0hop vs anchor: improved 0; worse 0

### MRR by question style

| Style | n | hybrid | hard-filter | anchor | graph | graph-0hop |
|---|---|---|---|---|---|---|
| identifier | 5 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| lookup | 8 | 0.938 | 0.938 | 0.938 | 0.938 | 0.938 |
| multi | 5 | 0.900 | 1.000 | 1.000 | 0.900 | 1.000 |
| paraphrase | 8 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| procedure | 4 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |

### Misses: hybrid (0 of 30 questions)

Questions whose evidence is not all inside the top 5.

### Misses: hard-filter (0 of 30 questions)

Questions whose evidence is not all inside the top 5.

### Misses: anchor (0 of 30 questions)

Questions whose evidence is not all inside the top 5.

### Misses: graph (0 of 30 questions)

Questions whose evidence is not all inside the top 5.

### Misses: graph-0hop (0 of 30 questions)

Questions whose evidence is not all inside the top 5.

## section-1500+ctx · graph questions (exploratory)

| Stage | Recall@1 | Recall@5 | Precision@5 | Hit@1 | MRR@10 | Chars read@5 | mean ms | p50 ms | p95 ms | est. $/1,000 queries |
|---|---|---|---|---|---|---|---|---|---|---|
| hybrid | 0.083 | 0.667 | 0.133 | 0.083 | 0.345 | 2343 | 181 | 174 | 234 | 0.0007 |
| hard-filter | 0.000 | 0.083 | 0.017 | 0.000 | 0.079 | 1803 | 167 | 175 | 248 | 0.0007 |
| anchor | 0.083 | 0.167 | 0.033 | 0.083 | 0.175 | 2101 | 196 | 194 | 221 | 0.0014 |
| graph | 0.083 | 0.667 | 0.133 | 0.083 | 0.346 | 2343 | 189 | 193 | 207 | 0.0014 |
| graph-0hop | 0.000 | 0.083 | 0.017 | 0.000 | 0.091 | 2096 | 194 | 192 | 229 | 0.0014 |

### Questions moved

- hard-filter vs hybrid: improved 1 (g04); worse 7 (g01, g02, g03, g05, g06, g09, g12)
- anchor vs hybrid: improved 1 (g04); worse 6 (g01, g02, g03, g05, g06, g09)
- graph vs hybrid: improved 1 (g04); worse 0
- graph vs anchor: improved 6 (g01, g02, g03, g05, g06, g09); worse 0
- graph-0hop vs anchor: improved 0; worse 1 (g12)

### MRR by question style

| Style | n | hybrid | hard-filter | anchor | graph | graph-0hop |
|---|---|---|---|---|---|---|
| dependency | 5 | 0.340 | 0.000 | 0.029 | 0.340 | 0.029 |
| dependent | 2 | 0.750 | 0.000 | 0.500 | 0.750 | 0.000 |
| history | 3 | 0.089 | 0.095 | 0.095 | 0.095 | 0.095 |
| same-service | 2 | 0.333 | 0.333 | 0.333 | 0.333 | 0.333 |

### Misses: hybrid (4 of 12 questions)

Questions whose evidence is not all inside the top 5.

#### g04 (history): notification-worker has stopped delivering messages again. What turned out to be the root cause the last time this happened?

- first relevant at rank 8; Recall@5 0.00
- not found: `pm-2026-02-kafka`: “The broker certificate was issued manually two years earlier and was not managed by cert-manager, so it did n…”
- returned #1 `rb-notification-kafka` › notification-worker cannot reach Kafka > Recovery (score 0.033): ## Recovery - Once connectivity is back, the consumer group `notification-workers` resumes from the last comm…
- returned #2 `rb-notification-kafka` › notification-worker cannot reach Kafka (score 0.033): # notification-worker cannot reach Kafka notification-worker consumes `notifications.requests` and sends emai…
- returned #3 `rb-notification-kafka` › notification-worker cannot reach Kafka > Escalation (score 0.031): ## Escalation Password reset emails travel through this worker. If the outage lasts more than 30 minutes, tel…

#### g07 (history): session-service rejects cookies it has only just signed, in a single region. Which past incident looked like this, and why did the problem only appear after a failover?

- first relevant at rank 7; Recall@5 0.00
- not found: `pm-2026-08-session`: “clock drift only became visible once traffic shifted onto those nodes after the failover.”
- returned #1 `rb-session-invalid` › Users get "Invalid session" after logging in > Narrowing down the cause (score 0.033): ## Narrowing down the cause 1. **Is it one region?** Compare the `session_validation_failures` metric per reg…
- returned #2 `pm-2026-08-session` › Postmortem: German users logged out after Redis failover > Timeline (CET) (score 0.032): ## Timeline (CET) - 07:03 The primary node of the eu-central Redis cluster failed its health check. - 07:04 A…
- returned #3 `rb-session-invalid` › Users get "Invalid session" after logging in > How sessions work (score 0.031): ## How sessions work A successful login creates a session record in the regional Redis cluster and returns a …

#### g10 (same-service): checkout-api returns 502s right after a release and someone wants to investigate payments-gateway first. How did that go during the March incident?

- first relevant at rank 6; Recall@5 0.00
- not found: `pm-2026-03-checkout`: “We lost 12 minutes looking at payments-gateway, which was healthy.”
- returned #1 `rb-checkout-502` › checkout-api returns 502 Bad Gateway > First checks (five minutes) (score 0.033): ## First checks (five minutes) 1. Open the Deploys dashboard and look for a checkout-api release in the last …
- returned #2 `rb-checkout-502` › checkout-api returns 502 Bad Gateway > Likely causes and fixes > Payments circuit breaker open (score 0.032): ### Payments circuit breaker open When payments-gateway is slow, the circuit breaker in checkout-api opens af…
- returned #3 `pm-2026-03-checkout` › Postmortem: checkout-api 502 errors after release v2.31.0 > Timeline (UTC) (score 0.031): ## Timeline (UTC) - 14:02 v2.31.0 finished rolling out. - 14:09 The 502 rate alert fired at 3%. - 14:14 On-ca…

#### g11 (history): The orders primary database ran out of connections during a write outage. Which database setting was changed afterwards?

- no relevant chunk in the top 10; Recall@5 0.00
- not found: `pm-2026-05-orders-pool`: “Set `idle_in_transaction_session_timeout` to 120 seconds on the orders database (done).”
- returned #1 `pm-2026-05-orders-pool` › Postmortem: orders-api writes failing, connection pool exhausted > Timeline (UTC) (score 0.032): ## Timeline (UTC) - 09:38 A new release of orders-api started rolling out. - 09:41 Order creation latency ros…
- returned #2 `rb-orders-db-failover` › (no section) (score 0.032): Step 4. Point the connection alias at the new primary: dbctl alias set orders-db-primary orders-db-standby. T…
- returned #3 `rb-orders-db-pool` › orders-api database connection pool exhausted > How the pool is configured (score 0.031): ## How the pool is configured orders-api opens at most 15 connections to `orders-db-primary` per pod: `pool_s…

### Misses: hard-filter (11 of 12 questions)

Questions whose evidence is not all inside the top 5.

#### g01 (dependency): checkout-api pods keep failing readiness because the payments circuit breaker is open. How do I tell whether the payment problem is limited to one region?

- no relevant chunk in the top 10; Recall@5 0.00
- not found: `rb-payments-latency`: “Check whether only one region is affected using the `region` label on the charge latency panel.”
- returned #1 `rb-checkout-502` › checkout-api returns 502 Bad Gateway > Likely causes and fixes > Payments circuit breaker open (score 0.033): ### Payments circuit breaker open When payments-gateway is slow, the circuit breaker in checkout-api opens af…
- returned #2 `rb-checkout-502` › checkout-api returns 502 Bad Gateway > First checks (five minutes) (score 0.032): ## First checks (five minutes) 1. Open the Deploys dashboard and look for a checkout-api release in the last …
- returned #3 `rb-checkout-502` › checkout-api returns 502 Bad Gateway > Symptoms (score 0.032): ## Symptoms - The load balancer dashboard shows `HTTPCode_ELB_502_Count` rising while checkout-api pods still…

#### g02 (dependency): Placing an order from checkout-api hangs for 30 seconds and then fails, while browsing still works. What is the most common cause behind that?

- no relevant chunk in the top 10; Recall@5 0.00
- not found: `rb-orders-db-pool`: “A large number of `idle in transaction` sessions means the application opened a transaction and did not finis…”
- returned #1 `rb-checkout-502` › checkout-api returns 502 Bad Gateway > Symptoms (score 0.032): ## Symptoms - The load balancer dashboard shows `HTTPCode_ELB_502_Count` rising while checkout-api pods still…
- returned #2 `rb-checkout-502` › checkout-api returns 502 Bad Gateway > First checks (five minutes) (score 0.032): ## First checks (five minutes) 1. Open the Deploys dashboard and look for a checkout-api release in the last …
- returned #3 `rb-checkout-502` › checkout-api returns 502 Bad Gateway > Likely causes and fixes > Payments circuit breaker open (score 0.031): ### Payments circuit breaker open When payments-gateway is slow, the circuit breaker in checkout-api opens af…

#### g03 (dependency): orders-api cannot reach its primary database in eu-west-2. When are we allowed to switch to the standby?

- no relevant chunk in the top 10; Recall@5 0.00
- not found: `rb-orders-db-failover`: “Fail over only when the primary has been unreachable for more than 3 minutes AND replication lag on the stand…”
- returned #1 `rb-orders-db-pool` › orders-api database connection pool exhausted > How the pool is configured (score 0.033): ## How the pool is configured orders-api opens at most 15 connections to `orders-db-primary` per pod: `pool_s…
- returned #2 `rb-orders-db-pool` › orders-api database connection pool exhausted (score 0.032): # orders-api database connection pool exhausted Use this runbook when orders-api writes time out and the logs…
- returned #3 `rb-orders-db-pool` › orders-api database connection pool exhausted > Fix (score 0.031): ## Fix - Terminate sessions that have been idle in transaction for more than 5 minutes with `SELECT pg_termin…

#### g04 (history): notification-worker has stopped delivering messages again. What turned out to be the root cause the last time this happened?

- first relevant at rank 7; Recall@5 0.00
- not found: `pm-2026-02-kafka`: “The broker certificate was issued manually two years earlier and was not managed by cert-manager, so it did n…”
- returned #1 `rb-notification-kafka` › notification-worker cannot reach Kafka > Recovery (score 0.033): ## Recovery - Once connectivity is back, the consumer group `notification-workers` resumes from the last comm…
- returned #2 `rb-notification-kafka` › notification-worker cannot reach Kafka (score 0.033): # notification-worker cannot reach Kafka notification-worker consumes `notifications.requests` and sends emai…
- returned #3 `rb-notification-kafka` › notification-worker cannot reach Kafka > Escalation (score 0.031): ## Escalation Password reset emails travel through this worker. If the outage lasts more than 30 minutes, tel…

#### g05 (dependency): billing-service retries are rejected on the payment side with a code saying the key was reused. What does that code mean and how urgent is it?

- no relevant chunk in the top 10; Recall@5 0.00
- not found: `rb-payments-latency`: “| PAY-1017 | Idempotency key reused with different body | Client bug, ticket only |”
- returned #1 `pm-2026-09-billing` › Postmortem: annual subscribers charged twice > Action items (score 0.032): ## Action items 1. Derive the key from invoice id and period, and add a test that retries reuse it. 2. Run th…
- returned #2 `rb-billing-duplicates` › billing-service charges customers twice > Fix the cause (score 0.032): ## Fix the cause - If the retry path builds a new idempotency key for each attempt, the payments gateway cann…
- returned #3 `pm-2026-09-billing` › Postmortem: annual subscribers charged twice > Root cause (score 0.032): ## Root cause The new retry helper generated a fresh idempotency key for every attempt, built from the curren…

#### g06 (dependency): Dashboard users enter the right password and are sent straight back to the login page. Where is their session stored and checked?

- no relevant chunk in the top 10; Recall@5 0.00
- not found: `rb-session-invalid`: “A successful login creates a session record in the regional Redis cluster and returns a signed cookie.”
- returned #1 `rb-dashboard-assets` › web-dashboard shows stale or wrong content (score 0.033): # web-dashboard shows stale or wrong content Use this runbook when users of the customer dashboard see old co…
- returned #2 `rb-dashboard-assets` › web-dashboard shows stale or wrong content > Why it happens (score 0.032): ## Why it happens web-dashboard is a static single page app served from object storage behind a CDN. Asset fi…
- returned #3 `rb-dashboard-assets` › web-dashboard shows stale or wrong content > Severity (score 0.032): ## Severity Cosmetic problems with a workaround are SEV4 under the general triage runbook. Do not page anyone…

#### g07 (history): session-service rejects cookies it has only just signed, in a single region. Which past incident looked like this, and why did the problem only appear after a failover?

- first relevant at rank 7; Recall@5 0.00
- not found: `pm-2026-08-session`: “clock drift only became visible once traffic shifted onto those nodes after the failover.”
- returned #1 `rb-session-invalid` › Users get "Invalid session" after logging in > Narrowing down the cause (score 0.033): ## Narrowing down the cause 1. **Is it one region?** Compare the `session_validation_failures` metric per reg…
- returned #2 `pm-2026-08-session` › Postmortem: German users logged out after Redis failover > Timeline (CET) (score 0.032): ## Timeline (CET) - 07:03 The primary node of the eu-central Redis cluster failed its health check. - 07:04 A…
- returned #3 `rb-session-invalid` › Users get "Invalid session" after logging in > How sessions work (score 0.031): ## How sessions work A successful login creates a session record in the regional Redis cluster and returns a …

#### g09 (dependent): payments-gateway is seeing the same invoice charged twice by one of its callers, each attempt with a different key. Has a caller done this before, and what produced the keys?

- no relevant chunk in the top 10; Recall@5 0.00
- not found: `pm-2026-09-billing`: “The new retry helper generated a fresh idempotency key for every attempt, built from the current timestamp.”
- returned #1 `rb-payments-latency` › payments-gateway latency and charge failures > Internal error codes (score 0.033): ## Internal error codes The gateway logs an internal code with every failed charge: | Code | Meaning | Action…
- returned #2 `rb-payments-latency` › payments-gateway latency and charge failures > PAY-1042 acquirer timeouts (score 0.032): ## PAY-1042 acquirer timeouts A spike of PAY-1042 together with `StripeConnectionError` and a p99 near 9 seco…
- returned #3 `rb-payments-latency` › payments-gateway latency and charge failures > Escalation (score 0.032): ## Escalation If more than 5% of charges fail for ten minutes, declare SEV1. Customers cannot pay, which is t…

#### g10 (same-service): checkout-api returns 502s right after a release and someone wants to investigate payments-gateway first. How did that go during the March incident?

- first relevant at rank 6; Recall@5 0.00
- not found: `pm-2026-03-checkout`: “We lost 12 minutes looking at payments-gateway, which was healthy.”
- returned #1 `rb-checkout-502` › checkout-api returns 502 Bad Gateway > First checks (five minutes) (score 0.033): ## First checks (five minutes) 1. Open the Deploys dashboard and look for a checkout-api release in the last …
- returned #2 `rb-checkout-502` › checkout-api returns 502 Bad Gateway > Likely causes and fixes > Payments circuit breaker open (score 0.032): ### Payments circuit breaker open When payments-gateway is slow, the circuit breaker in checkout-api opens af…
- returned #3 `pm-2026-03-checkout` › Postmortem: checkout-api 502 errors after release v2.31.0 > Timeline (UTC) (score 0.031): ## Timeline (UTC) - 14:02 v2.31.0 finished rolling out. - 14:09 The 502 rate alert fired at 3%. - 14:14 On-ca…

#### g11 (history): The orders primary database ran out of connections during a write outage. Which database setting was changed afterwards?

- no relevant chunk in the top 10; Recall@5 0.00
- not found: `pm-2026-05-orders-pool`: “Set `idle_in_transaction_session_timeout` to 120 seconds on the orders database (done).”
- returned #1 `rb-orders-db-failover` › (no section) (score 0.033): Step 4. Point the connection alias at the new primary: dbctl alias set orders-db-primary orders-db-standby. T…
- returned #2 `rb-orders-db-failover` › (no section) (score 0.033): ORDERS DATABASE FAILOVER RUNBOOK Document owner: Database Reliability. Last reviewed: 2025-11-03 Exported fro…

#### g12 (dependent): Kafka consumer lag is growing after one of the consuming services restarted repeatedly. How quickly does the product search consumer catch up once its pods are stable?

- no relevant chunk in the top 10; Recall@5 0.00
- not found: `rb-search-indexer-oom`: “It catches up at about 2,000 documents per second, so a one hour outage clears in roughly ten minutes.”

### Misses: anchor (10 of 12 questions)

Questions whose evidence is not all inside the top 5.

#### g01 (dependency): checkout-api pods keep failing readiness because the payments circuit breaker is open. How do I tell whether the payment problem is limited to one region?

- no relevant chunk in the top 10; Recall@5 0.00
- not found: `rb-payments-latency`: “Check whether only one region is affected using the `region` label on the charge latency panel.”
- returned #1 `rb-checkout-502` › checkout-api returns 502 Bad Gateway > Likely causes and fixes > Payments circuit breaker open (score 0.033): ### Payments circuit breaker open When payments-gateway is slow, the circuit breaker in checkout-api opens af…
- returned #2 `rb-checkout-502` › checkout-api returns 502 Bad Gateway > First checks (five minutes) (score 0.032): ## First checks (five minutes) 1. Open the Deploys dashboard and look for a checkout-api release in the last …
- returned #3 `rb-checkout-502` › checkout-api returns 502 Bad Gateway > Symptoms (score 0.032): ## Symptoms - The load balancer dashboard shows `HTTPCode_ELB_502_Count` rising while checkout-api pods still…

#### g02 (dependency): Placing an order from checkout-api hangs for 30 seconds and then fails, while browsing still works. What is the most common cause behind that?

- no relevant chunk in the top 10; Recall@5 0.00
- not found: `rb-orders-db-pool`: “A large number of `idle in transaction` sessions means the application opened a transaction and did not finis…”
- returned #1 `rb-checkout-502` › checkout-api returns 502 Bad Gateway > Symptoms (score 0.033): ## Symptoms - The load balancer dashboard shows `HTTPCode_ELB_502_Count` rising while checkout-api pods still…
- returned #2 `rb-checkout-502` › checkout-api returns 502 Bad Gateway > First checks (five minutes) (score 0.032): ## First checks (five minutes) 1. Open the Deploys dashboard and look for a checkout-api release in the last …
- returned #3 `rb-checkout-502` › checkout-api returns 502 Bad Gateway > Likely causes and fixes > Payments circuit breaker open (score 0.032): ### Payments circuit breaker open When payments-gateway is slow, the circuit breaker in checkout-api opens af…

#### g03 (dependency): orders-api cannot reach its primary database in eu-west-2. When are we allowed to switch to the standby?

- no relevant chunk in the top 10; Recall@5 0.00
- not found: `rb-orders-db-failover`: “Fail over only when the primary has been unreachable for more than 3 minutes AND replication lag on the stand…”
- returned #1 `rb-orders-db-pool` › orders-api database connection pool exhausted > How the pool is configured (score 0.032): ## How the pool is configured orders-api opens at most 15 connections to `orders-db-primary` per pod: `pool_s…
- returned #2 `rb-orders-db-pool` › orders-api database connection pool exhausted (score 0.032): # orders-api database connection pool exhausted Use this runbook when orders-api writes time out and the logs…
- returned #3 `rb-orders-db-pool` › orders-api database connection pool exhausted > Fix (score 0.031): ## Fix - Terminate sessions that have been idle in transaction for more than 5 minutes with `SELECT pg_termin…

#### g04 (history): notification-worker has stopped delivering messages again. What turned out to be the root cause the last time this happened?

- first relevant at rank 7; Recall@5 0.00
- not found: `pm-2026-02-kafka`: “The broker certificate was issued manually two years earlier and was not managed by cert-manager, so it did n…”
- returned #1 `rb-notification-kafka` › notification-worker cannot reach Kafka > Recovery (score 0.033): ## Recovery - Once connectivity is back, the consumer group `notification-workers` resumes from the last comm…
- returned #2 `rb-notification-kafka` › notification-worker cannot reach Kafka (score 0.032): # notification-worker cannot reach Kafka notification-worker consumes `notifications.requests` and sends emai…
- returned #3 `rb-notification-kafka` › notification-worker cannot reach Kafka > Escalation (score 0.032): ## Escalation Password reset emails travel through this worker. If the outage lasts more than 30 minutes, tel…

#### g05 (dependency): billing-service retries are rejected on the payment side with a code saying the key was reused. What does that code mean and how urgent is it?

- no relevant chunk in the top 10; Recall@5 0.00
- not found: `rb-payments-latency`: “| PAY-1017 | Idempotency key reused with different body | Client bug, ticket only |”
- returned #1 `rb-billing-duplicates` › billing-service charges customers twice > Fix the cause (score 0.033): ## Fix the cause - If the retry path builds a new idempotency key for each attempt, the payments gateway cann…
- returned #2 `pm-2026-09-billing` › Postmortem: annual subscribers charged twice > Action items (score 0.033): ## Action items 1. Derive the key from invoice id and period, and add a test that retries reuse it. 2. Run th…
- returned #3 `pm-2026-09-billing` › Postmortem: annual subscribers charged twice > Root cause (score 0.032): ## Root cause The new retry helper generated a fresh idempotency key for every attempt, built from the curren…

#### g06 (dependency): Dashboard users enter the right password and are sent straight back to the login page. Where is their session stored and checked?

- first relevant at rank 7; Recall@5 0.00
- not found: `rb-session-invalid`: “A successful login creates a session record in the regional Redis cluster and returns a signed cookie.”
- returned #1 `rb-dashboard-assets` › web-dashboard shows stale or wrong content (score 0.032): # web-dashboard shows stale or wrong content Use this runbook when users of the customer dashboard see old co…
- returned #2 `rb-dashboard-assets` › web-dashboard shows stale or wrong content > Why it happens (score 0.031): ## Why it happens web-dashboard is a static single page app served from object storage behind a CDN. Asset fi…
- returned #3 `rb-dashboard-assets` › web-dashboard shows stale or wrong content > Severity (score 0.030): ## Severity Cosmetic problems with a workaround are SEV4 under the general triage runbook. Do not page anyone…

#### g07 (history): session-service rejects cookies it has only just signed, in a single region. Which past incident looked like this, and why did the problem only appear after a failover?

- first relevant at rank 7; Recall@5 0.00
- not found: `pm-2026-08-session`: “clock drift only became visible once traffic shifted onto those nodes after the failover.”
- returned #1 `rb-session-invalid` › Users get "Invalid session" after logging in > Narrowing down the cause (score 0.033): ## Narrowing down the cause 1. **Is it one region?** Compare the `session_validation_failures` metric per reg…
- returned #2 `pm-2026-08-session` › Postmortem: German users logged out after Redis failover > Timeline (CET) (score 0.032): ## Timeline (CET) - 07:03 The primary node of the eu-central Redis cluster failed its health check. - 07:04 A…
- returned #3 `rb-session-invalid` › Users get "Invalid session" after logging in > How sessions work (score 0.032): ## How sessions work A successful login creates a session record in the regional Redis cluster and returns a …

#### g09 (dependent): payments-gateway is seeing the same invoice charged twice by one of its callers, each attempt with a different key. Has a caller done this before, and what produced the keys?

- no relevant chunk in the top 10; Recall@5 0.00
- not found: `pm-2026-09-billing`: “The new retry helper generated a fresh idempotency key for every attempt, built from the current timestamp.”
- returned #1 `rb-payments-latency` › payments-gateway latency and charge failures > Internal error codes (score 0.032): ## Internal error codes The gateway logs an internal code with every failed charge: | Code | Meaning | Action…
- returned #2 `rb-payments-latency` › payments-gateway latency and charge failures > PAY-1042 acquirer timeouts (score 0.031): ## PAY-1042 acquirer timeouts A spike of PAY-1042 together with `StripeConnectionError` and a p99 near 9 seco…
- returned #3 `rb-payments-latency` › payments-gateway latency and charge failures > Escalation (score 0.030): ## Escalation If more than 5% of charges fail for ten minutes, declare SEV1. Customers cannot pay, which is t…

#### g10 (same-service): checkout-api returns 502s right after a release and someone wants to investigate payments-gateway first. How did that go during the March incident?

- first relevant at rank 6; Recall@5 0.00
- not found: `pm-2026-03-checkout`: “We lost 12 minutes looking at payments-gateway, which was healthy.”
- returned #1 `rb-checkout-502` › checkout-api returns 502 Bad Gateway > First checks (five minutes) (score 0.033): ## First checks (five minutes) 1. Open the Deploys dashboard and look for a checkout-api release in the last …
- returned #2 `rb-checkout-502` › checkout-api returns 502 Bad Gateway > Likely causes and fixes > Payments circuit breaker open (score 0.032): ### Payments circuit breaker open When payments-gateway is slow, the circuit breaker in checkout-api opens af…
- returned #3 `pm-2026-03-checkout` › Postmortem: checkout-api 502 errors after release v2.31.0 > Timeline (UTC) (score 0.032): ## Timeline (UTC) - 14:02 v2.31.0 finished rolling out. - 14:09 The 502 rate alert fired at 3%. - 14:14 On-ca…

#### g11 (history): The orders primary database ran out of connections during a write outage. Which database setting was changed afterwards?

- no relevant chunk in the top 10; Recall@5 0.00
- not found: `pm-2026-05-orders-pool`: “Set `idle_in_transaction_session_timeout` to 120 seconds on the orders database (done).”
- returned #1 `rb-orders-db-failover` › (no section) (score 0.033): Step 4. Point the connection alias at the new primary: dbctl alias set orders-db-primary orders-db-standby. T…
- returned #2 `rb-orders-db-failover` › (no section) (score 0.032): ORDERS DATABASE FAILOVER RUNBOOK Document owner: Database Reliability. Last reviewed: 2025-11-03 Exported fro…
- returned #3 `pm-2026-05-orders-pool` › Postmortem: orders-api writes failing, connection pool exhausted > Timeline (UTC) (score 0.016): ## Timeline (UTC) - 09:38 A new release of orders-api started rolling out. - 09:41 Order creation latency ros…

### Misses: graph (4 of 12 questions)

Questions whose evidence is not all inside the top 5.

#### g04 (history): notification-worker has stopped delivering messages again. What turned out to be the root cause the last time this happened?

- first relevant at rank 7; Recall@5 0.00
- not found: `pm-2026-02-kafka`: “The broker certificate was issued manually two years earlier and was not managed by cert-manager, so it did n…”
- returned #1 `rb-notification-kafka` › notification-worker cannot reach Kafka > Recovery (score 0.033): ## Recovery - Once connectivity is back, the consumer group `notification-workers` resumes from the last comm…
- returned #2 `rb-notification-kafka` › notification-worker cannot reach Kafka (score 0.032): # notification-worker cannot reach Kafka notification-worker consumes `notifications.requests` and sends emai…
- returned #3 `rb-notification-kafka` › notification-worker cannot reach Kafka > Escalation (score 0.032): ## Escalation Password reset emails travel through this worker. If the outage lasts more than 30 minutes, tel…

#### g07 (history): session-service rejects cookies it has only just signed, in a single region. Which past incident looked like this, and why did the problem only appear after a failover?

- first relevant at rank 7; Recall@5 0.00
- not found: `pm-2026-08-session`: “clock drift only became visible once traffic shifted onto those nodes after the failover.”
- returned #1 `rb-session-invalid` › Users get "Invalid session" after logging in > Narrowing down the cause (score 0.033): ## Narrowing down the cause 1. **Is it one region?** Compare the `session_validation_failures` metric per reg…
- returned #2 `pm-2026-08-session` › Postmortem: German users logged out after Redis failover > Timeline (CET) (score 0.032): ## Timeline (CET) - 07:03 The primary node of the eu-central Redis cluster failed its health check. - 07:04 A…
- returned #3 `rb-session-invalid` › Users get "Invalid session" after logging in > How sessions work (score 0.032): ## How sessions work A successful login creates a session record in the regional Redis cluster and returns a …

#### g10 (same-service): checkout-api returns 502s right after a release and someone wants to investigate payments-gateway first. How did that go during the March incident?

- first relevant at rank 6; Recall@5 0.00
- not found: `pm-2026-03-checkout`: “We lost 12 minutes looking at payments-gateway, which was healthy.”
- returned #1 `rb-checkout-502` › checkout-api returns 502 Bad Gateway > First checks (five minutes) (score 0.033): ## First checks (five minutes) 1. Open the Deploys dashboard and look for a checkout-api release in the last …
- returned #2 `rb-checkout-502` › checkout-api returns 502 Bad Gateway > Likely causes and fixes > Payments circuit breaker open (score 0.032): ### Payments circuit breaker open When payments-gateway is slow, the circuit breaker in checkout-api opens af…
- returned #3 `pm-2026-03-checkout` › Postmortem: checkout-api 502 errors after release v2.31.0 > Timeline (UTC) (score 0.032): ## Timeline (UTC) - 14:02 v2.31.0 finished rolling out. - 14:09 The 502 rate alert fired at 3%. - 14:14 On-ca…

#### g11 (history): The orders primary database ran out of connections during a write outage. Which database setting was changed afterwards?

- no relevant chunk in the top 10; Recall@5 0.00
- not found: `pm-2026-05-orders-pool`: “Set `idle_in_transaction_session_timeout` to 120 seconds on the orders database (done).”
- returned #1 `pm-2026-05-orders-pool` › Postmortem: orders-api writes failing, connection pool exhausted > Timeline (UTC) (score 0.033): ## Timeline (UTC) - 09:38 A new release of orders-api started rolling out. - 09:41 Order creation latency ros…
- returned #2 `rb-orders-db-failover` › (no section) (score 0.032): Step 4. Point the connection alias at the new primary: dbctl alias set orders-db-primary orders-db-standby. T…
- returned #3 `rb-orders-db-pool` › orders-api database connection pool exhausted > How the pool is configured (score 0.032): ## How the pool is configured orders-api opens at most 15 connections to `orders-db-primary` per pod: `pool_s…

### Misses: graph-0hop (11 of 12 questions)

Questions whose evidence is not all inside the top 5.

#### g01 (dependency): checkout-api pods keep failing readiness because the payments circuit breaker is open. How do I tell whether the payment problem is limited to one region?

- no relevant chunk in the top 10; Recall@5 0.00
- not found: `rb-payments-latency`: “Check whether only one region is affected using the `region` label on the charge latency panel.”
- returned #1 `rb-checkout-502` › checkout-api returns 502 Bad Gateway > Likely causes and fixes > Payments circuit breaker open (score 0.033): ### Payments circuit breaker open When payments-gateway is slow, the circuit breaker in checkout-api opens af…
- returned #2 `rb-checkout-502` › checkout-api returns 502 Bad Gateway > First checks (five minutes) (score 0.032): ## First checks (five minutes) 1. Open the Deploys dashboard and look for a checkout-api release in the last …
- returned #3 `rb-checkout-502` › checkout-api returns 502 Bad Gateway > Symptoms (score 0.032): ## Symptoms - The load balancer dashboard shows `HTTPCode_ELB_502_Count` rising while checkout-api pods still…

#### g02 (dependency): Placing an order from checkout-api hangs for 30 seconds and then fails, while browsing still works. What is the most common cause behind that?

- no relevant chunk in the top 10; Recall@5 0.00
- not found: `rb-orders-db-pool`: “A large number of `idle in transaction` sessions means the application opened a transaction and did not finis…”
- returned #1 `rb-checkout-502` › checkout-api returns 502 Bad Gateway > Symptoms (score 0.033): ## Symptoms - The load balancer dashboard shows `HTTPCode_ELB_502_Count` rising while checkout-api pods still…
- returned #2 `rb-checkout-502` › checkout-api returns 502 Bad Gateway > First checks (five minutes) (score 0.032): ## First checks (five minutes) 1. Open the Deploys dashboard and look for a checkout-api release in the last …
- returned #3 `rb-checkout-502` › checkout-api returns 502 Bad Gateway > Likely causes and fixes > Payments circuit breaker open (score 0.032): ### Payments circuit breaker open When payments-gateway is slow, the circuit breaker in checkout-api opens af…

#### g03 (dependency): orders-api cannot reach its primary database in eu-west-2. When are we allowed to switch to the standby?

- no relevant chunk in the top 10; Recall@5 0.00
- not found: `rb-orders-db-failover`: “Fail over only when the primary has been unreachable for more than 3 minutes AND replication lag on the stand…”
- returned #1 `rb-orders-db-pool` › orders-api database connection pool exhausted > How the pool is configured (score 0.032): ## How the pool is configured orders-api opens at most 15 connections to `orders-db-primary` per pod: `pool_s…
- returned #2 `rb-orders-db-pool` › orders-api database connection pool exhausted (score 0.032): # orders-api database connection pool exhausted Use this runbook when orders-api writes time out and the logs…
- returned #3 `rb-orders-db-pool` › orders-api database connection pool exhausted > Fix (score 0.031): ## Fix - Terminate sessions that have been idle in transaction for more than 5 minutes with `SELECT pg_termin…

#### g04 (history): notification-worker has stopped delivering messages again. What turned out to be the root cause the last time this happened?

- first relevant at rank 7; Recall@5 0.00
- not found: `pm-2026-02-kafka`: “The broker certificate was issued manually two years earlier and was not managed by cert-manager, so it did n…”
- returned #1 `rb-notification-kafka` › notification-worker cannot reach Kafka > Recovery (score 0.033): ## Recovery - Once connectivity is back, the consumer group `notification-workers` resumes from the last comm…
- returned #2 `rb-notification-kafka` › notification-worker cannot reach Kafka (score 0.032): # notification-worker cannot reach Kafka notification-worker consumes `notifications.requests` and sends emai…
- returned #3 `rb-notification-kafka` › notification-worker cannot reach Kafka > Escalation (score 0.032): ## Escalation Password reset emails travel through this worker. If the outage lasts more than 30 minutes, tel…

#### g05 (dependency): billing-service retries are rejected on the payment side with a code saying the key was reused. What does that code mean and how urgent is it?

- no relevant chunk in the top 10; Recall@5 0.00
- not found: `rb-payments-latency`: “| PAY-1017 | Idempotency key reused with different body | Client bug, ticket only |”
- returned #1 `rb-billing-duplicates` › billing-service charges customers twice > Fix the cause (score 0.033): ## Fix the cause - If the retry path builds a new idempotency key for each attempt, the payments gateway cann…
- returned #2 `pm-2026-09-billing` › Postmortem: annual subscribers charged twice > Action items (score 0.033): ## Action items 1. Derive the key from invoice id and period, and add a test that retries reuse it. 2. Run th…
- returned #3 `pm-2026-09-billing` › Postmortem: annual subscribers charged twice > Root cause (score 0.032): ## Root cause The new retry helper generated a fresh idempotency key for every attempt, built from the curren…

#### g06 (dependency): Dashboard users enter the right password and are sent straight back to the login page. Where is their session stored and checked?

- first relevant at rank 7; Recall@5 0.00
- not found: `rb-session-invalid`: “A successful login creates a session record in the regional Redis cluster and returns a signed cookie.”
- returned #1 `rb-dashboard-assets` › web-dashboard shows stale or wrong content (score 0.032): # web-dashboard shows stale or wrong content Use this runbook when users of the customer dashboard see old co…
- returned #2 `rb-dashboard-assets` › web-dashboard shows stale or wrong content > Why it happens (score 0.031): ## Why it happens web-dashboard is a static single page app served from object storage behind a CDN. Asset fi…
- returned #3 `rb-dashboard-assets` › web-dashboard shows stale or wrong content > Severity (score 0.030): ## Severity Cosmetic problems with a workaround are SEV4 under the general triage runbook. Do not page anyone…

#### g07 (history): session-service rejects cookies it has only just signed, in a single region. Which past incident looked like this, and why did the problem only appear after a failover?

- first relevant at rank 7; Recall@5 0.00
- not found: `pm-2026-08-session`: “clock drift only became visible once traffic shifted onto those nodes after the failover.”
- returned #1 `rb-session-invalid` › Users get "Invalid session" after logging in > Narrowing down the cause (score 0.033): ## Narrowing down the cause 1. **Is it one region?** Compare the `session_validation_failures` metric per reg…
- returned #2 `pm-2026-08-session` › Postmortem: German users logged out after Redis failover > Timeline (CET) (score 0.032): ## Timeline (CET) - 07:03 The primary node of the eu-central Redis cluster failed its health check. - 07:04 A…
- returned #3 `rb-session-invalid` › Users get "Invalid session" after logging in > How sessions work (score 0.032): ## How sessions work A successful login creates a session record in the regional Redis cluster and returns a …

#### g09 (dependent): payments-gateway is seeing the same invoice charged twice by one of its callers, each attempt with a different key. Has a caller done this before, and what produced the keys?

- no relevant chunk in the top 10; Recall@5 0.00
- not found: `pm-2026-09-billing`: “The new retry helper generated a fresh idempotency key for every attempt, built from the current timestamp.”
- returned #1 `rb-payments-latency` › payments-gateway latency and charge failures > Internal error codes (score 0.032): ## Internal error codes The gateway logs an internal code with every failed charge: | Code | Meaning | Action…
- returned #2 `rb-payments-latency` › payments-gateway latency and charge failures > PAY-1042 acquirer timeouts (score 0.031): ## PAY-1042 acquirer timeouts A spike of PAY-1042 together with `StripeConnectionError` and a p99 near 9 seco…
- returned #3 `rb-payments-latency` › payments-gateway latency and charge failures > Escalation (score 0.030): ## Escalation If more than 5% of charges fail for ten minutes, declare SEV1. Customers cannot pay, which is t…

#### g10 (same-service): checkout-api returns 502s right after a release and someone wants to investigate payments-gateway first. How did that go during the March incident?

- first relevant at rank 6; Recall@5 0.00
- not found: `pm-2026-03-checkout`: “We lost 12 minutes looking at payments-gateway, which was healthy.”
- returned #1 `rb-checkout-502` › checkout-api returns 502 Bad Gateway > First checks (five minutes) (score 0.033): ## First checks (five minutes) 1. Open the Deploys dashboard and look for a checkout-api release in the last …
- returned #2 `rb-checkout-502` › checkout-api returns 502 Bad Gateway > Likely causes and fixes > Payments circuit breaker open (score 0.032): ### Payments circuit breaker open When payments-gateway is slow, the circuit breaker in checkout-api opens af…
- returned #3 `pm-2026-03-checkout` › Postmortem: checkout-api 502 errors after release v2.31.0 > Timeline (UTC) (score 0.032): ## Timeline (UTC) - 14:02 v2.31.0 finished rolling out. - 14:09 The 502 rate alert fired at 3%. - 14:14 On-ca…

#### g11 (history): The orders primary database ran out of connections during a write outage. Which database setting was changed afterwards?

- no relevant chunk in the top 10; Recall@5 0.00
- not found: `pm-2026-05-orders-pool`: “Set `idle_in_transaction_session_timeout` to 120 seconds on the orders database (done).”
- returned #1 `pm-2026-05-orders-pool` › Postmortem: orders-api writes failing, connection pool exhausted > Timeline (UTC) (score 0.033): ## Timeline (UTC) - 09:38 A new release of orders-api started rolling out. - 09:41 Order creation latency ros…
- returned #2 `rb-orders-db-failover` › (no section) (score 0.032): Step 4. Point the connection alias at the new primary: dbctl alias set orders-db-primary orders-db-standby. T…
- returned #3 `rb-orders-db-pool` › orders-api database connection pool exhausted > How the pool is configured (score 0.032): ## How the pool is configured orders-api opens at most 15 connections to `orders-db-primary` per pod: `pool_s…

#### g12 (dependent): Kafka consumer lag is growing after one of the consuming services restarted repeatedly. How quickly does the product search consumer catch up once its pods are stable?

- no relevant chunk in the top 10; Recall@5 0.00
- not found: `rb-search-indexer-oom`: “It catches up at about 2,000 documents per second, so a one hour outage clears in roughly ten minutes.”
- returned #1 `rb-notification-kafka` › notification-worker cannot reach Kafka > Recovery (score 0.032): ## Recovery - Once connectivity is back, the consumer group `notification-workers` resumes from the last comm…
- returned #2 `pm-2026-02-kafka` › Postmortem: notifications stopped when the Kafka broker certificate expired > Why it took so long (score 0.031): ## Why it took so long The client error `NoBrokersAvailable` does not mention TLS. Responders spent almost tw…
- returned #3 `rb-notification-kafka` › notification-worker cannot reach Kafka > Checks in order (score 0.030): ## Checks in order 1. From a worker pod run `nslookup kafka-bootstrap.messaging.svc` and `nc -zv kafka-bootst…

## section-1500+ctx · retrieval questions (exploratory)

| Stage | Recall@1 | Recall@5 | Precision@5 | Hit@1 | MRR@10 | Chars read@5 | mean ms | p50 ms | p95 ms | est. $/1,000 queries |
|---|---|---|---|---|---|---|---|---|---|---|
| hybrid | 0.739 | 0.950 | 0.240 | 0.867 | 0.918 | 2037 | 187 | 185 | 210 | 0.0004 |
| hard-filter | 0.756 | 0.950 | 0.240 | 0.900 | 0.934 | 1852 | 198 | 192 | 234 | 0.0004 |
| anchor | 0.756 | 0.950 | 0.240 | 0.900 | 0.934 | 1977 | 194 | 194 | 221 | 0.0008 |
| graph | 0.739 | 0.950 | 0.240 | 0.867 | 0.918 | 2059 | 192 | 192 | 218 | 0.0008 |
| graph-0hop | 0.756 | 0.950 | 0.240 | 0.900 | 0.934 | 1970 | 203 | 195 | 301 | 0.0008 |

### Questions moved

- hard-filter vs hybrid: improved 1 (q24); worse 0
- anchor vs hybrid: improved 1 (q24); worse 0
- graph vs hybrid: improved 0; worse 0
- graph vs anchor: improved 0; worse 1 (q24)
- graph-0hop vs anchor: improved 0; worse 0

### MRR by question style

| Style | n | hybrid | hard-filter | anchor | graph | graph-0hop |
|---|---|---|---|---|---|---|
| identifier | 5 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| lookup | 8 | 0.838 | 0.838 | 0.838 | 0.838 | 0.838 |
| multi | 5 | 0.900 | 1.000 | 1.000 | 0.900 | 1.000 |
| paraphrase | 8 | 0.917 | 0.917 | 0.917 | 0.917 | 0.917 |
| procedure | 4 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |

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

### Misses: hard-filter (3 of 30 questions)

Questions whose evidence is not all inside the top 5.

#### q24 (multi): Why did the double charge happen on a technical level?

- first relevant at rank 1; Recall@5 0.50
- not found: `rb-billing-duplicates`: “If the retry path builds a new idempotency key for each attempt, the payments gateway cannot tell the attempt…”
- returned #1 `pm-2026-09-billing` › Postmortem: annual subscribers charged twice > Root cause (score 0.032): ## Root cause The new retry helper generated a fresh idempotency key for every attempt, built from the curren…
- returned #2 `rb-billing-duplicates` › billing-service charges customers twice (score 0.032): # billing-service charges customers twice Use this runbook when finance or Support report that customers were…
- returned #3 `rb-billing-duplicates` › billing-service charges customers twice > Find the duplicates (score 0.032): ## Find the duplicates Duplicates normally come from a charge being retried without the original idempotency …

#### q25 (multi): Why did the February notification outage take so long to diagnose?

- first relevant at rank 1; Recall@5 0.50
- not found: `rb-notification-kafka`: “An expired broker certificate shows up in the client as NoBrokersAvailable, not as a TLS error.”
- returned #1 `pm-2026-02-kafka` › Postmortem: notifications stopped when the Kafka broker certificate expired > Why it took so long (score 0.033): ## Why it took so long The client error `NoBrokersAvailable` does not mention TLS. Responders spent almost tw…
- returned #2 `pm-2026-02-kafka` › Postmortem: notifications stopped when the Kafka broker certificate expired (score 0.032): # Postmortem: notifications stopped when the Kafka broker certificate expired Date: 3 February 2026. Severity…
- returned #3 `rb-notification-kafka` › notification-worker cannot reach Kafka > Escalation (score 0.031): ## Escalation Password reset emails travel through this worker. If the outage lasts more than 30 minutes, tel…

#### q29 (multi): What went wrong with clock drift during the German login incident?

- first relevant at rank 1; Recall@5 0.50
- not found: `rb-session-invalid`: “If an app node's clock drifts by more than 60 seconds the signature check rejects fresh cookies.”
- returned #1 `pm-2026-08-session` › Postmortem: German users logged out after Redis failover > What went badly (score 0.033): ## What went well - Comparing the metric per region quickly proved the problem was regional. ## What went bad…
- returned #2 `pm-2026-08-session` › Postmortem: German users logged out after Redis failover > Root cause (score 0.033): ## Root cause Two independent faults overlapped. The Redis failover lost sessions written in the last seconds…
- returned #3 `pm-2026-08-session` › Postmortem: German users logged out after Redis failover > Timeline (CET) (score 0.031): ## Timeline (CET) - 07:03 The primary node of the eu-central Redis cluster failed its health check. - 07:04 A…

### Misses: anchor (3 of 30 questions)

Questions whose evidence is not all inside the top 5.

#### q24 (multi): Why did the double charge happen on a technical level?

- first relevant at rank 1; Recall@5 0.50
- not found: `rb-billing-duplicates`: “If the retry path builds a new idempotency key for each attempt, the payments gateway cannot tell the attempt…”
- returned #1 `pm-2026-09-billing` › Postmortem: annual subscribers charged twice > Root cause (score 0.033): ## Root cause The new retry helper generated a fresh idempotency key for every attempt, built from the curren…
- returned #2 `rb-billing-duplicates` › billing-service charges customers twice (score 0.032): # billing-service charges customers twice Use this runbook when finance or Support report that customers were…
- returned #3 `rb-billing-duplicates` › billing-service charges customers twice > Find the duplicates (score 0.031): ## Find the duplicates Duplicates normally come from a charge being retried without the original idempotency …

#### q25 (multi): Why did the February notification outage take so long to diagnose?

- first relevant at rank 1; Recall@5 0.50
- not found: `rb-notification-kafka`: “An expired broker certificate shows up in the client as NoBrokersAvailable, not as a TLS error.”
- returned #1 `pm-2026-02-kafka` › Postmortem: notifications stopped when the Kafka broker certificate expired > Why it took so long (score 0.033): ## Why it took so long The client error `NoBrokersAvailable` does not mention TLS. Responders spent almost tw…
- returned #2 `pm-2026-02-kafka` › Postmortem: notifications stopped when the Kafka broker certificate expired (score 0.032): # Postmortem: notifications stopped when the Kafka broker certificate expired Date: 3 February 2026. Severity…
- returned #3 `rb-notification-kafka` › notification-worker cannot reach Kafka > Escalation (score 0.031): ## Escalation Password reset emails travel through this worker. If the outage lasts more than 30 minutes, tel…

#### q29 (multi): What went wrong with clock drift during the German login incident?

- first relevant at rank 1; Recall@5 0.50
- not found: `rb-session-invalid`: “If an app node's clock drifts by more than 60 seconds the signature check rejects fresh cookies.”
- returned #1 `pm-2026-08-session` › Postmortem: German users logged out after Redis failover > What went badly (score 0.033): ## What went well - Comparing the metric per region quickly proved the problem was regional. ## What went bad…
- returned #2 `pm-2026-08-session` › Postmortem: German users logged out after Redis failover > Root cause (score 0.032): ## Root cause Two independent faults overlapped. The Redis failover lost sessions written in the last seconds…
- returned #3 `pm-2026-08-session` › Postmortem: German users logged out after Redis failover > Timeline (CET) (score 0.032): ## Timeline (CET) - 07:03 The primary node of the eu-central Redis cluster failed its health check. - 07:04 A…

### Misses: graph (3 of 30 questions)

Questions whose evidence is not all inside the top 5.

#### q24 (multi): Why did the double charge happen on a technical level?

- first relevant at rank 2; Recall@5 0.50
- not found: `rb-billing-duplicates`: “If the retry path builds a new idempotency key for each attempt, the payments gateway cannot tell the attempt…”
- returned #1 `pm-2026-06-payments` › Postmortem: card charges timing out in eu-west-1 > What went well (score 0.033): ## What went well - Failover was safe because every attempt carries an idempotency key. The reconciliation jo…
- returned #2 `pm-2026-09-billing` › Postmortem: annual subscribers charged twice > Root cause (score 0.032): ## Root cause The new retry helper generated a fresh idempotency key for every attempt, built from the curren…
- returned #3 `rb-billing-duplicates` › billing-service charges customers twice (score 0.032): # billing-service charges customers twice Use this runbook when finance or Support report that customers were…

#### q25 (multi): Why did the February notification outage take so long to diagnose?

- first relevant at rank 1; Recall@5 0.50
- not found: `rb-notification-kafka`: “An expired broker certificate shows up in the client as NoBrokersAvailable, not as a TLS error.”
- returned #1 `pm-2026-02-kafka` › Postmortem: notifications stopped when the Kafka broker certificate expired > Why it took so long (score 0.033): ## Why it took so long The client error `NoBrokersAvailable` does not mention TLS. Responders spent almost tw…
- returned #2 `pm-2026-02-kafka` › Postmortem: notifications stopped when the Kafka broker certificate expired (score 0.032): # Postmortem: notifications stopped when the Kafka broker certificate expired Date: 3 February 2026. Severity…
- returned #3 `rb-notification-kafka` › notification-worker cannot reach Kafka > Escalation (score 0.031): ## Escalation Password reset emails travel through this worker. If the outage lasts more than 30 minutes, tel…

#### q29 (multi): What went wrong with clock drift during the German login incident?

- first relevant at rank 1; Recall@5 0.50
- not found: `rb-session-invalid`: “If an app node's clock drifts by more than 60 seconds the signature check rejects fresh cookies.”
- returned #1 `pm-2026-08-session` › Postmortem: German users logged out after Redis failover > What went badly (score 0.033): ## What went well - Comparing the metric per region quickly proved the problem was regional. ## What went bad…
- returned #2 `pm-2026-08-session` › Postmortem: German users logged out after Redis failover > Root cause (score 0.032): ## Root cause Two independent faults overlapped. The Redis failover lost sessions written in the last seconds…
- returned #3 `pm-2026-08-session` › Postmortem: German users logged out after Redis failover > Timeline (CET) (score 0.032): ## Timeline (CET) - 07:03 The primary node of the eu-central Redis cluster failed its health check. - 07:04 A…

### Misses: graph-0hop (3 of 30 questions)

Questions whose evidence is not all inside the top 5.

#### q24 (multi): Why did the double charge happen on a technical level?

- first relevant at rank 1; Recall@5 0.50
- not found: `rb-billing-duplicates`: “If the retry path builds a new idempotency key for each attempt, the payments gateway cannot tell the attempt…”
- returned #1 `pm-2026-09-billing` › Postmortem: annual subscribers charged twice > Root cause (score 0.033): ## Root cause The new retry helper generated a fresh idempotency key for every attempt, built from the curren…
- returned #2 `rb-billing-duplicates` › billing-service charges customers twice (score 0.032): # billing-service charges customers twice Use this runbook when finance or Support report that customers were…
- returned #3 `rb-billing-duplicates` › billing-service charges customers twice > Find the duplicates (score 0.031): ## Find the duplicates Duplicates normally come from a charge being retried without the original idempotency …

#### q25 (multi): Why did the February notification outage take so long to diagnose?

- first relevant at rank 1; Recall@5 0.50
- not found: `rb-notification-kafka`: “An expired broker certificate shows up in the client as NoBrokersAvailable, not as a TLS error.”
- returned #1 `pm-2026-02-kafka` › Postmortem: notifications stopped when the Kafka broker certificate expired > Why it took so long (score 0.033): ## Why it took so long The client error `NoBrokersAvailable` does not mention TLS. Responders spent almost tw…
- returned #2 `pm-2026-02-kafka` › Postmortem: notifications stopped when the Kafka broker certificate expired (score 0.032): # Postmortem: notifications stopped when the Kafka broker certificate expired Date: 3 February 2026. Severity…
- returned #3 `rb-notification-kafka` › notification-worker cannot reach Kafka > Escalation (score 0.031): ## Escalation Password reset emails travel through this worker. If the outage lasts more than 30 minutes, tel…

#### q29 (multi): What went wrong with clock drift during the German login incident?

- first relevant at rank 1; Recall@5 0.50
- not found: `rb-session-invalid`: “If an app node's clock drifts by more than 60 seconds the signature check rejects fresh cookies.”
- returned #1 `pm-2026-08-session` › Postmortem: German users logged out after Redis failover > What went badly (score 0.033): ## What went well - Comparing the metric per region quickly proved the problem was regional. ## What went bad…
- returned #2 `pm-2026-08-session` › Postmortem: German users logged out after Redis failover > Root cause (score 0.032): ## Root cause Two independent faults overlapped. The Redis failover lost sessions written in the last seconds…
- returned #3 `pm-2026-08-session` › Postmortem: German users logged out after Redis failover > Timeline (CET) (score 0.032): ## Timeline (CET) - 07:03 The primary node of the eu-central Redis cluster failed its health check. - 07:04 A…

## Decision

By the rule fixed in `docs/roadmap-w05.md`: a stage replaces the current one if MRR on the graph set rises by at least 0.04 without lowering Recall@5, and MRR on the 30 retrieval questions falls by no more than 0.02; on section-1500+ctx the adopted stage may not lose more than 0.02 against hybrid.

Adopted: **hybrid**

- anchor vs hybrid: graph-set MRR 0.701 → 0.506 (-0.196, needs +0.04), Recall@5 1.000 → 1.000, 30-question MRR 0.967 → 0.983 (may fall by 0.02): not adopted
- graph vs hybrid: graph-set MRR 0.701 → 0.701 (+0.000, needs +0.04), Recall@5 1.000 → 1.000, 30-question MRR 0.967 → 0.967 (may fall by 0.02): not adopted
