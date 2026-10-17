# Multimodal extraction benchmark, 2026-10-17

- Model: `gpt-6-luna` (vision and text answers); OCR: RapidOCR, local
- 14 assets, 28 questions; an answer is correct when it holds every expected term
- Reproduce: `uv run --extra ocr python -m incidentops_ai.multimodal.benchmark`
- Cost is an *estimate* at an assumed price ($2.5 per million input tokens, $10.0 per million output tokens); OCR and PDF text extraction cost no API money.

## By kind

| Kind | Path | Correct | Text holds the answer | Extraction ms (mean) | Tokens in / out | est. $ |
|---|---|---|---|---|---|---|
| screenshot | ocr | **7/8** | 7/8 | 425 | 1,677 / 254 | 0.0067 |
| screenshot | vlm-transcript | **8/8** | 8/8 | 2681 | 4,856 / 1,042 | 0.0226 |
| screenshot | vlm-direct | **8/8** | n/a | - | 4,637 / 222 | 0.0138 |
| chart | ocr | **1/6** | 6/6 | 198 | 1,006 / 1,057 | 0.0131 |
| chart | vlm-transcript | **6/6** | 6/6 | 3465 | 4,302 / 1,172 | 0.0225 |
| chart | vlm-direct | **6/6** | n/a | - | 4,862 / 178 | 0.0139 |
| diagram | ocr | **2/6** | 5/6 | 176 | 924 / 654 | 0.0089 |
| diagram | vlm-transcript | **6/6** | 6/6 | 2345 | 4,171 / 803 | 0.0185 |
| diagram | vlm-direct | **6/6** | n/a | - | 4,962 / 195 | 0.0144 |
| pdf-text | pdf-text | **4/4** | 4/4 | 37 | 829 / 55 | 0.0026 |
| pdf-text | ocr | **4/4** | 4/4 | 414 | 869 / 72 | 0.0029 |
| pdf-text | vlm-transcript | **4/4** | 4/4 | 3050 | 6,299 / 540 | 0.0211 |
| pdf-text | vlm-direct | **4/4** | n/a | - | 10,147 / 55 | 0.0259 |
| pdf-scanned | pdf-text | **0/4** | 0/4 | 1 | 0 / 0 | 0.0000 |
| pdf-scanned | ocr | **4/4** | 4/4 | 295 | 700 / 389 | 0.0056 |
| pdf-scanned | vlm-transcript | **4/4** | 4/4 | 2372 | 6,148 / 495 | 0.0203 |
| pdf-scanned | vlm-direct | **4/4** | n/a | - | 10,138 / 118 | 0.0265 |

## Decision

By the rule fixed in `docs/roadmap-w06.md`: per kind, the cheapest citable path (`pdf-text` < `ocr` < `vlm-transcript`) unless a costlier one answers at least one more question. `vlm-direct` is the ceiling and cannot be cited.

| Kind | Chosen path |
|---|---|
| screenshot | **vlm-transcript** |
| chart | **vlm-transcript** |
| diagram | **vlm-transcript** |
| pdf-text | **pdf-text** |
| pdf-scanned | **ocr** |

## Router

The automatic router (text layer ≥ 200 characters per page → `pdf-text`; OCR boxes covering < 8% of the image → `vlm-transcript`; otherwise `ocr`) agrees with the per-kind choice on **9 of 14** assets.

