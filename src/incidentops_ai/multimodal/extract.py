"""From PDFs and images to text lines with provenance (AIENG-40, AIENG-41).

Three extractors share one result type, `Extraction`: lines of text, each with the page it came from
and, for OCR, the pixel box it occupies. The diagnosis can only cite text, so this is how a
screenshot becomes evidence: OCR (local, free, boxes per line) or a vision model's transcript
(costs a call, reads charts and diagrams, no boxes). Text-layer PDFs need neither.
"""

import asyncio
import io
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict

from incidentops_ai.llm.prompts import load_prompt
from incidentops_ai.llm.provider import ImageInput, LLMProvider, LLMStatus, ModelConfig

MediaType = Literal["image/png", "image/jpeg", "application/pdf"]
Method = Literal["pdf-text", "ocr", "vlm-transcript"]
RENDER_SCALE = 2.0  # PDF pages are 72 dpi; twice that is enough for OCR on normal print


class UnsupportedMediaError(ValueError):
    """The bytes are not a PNG, JPEG or PDF (whatever the file name says)."""


class ExtractionError(RuntimeError):
    """Extraction failed (for a VLM: the model call did not succeed). Never an empty success."""


@dataclass(frozen=True, slots=True)
class TextLine:
    text: str
    page: int  # 1-based
    box: tuple[int, int, int, int] | None  # x0, y0, x1, y1 in the page image's pixels (OCR only)
    confidence: float | None = None


@dataclass(frozen=True)
class Extraction:
    method: Method
    lines: tuple[TextLine, ...]
    pages: int
    chars_per_page: tuple[int, ...] = ()  # text-layer characters per page (pdf-text)
    coverage: tuple[float, ...] = ()  # share of each page image covered by OCR boxes (ocr)
    latency_ms: float = 0.0
    input_tokens: int | None = None
    output_tokens: int | None = None

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)


def detect_media_type(data: bytes) -> MediaType:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    raise UnsupportedMediaError("attachments must be PNG, JPEG or PDF files")


def render_pages(data: bytes, *, scale: float = RENDER_SCALE) -> list[bytes]:
    """Every PDF page as a PNG image."""
    import pypdfium2 as pdfium

    pages: list[bytes] = []
    document = pdfium.PdfDocument(data)
    try:
        for index in range(len(document)):
            image = document[index].render(scale=scale).to_pil()
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            pages.append(buffer.getvalue())
    finally:
        document.close()
    return pages


def _images(data: bytes) -> list[tuple[bytes, Literal["image/png", "image/jpeg"]]]:
    """An image as itself, a PDF as its rendered pages."""
    media_type = detect_media_type(data)
    if media_type == "application/pdf":
        return [(page, "image/png") for page in render_pages(data)]
    return [(data, media_type)]


class Extractor(Protocol):
    method: Method

    async def extract(self, data: bytes) -> Extraction: ...


class PdfTextExtractor:
    """The text layer of a PDF. An image, or a scanned PDF, has none."""

    method: Method = "pdf-text"

    async def extract(self, data: bytes) -> Extraction:
        started = time.perf_counter()
        if detect_media_type(data) != "application/pdf":
            return Extraction("pdf-text", (), 1, (0,), latency_ms=0.0)
        return await asyncio.to_thread(self._extract, data, started)

    def _extract(self, data: bytes, started: float) -> Extraction:
        import pypdfium2 as pdfium

        lines: list[TextLine] = []
        chars: list[int] = []
        document = pdfium.PdfDocument(data)
        try:
            for index in range(len(document)):
                textpage = document[index].get_textpage()
                text = textpage.get_text_bounded()
                textpage.close()
                page_lines = [line.strip() for line in text.splitlines() if line.strip()]
                chars.append(sum(len(line) for line in page_lines))
                lines += [TextLine(line, index + 1, None) for line in page_lines]
            pages = len(document)
        finally:
            document.close()
        latency = (time.perf_counter() - started) * 1000
        return Extraction("pdf-text", tuple(lines), pages, tuple(chars), latency_ms=latency)


