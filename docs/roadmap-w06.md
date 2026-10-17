# W06 roadmap: multimodal evidence — OCR, vision models and when to use which (9–13 Nov 2026)

Source: Jira epic **AIENG-7** and tasks **AIENG-39 to AIENG-43**, read 17 Oct 2026. Overall plan:
[ROADMAP.md](ROADMAP.md). Built early, stacked on W05 (branch `feat/w06-multimodal`).

> **Status:** design written before any extraction code. Decisions are mine (the user asked for
> W05 to W13 back to back); the spend cap and local-first choices are the user's.

## Goal of the week

Let an incident carry screenshots, charts, diagrams and PDF pages as evidence, turn them into text the
diagnosis can **cite**, and decide with numbers which extraction path each kind of document gets.

**Exit criterion (from the epic):** benchmarked multimodal ingestion over screenshots, diagrams and
complex PDFs.

```
attachment (PNG / JPEG / PDF)
   │ route by kind and by what the file contains          AIENG-43
   ├─ PDF with a text layer ─▶ text extraction (pypdfium2)
   ├─ text-like image or scanned page ─▶ OCR (RapidOCR, local) + line boxes
   └─ chart or diagram ─▶ vision model transcript (VLM)
            ▼
   text + provenance (page, OCR line box) ─▶ evidence A1..An beside E1..E5 ─▶ cited diagnosis   AIENG-42
```

## Two probes before writing this (17 Oct)

- **RapidOCR** (ONNX, PP-OCRv4, models bundled in the wheel, no download at run time) read a rendered
  log screenshot with scores of 0.97 to 0.99 in 0.36 s, but **dropped the spaces between words and
  some casing** ("2026-02-0303:21:04ERROR…", "ooMkilled"). That is a property to measure, not hide.
- The configured model (`gpt-6-luna`) **accepts images**: a low-detail screenshot cost 120 input
  tokens and answered correctly in 3 s.

## Decisions made by the user

| Decision | Choice |
|---|---|
| Spend and downloads | Allowed, $5 total cap across W05 to W13 |

## Decisions made by me

| Decision | Choice | Why |
|---|---|---|
| Benchmark data | **14 synthetic assets** rendered by code (`incidentops_ai.multimodal.assets`), committed as files: 4 screenshots, 3 charts, 3 diagrams, 2 text-layer PDFs, 2 scanned PDFs; **28 questions** with expected answers | Real incident screenshots are private; generated ones are reproducible and can be designed so that, for example, a chart's answer is only visible as a line's shape |
| Answers | A list of required terms per question, each with allowed alternatives (`5\|five`); a reply is correct if it contains every term (case-insensitive) | Exact match is too strict for free text; containment is easy to audit |
| Questions that cannot be guessed | Diagram relations use components a model cannot infer from names (`promo-engine`, `ledger-writer`); the failed database is marked only by a red cross, not by text | Otherwise a text model answers from prior knowledge and OCR looks better than it is |
| OCR | **RapidOCR** (`rapidocr-onnxruntime`, optional extra `ocr`) | Local, free, no system install; the only pip OCR with bundled models I found that runs on Python 3.13 |
| PDFs | **pypdfium2** for text extraction and for rendering pages to images | One dependency for both; binary wheels |
| VLM | The configured LLM through the existing provider, with image inputs added to `Prompt` | No new vendor; the provider already handles retries, timeouts and recording |
| Citable text | Diagnosis can only cite text. So images become text first: OCR text (with a box per line) or a VLM **transcript**. Direct VLM answers are measured as a ceiling, not used for citations | The W04 rule stands: every shown cause has checked provenance |
| Storage | Attachments in PostgreSQL (`bytea`, ≤ 5 MB, PNG/JPEG/PDF checked by magic bytes, ≤ 10 PDF pages), extracted text and line boxes beside them | One store, transactional with the incident; object storage is a deploy concern for later |
| Attachments in diagnosis | Not indexed into the shared corpus. Each incident's own attachment chunks are added as evidence `A1..An` (best 3 by BM25 against the incident text) next to the retrieved `E1..E5` | An attachment belongs to one incident; putting it in the shared index would leak it into other incidents' retrieval |
| Provenance | Citation of an attachment shows attachment id, file name, page, extraction method and, for OCR, the **pixel box** of the quoted lines | "Traceable evidence" for an image means pointing at the region |

