# ADR-003: How incident attachments become citable text

- **Status:** accepted (W06, 17 Oct 2026)
- **Context tickets:** AIENG-39 to AIENG-43
- **Evidence:** [multimodal findings](../../evals/multimodal/runs/2026-10-17-multimodal-findings.md),
  [six incidents end to end](../../evals/multimodal/runs/2026-10-17-multimodal-incidents.md),
  [W06 design and hypotheses](../roadmap-w06.md)

## Context

Incidents arrive with screenshots, dashboards, architecture diagrams and PDF pages. The diagnosis
(W04) shows a cause only if it can quote checked text, so every attachment must become text, with
enough provenance to find the quote again. Three ways to get text were measured on 14 synthetic
assets and 28 questions: the PDF text layer, local OCR (RapidOCR), and a vision model's transcript.
A vision model answering straight from the image was measured as the ceiling.

## Decision

1. **Route by what the file is.**
   - PDF with at least 200 characters of text per page → its **text layer** (exact, ~40 ms, free).
   - Any other PDF (a scan) → **OCR** of each rendered page (exact boxes, ~0.3 s, free).
   - Image → **vision-model transcript** when a model is configured; **OCR** when not, or when the
     model call fails.
2. **Only text is cited.** A vision model's direct answer is never shown as evidence: it has nothing
   to quote. Its transcript is.
3. **Provenance is as precise as the extractor allows.** OCR citations carry the pixel box of the
   quoted lines; transcript citations carry the attachment, page and extraction method; text-layer
   citations carry the page and character span.
4. **Attachments stay with their incident.** They are evidence `A1..An` for that incident's
   diagnosis, never indexed into the shared corpus.
5. **Untrusted input is bounded.** Type from the bytes, 5 MB, 10 pages, Pillow's pixel limit, the file
   name only a label, and extracted text treated as data in the prompt.

## Why not the alternatives

- **OCR for everything.** Free and fast, and right on text-heavy screenshots and scans, but 1 of 6 on
  charts and 2 of 6 on diagrams: it keeps the words and loses the picture. It also silently dropped a
  whole line from an alert card, and confidence scores cannot reveal a line that was never detected.
- **The vision model for everything.** Correct on every kind, but 2 to 7 seconds and a paid call per
  page, for no gain on text PDFs and scans.
- **The router I wrote in advance** (send an image to the vision model when OCR boxes cover less than
  8% of it) agreed with the measured choice on 9 of 14 assets: a scanned page is mostly white, so it
  looked "visual". Routing on what the file *is* turned out simpler and, on this data, right.
- **Visual embeddings (ColPali).** Retrieval over page images, not text; nothing to cite, and a large
  model to run locally. Not needed for the questions measured; revisit for long scanned manuals.

## Consequences

- An image attachment costs a model call at upload (about 300 to 1,000 tokens per page in this run)
  and is limited by the LLM rate limit and token, like the other model-spending routes.
- **A transcript can be wrong and still be cited faithfully**: the run cited `mbznw` where the image
  says `mbzrw`. The citation proves the transcript says it, not that the picture does. Mitigation to
  consider: run OCR alongside the transcript for images and flag identifiers the two disagree on.
- OCR needs an optional extra (`uv sync --extra ocr`, about 250 MB with OpenCV). Without it, scanned
  PDFs (and images with no model configured) are refused with `503 extraction_unavailable`.
- The routing rule was read off 14 synthetic assets. It needs real, noisy screenshots and scans to be
  trusted; the benchmark is the harness for that.

## Revisit when

- real attachments arrive (blurry, cropped, phone photos of screens);
- a cheaper or local vision model is good enough for transcripts;
- documents get long (many-page PDFs): page-level routing and visual retrieval become worth measuring.