| Asset | Kind | OCR coverage | Router | Per-kind choice |
|---|---|---|---|---|
| s01 | screenshot | 0.258 | ocr ✗ | vlm-transcript |
| s02 | screenshot | 0.145 | ocr ✗ | vlm-transcript |
| s03 | screenshot | 0.059 | vlm-transcript | vlm-transcript |
| s04 | screenshot | 0.117 | ocr ✗ | vlm-transcript |
| c01 | chart | 0.039 | vlm-transcript | vlm-transcript |
| c02 | chart | 0.034 | vlm-transcript | vlm-transcript |
| c03 | chart | 0.039 | vlm-transcript | vlm-transcript |
| d01 | diagram | 0.040 | vlm-transcript | vlm-transcript |
| d02 | diagram | 0.032 | vlm-transcript | vlm-transcript |
| d03 | diagram | 0.036 | vlm-transcript | vlm-transcript |
| p01 | pdf-text | 0.049 | pdf-text | pdf-text |
| p02 | pdf-text | 0.057 | pdf-text | pdf-text |
| p03 | pdf-scanned | 0.027 | vlm-transcript ✗ | ocr |
| p04 | pdf-scanned | 0.039 | vlm-transcript ✗ | ocr |

## Every answer

| Question | Path | Correct | In text | Answer |
|---|---|---|---|---|
| c01-q1 | ocr | ✗ | yes | us-east-1 |
| c01-q1 | vlm-transcript | ✓ | yes | eu-west-1 |
| c01-q1 | vlm-direct | ✓ | - | eu-west-1 |
| c01-q2 | ocr | ✗ | yes | 10 seconds |
| c01-q2 | vlm-transcript | ✓ | yes | 9 seconds |
| c01-q2 | vlm-direct | ✓ | - | About 9 seconds |
| c02-q1 | ocr | ✗ | yes | unknown |
| c02-q1 | vlm-transcript | ✓ | yes | checkout-api |
| c02-q1 | vlm-direct | ✓ | - | checkout-api |
| c02-q2 | ocr | ✗ | yes | unknown |
| c02-q2 | vlm-transcript | ✓ | yes | session-service |
| c02-q2 | vlm-direct | ✓ | - | session-service |
| c03-q1 | ocr | ✗ | yes | idle |
| c03-q1 | vlm-transcript | ✓ | yes | idle in transaction |
| c03-q1 | vlm-direct | ✓ | - | idle in transaction |
| c03-q2 | ocr | ✓ | yes | idle in transaction |
| c03-q2 | vlm-transcript | ✓ | yes | idle |
| c03-q2 | vlm-direct | ✓ | - | idle |
| d01-q1 | ocr | ✗ | no | unknown |
| d01-q1 | vlm-transcript | ✓ | yes | payments-gateway and billing-service |
| d01-q1 | vlm-direct | ✓ | - | payments-gateway and billing-service |
| d01-q2 | ocr | ✗ | yes | unknown |
| d01-q2 | vlm-transcript | ✓ | yes | promo-engine |
| d01-q2 | vlm-direct | ✓ | - | promo-engine |
| d02-q1 | ocr | ✗ | yes | unknown |
| d02-q1 | vlm-transcript | ✓ | yes | orders-db-primary |
| d02-q1 | vlm-direct | ✓ | - | orders-db-primary |
| d02-q2 | ocr | ✗ | yes | unknown |
| d02-q2 | vlm-transcript | ✓ | yes | orders-db-standby |
| d02-q2 | vlm-direct | ✓ | - | orders-db-standby |
| d03-q1 | ocr | ✓ | yes | CDN |
| d03-q1 | vlm-transcript | ✓ | yes | CDN |
| d03-q1 | vlm-direct | ✓ | - | CDN |
| d03-q2 | ocr | ✓ | yes | admin-tools |
| d03-q2 | vlm-transcript | ✓ | yes | admin-tools |
| d03-q2 | vlm-direct | ✓ | - | admin-tools |
| p01-q1 | pdf-text | ✓ | yes | Tom Hughes |
| p01-q1 | ocr | ✓ | yes | Tom Hughes |
| p01-q1 | vlm-transcript | ✓ | yes | Tom Hughes |
| p01-q1 | vlm-direct | ✓ | - | Tom Hughes |
| p01-q2 | pdf-text | ✓ | yes | Priya Nair |
| p01-q2 | ocr | ✓ | yes | Priya Nair |
| p01-q2 | vlm-transcript | ✓ | yes | Priya Nair |
| p01-q2 | vlm-direct | ✓ | - | Priya Nair |
| p02-q1 | pdf-text | ✓ | yes | 30 minutes |
| p02-q1 | ocr | ✓ | yes | 30 minutes |
| p02-q1 | vlm-transcript | ✓ | yes | 30 minutes |
| p02-q1 | vlm-direct | ✓ | - | 30 minutes |
| p02-q2 | pdf-text | ✓ | yes | 5 working days |
| p02-q2 | ocr | ✓ | yes | 5 working days |
| p02-q2 | vlm-transcript | ✓ | yes | 5 working days |
| p02-q2 | vlm-direct | ✓ | - | 5 working days |
| p03-q1 | pdf-text | ✗ | no | unknown |
| p03-q1 | ocr | ✓ | yes | 120 orders/s |
| p03-q1 | vlm-transcript | ✓ | yes | 120 orders/s |
| p03-q1 | vlm-direct | ✓ | - | 120 orders/s |
| p03-q2 | pdf-text | ✗ | no | unknown |
| p03-q2 | ocr | ✓ | yes | 22 pods |
| p03-q2 | vlm-transcript | ✓ | yes | 22 pods |
| p03-q2 | vlm-direct | ✓ | - | 22 pods |
| p04-q1 | pdf-text | ✗ | no | unknown |
| p04-q1 | ocr | ✓ | yes | Sun 02:00-04:00 UTC |
| p04-q1 | vlm-transcript | ✓ | yes | Sun 02:00-04:00 UTC |
| p04-q1 | vlm-direct | ✓ | - | Sunday 02:00–04:00 UTC |
| p04-q2 | pdf-text | ✗ | no | unknown |
| p04-q2 | ocr | ✓ | yes | Messaging |
| p04-q2 | vlm-transcript | ✓ | yes | Messaging |
| p04-q2 | vlm-direct | ✓ | - | Messaging |
| s01-q1 | ocr | ✓ | yes | NoBrokersAvailable |
| s01-q1 | vlm-transcript | ✓ | yes | NoBrokersAvailable |
| s01-q1 | vlm-direct | ✓ | - | kafka.errors.NoBrokersAvailable |
| s01-q2 | ocr | ✓ | yes | 03:21:04 |
| s01-q2 | vlm-transcript | ✓ | yes | 03:21:04 |
| s01-q2 | vlm-direct | ✓ | - | 03:21:04 |
| s02-q1 | ocr | ✓ | yes | 11 |
| s02-q1 | vlm-transcript | ✓ | yes | 11 |
| s02-q1 | vlm-direct | ✓ | - | 11 |
| s02-q2 | ocr | ✓ | yes | search-indexer-pfjnd |
| s02-q2 | vlm-transcript | ✓ | yes | search-indexer-pfjnd |
| s02-q2 | vlm-direct | ✓ | - | search-indexer-pfjnd |
| s03-q1 | ocr | ✓ | yes | 502 |
| s03-q1 | vlm-transcript | ✓ | yes | 502 |
| s03-q1 | vlm-direct | ✓ | - | 502 Bad Gateway |
| s03-q2 | ocr | ✓ | yes | nginx/1.25.3 |
| s03-q2 | vlm-transcript | ✓ | yes | nginx/1.25.3 |
| s03-q2 | vlm-direct | ✓ | - | nginx/1.25.3 |
| s04-q1 | ocr | ✗ | no | unknown |
| s04-q1 | vlm-transcript | ✓ | yes | eu-west-1 |
| s04-q1 | vlm-direct | ✓ | - | eu-west-1 |
| s04-q2 | ocr | ✓ | yes | 9.1s |
| s04-q2 | vlm-transcript | ✓ | yes | 9.1 s |
| s04-q2 | vlm-direct | ✓ | - | 9.1 s |