## The benchmark (AIENG-39), written before any extraction code

`evals/multimodal/cases.jsonl` and `evals/multimodal/assets/`:

| Kind | Assets | Questions | What makes it hard |
|---|---|---|---|
| screenshot | terminal log, `kubectl get pods`, browser 502 page, alert card | 8 | Dense small text; OCR spacing |
| chart | latency by region (one spikes), error rate by service (bars, no labels), DB connections by state with a limit line | 6 | The answer is a shape, not a string |
| diagram | architecture with arrows, failover with a red cross, request path | 6 | Relations and visual marks |
| pdf-text | on-call rota table, SLA policy table | 4 | Table lookups |
| pdf-scanned | capacity plan, maintenance windows (image-only PDFs) | 4 | No text layer |

## Paths compared (AIENG-40, AIENG-41)

| Path | Produces | Applies to |
|---|---|---|
| `pdf-text` | the PDF's text layer | PDFs |
| `ocr` | OCR lines (images; PDF pages rendered at 2×) | everything |
| `vlm-transcript` | a model transcript: all text, then charts (which series rises, peaks) and diagrams (each arrow as `A → B`), visual marks | everything |
| `vlm-direct` | an answer straight from image + question (not citable; the ceiling) | everything |

For the three text paths, a text-only model answers each question from the extracted text ("answer
only from this text, or say unknown"). Measured per question: answer correct, plus whether the
extracted text even contains the expected terms (extraction recall, free). Per asset: latency and
tokens. Cost is reported as tokens and as an estimate at an assumed price, labelled as such.

## Hypotheses, written before any extraction code

- **H18.** On screenshots of text, OCR-based answers are within 1 question of `vlm-direct`, at no
  model cost for extraction.
- **H19.** On charts, OCR-based answers get at most 2 of 6; `vlm-direct` gets at least 5 of 6.
- **H20.** On diagrams, OCR-based answers get at most 3 of 6; `vlm-direct` at least 5 of 6.
- **H21.** On text-layer PDFs `pdf-text` answers 4 of 4. On scanned PDFs `pdf-text` answers none and
  OCR at least 3 of 4.
- **H22.** The citable `vlm-transcript` loses at most 1 question per kind against `vlm-direct`.
- **H23.** An automatic router (rule below) picks the per-kind winner for at least 80% of assets.

## How the routing is decided (fixed before the run)

1. For each kind, among the citable paths (`pdf-text`, `ocr`, `vlm-transcript`), pick the one with
   the most correct answers.
2. A costlier path must beat a cheaper one by **at least 1 question** to be picked. Cost order:
   `pdf-text` < `ocr` < `vlm-transcript`.
3. The automatic router does not know the kind. Its rule, fixed now: a PDF page with at least 200
   characters of text layer → `pdf-text`; otherwise render and OCR it. An image, or a page without a
   text layer, goes to `ocr` unless the OCR boxes cover **less than 8% of the image area**, in which
   case it is visual and goes to `vlm-transcript` (when a model is configured). H23 measures how often
   that agrees with step 1.

## Day by day

- **AIENG-39** assets, cases, this document.
- **AIENG-40** PDF text and OCR extractors with line boxes; offline fakes; the benchmark runner.
- **AIENG-41** image inputs in the provider; VLM transcript and direct paths; the real run.
- **AIENG-42** attachments: migration, upload route, extraction at upload, `A1..An` evidence with
  box-level provenance, five real multimodal incidents end to end.
- **AIENG-43** findings, routing decision, ADR-003.

## Risks

1. **Synthetic images are cleaner than real ones.** Real screenshots are blurrier, scaled and noisy;
   OCR will look better here than in production. The scanned PDFs add rotation and noise to partly
   compensate.
2. **28 questions.** One question is 3.6 points; kinds have 4 to 8 questions.
3. **Text from images is untrusted input.** An OCR'd line can say "ignore your instructions". It is
   treated as data like every other piece of evidence; the defence proper is W10.
4. **VLM cost and latency** at upload time. Measured; the router keeps VLM for images that need it.
