# Evaluation dashboard (2026-10-17)

Written by `python -m incidentops_ai.evals.gate`. Generation numbers come from the recorded real-model run; the rest are measured on every run of the gate.

**Gate: pass**

## Quality

| Layer | Metric | Value | Threshold |
|---|---|---|---|
| retrieval | recall_at_5 | 0.983 | 0.95 |
| retrieval | mrr_at_10 | 0.957 | 0.93 |
| workflow | routes_correct | 12 | 12 |
| security | sql_attacks_refused | 20 | 20 |
| security | sql_references_accepted | 15 | 15 |
| security | injection_recall | 1.0 | 1.0 |
| security | injection_false_positives | 0 | 0 |
| security | pii_recall | 1.0 | 1.0 |
| security | pii_false_positives | 0 | 0 |
| generation | answering_accuracy | 0.938 | 0.9 |
| generation | context_recall | 1.0 | 0.95 |
| generation | context_precision | 0.45 | – |
| generation | citation_precision | 1.0 | 0.95 |
| generation | judge_faithfulness | 0.615 | – |
| generation | judge_agreement | 0.769 | – |
| generation | model | gpt-6-luna | – |

## Cost and latency

| Source | Measure | Value |
|---|---|---|
| generation run | model calls | 16 |
| generation run | tokens in/out | 44,826/4,218 |
| generation run | cost (assumed price) | $0.154 |
| generation run | diagnosis p50 / max | 4086 ms / 6379 ms |
| workflow run (W07) | median end-to-end | 6.52 s |
| workflow run (W07) | cost per run (assumed) | $0.0190 |
