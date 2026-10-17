# Multimodal incidents end to end (AIENG-42)

Run on 17 Oct 2026 against a scratch database (dropped afterwards) with the corpus ingested and indexed, the real
embedding model, `gpt-6-luna` for vision and diagnosis, and RapidOCR installed (`uv sync --extra ocr`).
Each incident was created through the API, its files uploaded to `POST /incidents/{id}/attachments`,
then `POST /incidents/{id}/diagnosis` was called. Files are the benchmark assets in `../assets/`.

| Incident | Files (extraction, lines, upload time) | Diagnosis | Cited |
|---|---|---|---|
| Notifications stopped going out | s01 (vlm-transcript, 8, 4.6 s) | answered, 2.9 s | A1, E2 |
| New products missing from search | s02 (vlm-transcript, 5, 7.4 s) | answered, 4.0 s | A1, E2 |
| Card payments failing for European customers | s04 (vlm-transcript, 6, 2.6 s); c01 (vlm-transcript, 19, 4.3 s) | answered, 3.6 s | E1 |
| Checkout shows an error page after today's release | s03 (vlm-transcript, 3, 1.9 s); c02 (vlm-transcript, 13, 2.0 s) | answered, 4.4 s | E1, E2 |
| Orders cannot be placed | c03 (vlm-transcript, 14, 3.7 s) | answered, 3.1 s | E1 |
| Orders database failed over | d02 (vlm-transcript, 7, 2.0 s); p04 (ocr, 14, 0.6 s) | no_answer (insufficient_evidence), 3.9 s | – |

## Citations of attachments

- **Notifications stopped going out**: `A1` s01-terminal-notification-worker.png (vlm-transcript), quote “2026-02-03 03:21:04 ERRORkafka.errors.NoBrokersAvailable: NoBrokersAvailable”. Claim: The Kafka brokers’ TLS certificate expired, preventing the notification worker from connecting and stopping delivery. The incident timing and worker errors match the documented certificate-expiry outage.
- **New products missing from search**: `A1` s02-kubectl-search-indexer.png (vlm-transcript), quote “search-indexer-mbznw  0/1     OOMKilled          11         14m”. Claim: The search-indexer pods are unhealthy because of memory-related terminations, which are likely delaying new products from appearing in search. The evidence does not identify the specific configuration or code change behind the memory issue.

## Notes

- The pod name in the second citation reads `mbznw`; the image says `mbzrw`. The vision transcript misread it, and
  the citation is faithful to the transcript, not to the picture. Attachment text from a model is evidence about
  what the model read.
- Images went to the vision model, so their citations carry no pixel box (only OCR records boxes). The scanned
  PDF was OCR'd with boxes, but the diagnosis did not cite it.
- The failover incident (a diagram and a maintenance-window list) was answered `insufficient_evidence`: the files
  say what happened, not why, and the model declined to guess.
- Diagnosis calls used 17,365 input and 1,771 output tokens in total.
