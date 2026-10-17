# Workflow evaluation (2026-10-17)

Model `gpt-6-luna`, retrieval stack `hybrid-rrf`, 12 cases, each run
once with the concurrent layout and once with the sequential one (alternating order).

| | Concurrent | Sequential |
|---|---|---|
| Task success | 11/12 | 10/12 |
| Median end-to-end | 6.52 s | 9.85 s |

- Median reduction: **34%**. Same route and cited documents in both layouts: 8/12.
- 48 model calls, 96,466 input and 21,556 output tokens; estimate $0.46 at an assumed price ($2.5/M input, $10.0/M output). Attachment extraction at upload is not included.

| Case | Expected | Concurrent | Sequential | Cited (concurrent) | Success | Seconds |
|---|---|---|---|---|---|---|
| w01 | recommend | recommend | review (extraction_needs_review) | rb-notification-kafka, pm-2026-02-kafka | yes / no | 9.1 / 9.5 |
| w02 | recommend | recommend | recommend | pm-2026-03-checkout | yes / yes | 6.6 / 12.1 |
| w03 | review | review (extraction_needs_review, sev1) | review (sev1) | pm-2026-05-orders-pool | yes / yes | 6.2 / 8.6 |
| w04 | recommend | recommend | recommend | pm-2026-07-search | yes / yes | 6.7 / 16.3 |
| w05 | recommend | recommend | recommend | rb-session-invalid | yes / yes | 6.5 / 10.6 |
| w06 | review | review (sev1) | review (extraction_needs_review, sev1) | pm-2026-09-billing | yes / yes | 9.5 / 10.1 |
| w07 | recommend | review (extraction_needs_review) | recommend | pm-2026-06-payments | no / yes | 5.2 / 10.0 |
| w08 | recommend | recommend | review (extraction_needs_review) | rb-auth-proxy-cert | yes / no | 7.9 / 10.4 |
| w09 | recommend | recommend | recommend | rb-dashboard-assets | yes / yes | 5.4 / 9.2 |
| w10 | review | review (no_answer, extraction_needs_review) | review (no_answer, extraction_needs_review) | – | yes / yes | 7.9 / 9.7 |
| w11 | review | review (no_answer) | review (no_answer, extraction_needs_review) | – | yes / yes | 5.4 / 6.2 |
| w12 | review | review (no_answer, extraction_needs_review) | review (no_answer, extraction_needs_review) | – | yes / yes | 5.6 / 8.3 |

**Crash and resume** (w01): 2 model calls before the crash, **0 on resume**; the resumed run ended `recommended` (recommend).

**Overhead** (scripted models, 30 interleaved runs per variant):

| Variant | Median | p95 | Over the plain function |
|---|---|---|---|
| plain function | 9.9 ms | 11.9 ms | +0.0 ms |
| graph, no checkpointer | 16.3 ms | 18.5 ms | +6.4 ms |
| graph, in-memory checkpoints | 16.4 ms | 18.2 ms | +6.5 ms |
| graph, PostgreSQL checkpoints | 47.6 ms | 56.2 ms | +37.7 ms |