class OcrExtractor:
    """RapidOCR (optional extra `ocr`), run locally. `engine` lets tests stand in for it."""

    method: Method = "ocr"

    def __init__(self, engine: Any = None) -> None:
        self._engine = engine
        self._lock = threading.Lock()

    def _get_engine(self) -> Any:
        with self._lock:
            if self._engine is None:
                try:
                    from rapidocr_onnxruntime import RapidOCR
                except ImportError as error:
                    raise ExtractionError(
                        "OCR needs the optional extra: uv sync --extra ocr"
                    ) from error
                self._engine = RapidOCR()
            return self._engine

    async def extract(self, data: bytes) -> Extraction:
        started = time.perf_counter()
        pages = _images(data)
        lines: list[TextLine] = []
        coverage: list[float] = []
        for number, (image, _) in enumerate(pages, start=1):
            page_lines, covered = await asyncio.to_thread(self._ocr_page, image, number)
            lines += page_lines
            coverage.append(covered)
        latency = (time.perf_counter() - started) * 1000
        return Extraction(
            "ocr", tuple(lines), len(pages), coverage=tuple(coverage), latency_ms=latency
        )

    def _ocr_page(self, image: bytes, page: int) -> tuple[list[TextLine], float]:
        from PIL import Image

        width, height = Image.open(io.BytesIO(image)).size
        result, _ = self._get_engine()(image)
        lines: list[TextLine] = []
        area = 0
        for points, text, score in result or []:
            xs = [round(p[0]) for p in points]
            ys = [round(p[1]) for p in points]
            box = (min(xs), min(ys), max(xs), max(ys))
            area += (box[2] - box[0]) * (box[3] - box[1])
            if str(text).strip():
                lines.append(TextLine(str(text).strip(), page, box, float(score)))
        return lines, area / (width * height)


class Transcript(BaseModel):
    """What the vision model returns: lines of text, then "Visual:" lines for shapes and marks."""

    model_config = ConfigDict(extra="forbid")

    lines: list[str]


class VlmTranscriber:
    """A vision model's transcript of each image or rendered page. One call per page."""

    method: Method = "vlm-transcript"
    prompt_name = "transcribe_image"
    prompt_version = "v1"

    def __init__(self, provider: LLMProvider, config: ModelConfig, *, detail: str = "auto") -> None:
        self._provider = provider
        self._config = config
        self._detail = detail

    async def extract(self, data: bytes) -> Extraction:
        started = time.perf_counter()
        lines: list[TextLine] = []
        input_tokens = output_tokens = 0
        pages = _images(data)
        for number, (image, media_type) in enumerate(pages, start=1):
            prompt = load_prompt(
                self.prompt_name, self.prompt_version, input_text="Transcribe this image."
            )
            prompt = prompt.model_copy(
                update={
                    "images": (ImageInput(media_type=media_type, data=image, detail=self._detail),)
                }
            )
            result = await self._provider.generate_structured(
                prompt=prompt, schema=Transcript, config=self._config
            )
            if result.status is not LLMStatus.SUCCEEDED or result.value is None:
                raise ExtractionError(f"vision transcript failed: {result.status}")
            lines += [
                TextLine(text.strip(), number, None) for text in result.value.lines if text.strip()
            ]
            input_tokens += result.metadata.input_tokens or 0
            output_tokens += result.metadata.output_tokens or 0
        latency = (time.perf_counter() - started) * 1000
        return Extraction(
            "vlm-transcript",
            tuple(lines),
            len(pages),
            latency_ms=latency,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )


def squeeze(text: str) -> str:
    """Lowercase letters and digits only: how OCR text and expected terms are compared, since OCR
    drops spaces between words."""
    return "".join(ch for ch in text.lower() if ch.isalnum())


def contains_terms(text: str, expected: Sequence[str]) -> bool:
    """Extraction recall: does the text hold every expected term (any alternative), ignoring spacing
    and case?"""
    squeezed = squeeze(text)
    return all(
        any(squeeze(alternative) in squeezed for alternative in term.split("|"))
        for term in expected
    )
