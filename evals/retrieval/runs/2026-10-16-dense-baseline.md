# Dense retrieval baseline, 2026-10-16

- Retriever: `dense-pgvector`, exact cosine scan, tenant-filtered
- Embedding model: `text-embedding-3-small`; chunking: `fixed-2000-200`
- Questions: 30; top 10; a chunk is relevant when it holds at least half of an evidence span
- Reproduce: `uv run python -m incidentops_ai.retrieval.benchmark`

## Results

| Metric | K=1 | K=3 | K=5 | K=10 |
|---|---|---|---|---|
| Recall | 0.800 | 0.983 | 0.983 | 1.000 |
| Precision | 0.933 | 0.433 | 0.260 | 0.133 |
| Hit rate | 0.933 | 1.000 | 1.000 | 1.000 |

**MRR@10: 0.961**

Precision is capped: most questions have one or two evidence spans, so Precision@5 cannot exceed about 0.4. Use it to compare configurations, not as an absolute score.

## Latency

Per question, query embedding plus database search.

mean 210 ms, p50 198 ms, p95 285 ms, max 315 ms

## By question style

| Style | n | Recall@5 | Precision@5 | MRR |
|---|---|---|---|---|
| identifier | 5 | 1.000 | 0.200 | 1.000 |
| lookup | 8 | 1.000 | 0.200 | 0.854 |
| multi | 5 | 0.900 | 0.320 | 1.000 |
| paraphrase | 8 | 1.000 | 0.300 | 1.000 |
| procedure | 4 | 1.000 | 0.300 | 1.000 |

## Service filter

Same retriever, restricted to the service named in the question where there is one.

| | Recall@5 | Precision@5 | MRR |
|---|---|---|---|
| no filter | 0.983 | 0.260 | 0.961 |
| service filter | 1.000 | 0.267 | 0.983 |

## Misses (1 of 30 questions)

Questions whose evidence is not all inside the top 5.

### q25 (multi): Why did the February notification outage take so long to diagnose?

- first relevant at rank 1; Recall@5 0.50
- not found: `rb-notification-kafka`: “An expired broker certificate shows up in the client as NoBrokersAvailable, not as a TLS error.”
- returned #1 `pm-2026-02-kafka` › Postmortem: notifications stopped when the Kafka broker certificate expired (score 0.458): # Postmortem: notifications stopped when the Kafka broker certificate expired Date: 3 February 2026. Severity…
- returned #2 `pm-2026-08-session` › Postmortem: German users logged out after Redis failover (score 0.430): # Postmortem: German users logged out after Redis failover Date: 18 August 2026. Severity: SEV2. Duration: 47…
- returned #3 `pm-2026-06-payments` › Postmortem: card charges timing out in eu-west-1 (score 0.426): # Postmortem: card charges timing out in eu-west-1 Date: 4 June 2026. Severity: SEV1. Duration: 26 minutes. A…
