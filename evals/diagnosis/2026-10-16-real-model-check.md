# Cited diagnosis: real-model check (AIENG-32)

Run on 16 Oct 2026. Five incidents sent to `POST /incidents/{id}/diagnosis` on a scratch database, with
the real embedding model and the real LLM. Three describe situations the corpus covers, two describe
situations it does not. This is a spot check of behaviour, not an evaluation: five cases, one run each.

| | |
|---|---|
| Stack | `hybrid` (dense pgvector + BM25, RRF), chunking `fixed-2000-200`, 23 chunks of the 18-document corpus |
| Embeddings | `text-embedding-3-small` |
| Model | `gpt-6-luna` (OpenAI), prompt `diagnose_incident/v1`, one attempt per call, no retries needed |
| Evidence | top 5 chunks per incident, numbered E1 to E5 |
| Repeat | ingest and index the corpus, start the API with `OPENAI_API_KEY`, `INCIDENTOPS_LLM_MODEL` and `INCIDENTOPS_API_TOKEN` set, create an incident, `POST /incidents/{id}/diagnosis` |

## Results

| Incident | Result | Causes shown / dropped | Retrieval | Model | Tokens in / out |
|---|---|---|---|---|---|
| Checkout 502s after a release | **answered** | 1 / 0 | 583 ms (first call: includes building the BM25 index) | 5.3 s | 2,792 / 258 |
| Orders API timeouts, pool exhausted | **answered** | 1 / 0 | 260 ms | 5.7 s | 3,011 / 245 |
| Password reset emails not arriving | **answered** | 1 / 0 | 225 ms | 8.4 s | 2,934 / 530 |
| Office printers crash on PDFs (off-corpus) | **no answer: insufficient evidence** | 0 / 0 | 309 ms | 2.4 s | 2,919 / 44 |
| Blurry hero image on iPhone (off-corpus) | **no answer: insufficient evidence** | 0 / 0 | 198 ms | 1.5 s | 2,884 / 38 |

Whole request: 1.7 to 8.7 s. The model call is 80 to 95% of it.

## What the answers looked like

- **Checkout 502s.** Cause: the release set the app's keepalive to 60 s while the nginx sidecar kept
  upstream connections for 75 s, so nginx reused connections the app had closed. Two citations, both to
  the March checkout postmortem (E2), characters 773 to 958 and 959 to 1115 of the document: "The release
  replaced the application server's default keepalive timeout of 120 seconds with an explicit value of 60
  seconds. The nginx sidecar keeps upstream connections for 75 seconds." and the sentence about 15 of every
  75 seconds. The claim follows from the quotes. The runbook (E1) was retrieved but not cited.
- **Orders API.** Cause: a newly deployed slow query may hold pool connections long enough to exhaust the
  pool. One citation to the orders-db-pool runbook: "A single slow `UPDATE orders` can make every other
  writer queue behind it and hold its pool connection while it waits." The claim is hedged ("may be"), as
  the report only says a slow query was deployed.
- **Password reset emails.** Cause: the worker cannot establish a connection to a Kafka broker, with DNS or
  reachability and TLS handshake failures as possibilities, and an expired broker certificate "a strong
  candidate given the matching prior outage, but the current report does not confirm it". Three
  citations: the runbook's sentence about resolving or reaching the brokers and the TLS handshake, and two
  sentences from the February postmortem (the certificate expired; the worker began logging
  `NoBrokersAvailable`).
- **Off-corpus, both.** Five irrelevant chunks were retrieved (checkout, search indexer, general triage,
  dashboard assets). The model answered `insufficient_evidence` with no causes, in 44 and 38 output
  tokens. Nothing was dropped, because nothing was proposed.

## What this does and does not show

- Every shown cause carries citations the server checked: the quote exists in the cited chunk, and the
  position in the document is ours. No cause was dropped in these five runs, so the **blocking** path was
  exercised only by the offline tests (fabricated id, quote from another chunk, wrong case, too short).
- **A citation proves the words exist, not that the claim follows.** The third answer joins a runbook fact
  to an inference from a past incident; it is flagged as unconfirmed in the claim itself, but nothing in the
  server could have caught it if it had not been. Claim-level checking belongs to the generation evals
  (W11, AIENG-64 and 65).
- **There is no relevance threshold.** Off-corpus incidents still retrieve five chunks and reach the model;
  they were refused by the model, not by the server. Two cases show it works with this model and prompt,
  not that it always will. A score gate needs a set of off-corpus incidents to calibrate against (W11).
- The first request after startup includes building the BM25 index (583 ms against 198 to 309 ms).
- Latency is one run each on one laptop with live API calls; treat it as an order of magnitude.
