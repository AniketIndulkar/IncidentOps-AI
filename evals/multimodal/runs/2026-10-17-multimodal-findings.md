# Multimodal evidence: findings (AIENG-40 to AIENG-43)

Run on 17 Oct 2026. Model `gpt-6-luna` for vision and for text answers; OCR is RapidOCR (PP-OCRv4,
local). Evidence: [benchmark report](2026-10-17-multimodal-benchmark.md) and its
[per-answer data](2026-10-17-multimodal-benchmark.json); the six incidents run end to end are in
[multimodal-incidents](2026-10-17-multimodal-incidents.md). Design, hypotheses H18 to H23 and both
rules were committed before any extraction code: [roadmap-w06.md](../../../docs/roadmap-w06.md).

## The choice

| Kind | Path | Why |
|---|---|---|
| PDF with a text layer | **text layer** (pypdfium2) | 4/4, 37 ms, no model, exact text |
| Scanned PDF | **OCR** | 4/4; the text layer is empty; the VLM is no better and costs a call |
| Screenshot | **VLM transcript** | 8/8 against OCR's 7/8 (the rule: a costlier path must win by one question, and it did) |
| Chart | **VLM transcript** | 6/6 against OCR's 1/6 |
| Diagram | **VLM transcript** | 6/6 against OCR's 2/6 |

In the product (`AttachmentProcessor`): a PDF with at least 200 characters of text per page uses its
text layer, any other PDF is OCR'd, and an image goes to the vision model when one is configured, with
OCR as the fallback if the model is missing or fails.

## Numbers (28 questions)

Correct answers per kind and path:

| Kind | n | pdf-text | OCR | VLM transcript | VLM direct (ceiling, not citable) |
|---|---|---|---|---|---|
| screenshot | 8 | – | 7 | **8** | 8 |
| chart | 6 | – | 1 | **6** | 6 |
| diagram | 6 | – | 2 | **6** | 6 |
| pdf-text | 4 | **4** | 4 | 4 | 4 |
| pdf-scanned | 4 | 0 | **4** | 4 | 4 |

Extraction time per asset: PDF text 37 ms; OCR 176 to 425 ms; VLM transcript 2.3 to 3.5 s. All
model calls for the whole benchmark: about 67,000 input and 7,300 output tokens, about **$0.24** at an
assumed $2.50 / $10 per million tokens (the provider's price for this model is not published to us).

## What the failures were

- **OCR loses the picture, not the words.** On charts the OCR text held every word of the answer
  (6/6), yet only 1 of 6 answers was right: a legend lists three regions, but which line spikes is a
  shape. The same on diagrams: box labels survive, arrows do not. "The text contains the answer" is a
  misleading measure for anything visual.
- **OCR can silently drop a whole line.** On the alert card, the line
  `service=payments-gateway region=eu-west-1 acquirer=primary` is missing from the OCR output, and every
  line it did return scored 0.97 or more. Confidence scores cannot reveal what was never detected.
- **OCR drops spaces and mangles case and spelling**: `2026-02-0303:21:04ERROR…`, `ooMkilled`,
  `biling-service`. Answers still came out right on log screenshots because the text model reads
  through it, but quotes from OCR text look like this in a citation.
- **A diagram that reads left to right fooled the measure.** On the request-path diagram OCR scored
  2/2: the labels come out in layout order (`browser`, `CDN`, `object storage`), so the chain can be
  read without seeing the arrows. A diagram with crossing arrows (d01) shows the real gap.
- **A VLM transcript can misread too**, and then it is the transcript that gets cited. In the
  end-to-end run the vision model wrote `search-indexer-mbznw` for `mbzrw`. The citation check passes,
  because the quote is in the transcript; it is the transcript that is wrong.

## The router I wrote in advance was wrong (H23)

The automatic rule (text layer → `pdf-text`; OCR boxes covering under 8% of the image → VLM; else OCR)
agreed with the per-kind choice on **9 of 14** assets:

- the three text-heavy screenshots had high OCR coverage (0.12 to 0.26), so the rule chose OCR, while
  the per-kind rule chose the VLM by one question;
- the two scanned PDF pages had **low** coverage (0.027, 0.039) because most of a scanned page is white,
  so the rule sent them to the VLM, while OCR was enough. The first probe had already shown this; the
  rule was not changed after seeing it.

The product therefore routes by what the file is (PDF with or without a text layer, or an image), not
by text coverage. That rule matches the per-kind choice on all 14 assets **by construction**: it was
read off these results, and only new assets can test it.

## Hypotheses

| | Said beforehand | Result |
|---|---|---|
| H18 | On text screenshots, OCR within 1 question of VLM direct | **Confirmed**: 7 vs 8 |
| H19 | Charts: OCR ≤ 2/6, VLM direct ≥ 5/6 | **Confirmed**: 1/6 vs 6/6 |
| H20 | Diagrams: OCR ≤ 3/6, VLM direct ≥ 5/6 | **Confirmed**: 2/6 vs 6/6 (one diagram readable from label order) |
| H21 | Text PDFs: pdf-text 4/4; scanned: pdf-text 0, OCR ≥ 3/4 | **Confirmed**: 4/4; 0 and 4/4 |
| H22 | The citable VLM transcript loses ≤ 1 question per kind against VLM direct | **Confirmed**: it lost none |
| H23 | The coverage router agrees with the per-kind choice on ≥ 80% of assets | **Refuted**: 9 of 14 (64%) |

## End to end (AIENG-42)

Six incidents with eight attached files ran through the real API: all uploads stored and extracted
(images by VLM transcript in 1.9 to 7.4 s, the scanned PDF by OCR in 0.6 s), all six diagnoses
returned. Two diagnoses cited the attachment itself (a log line and a pod row), three cited only
runbooks and postmortems, and one answered `insufficient_evidence`. Details in
[multimodal-incidents](2026-10-17-multimodal-incidents.md).

## Limits

Synthetic, clean images; 28 questions (one question is 12 to 25 points within a kind); one model; one
run each; the price is assumed. Box-level provenance exists only for OCR text, and in this run no OCR
line happened to be cited end to end (the tests cover it). Scanned pages and screenshots from real
systems will be noisier.
