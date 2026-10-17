"""AIENG-40/41: turning PDFs and images into text lines with provenance (page, pixel box)."""

import os
from pathlib import Path

import pytest

from incidentops_ai.llm.fake_provider import FakeProvider
from incidentops_ai.llm.provider import LLMStatus, ModelConfig
from incidentops_ai.multimodal.extract import (
    ExtractionError,
    OcrExtractor,
    PdfTextExtractor,
    Transcript,
    UnsupportedMediaError,
    VlmTranscriber,
    detect_media_type,
    render_pages,
)

ASSETS = Path(__file__).resolve().parent.parent / "evals" / "multimodal" / "assets"
CONFIG = ModelConfig(model="fake-model")


def asset(name: str) -> bytes:
    return (ASSETS / name).read_bytes()


def test_media_type_comes_from_the_bytes_not_the_name():
    assert detect_media_type(asset("s01-terminal-notification-worker.png")) == "image/png"
    assert detect_media_type(asset("p01-oncall-rota.pdf")) == "application/pdf"
    assert detect_media_type(b"\xff\xd8\xff\xe0rest-of-jpeg") == "image/jpeg"
    with pytest.raises(UnsupportedMediaError, match="PNG, JPEG or PDF"):
        detect_media_type(b"GIF89a....")


async def test_a_text_pdf_gives_its_lines_with_page_numbers():
    extraction = await PdfTextExtractor().extract(asset("p01-oncall-rota.pdf"))

    assert extraction.method == "pdf-text" and extraction.pages == 1
    assert any("Tom Hughes" in line.text for line in extraction.lines)
    assert all(line.page == 1 and line.box is None for line in extraction.lines)
    assert extraction.chars_per_page[0] > 200


async def test_a_scanned_pdf_has_no_text_layer():
    extraction = await PdfTextExtractor().extract(asset("p03-capacity-plan-scanned.pdf"))

    assert extraction.lines == () and extraction.chars_per_page == (0,)


def test_pdf_pages_render_to_png_at_twice_the_size():
    [page] = render_pages(asset("p03-capacity-plan-scanned.pdf"))

    assert page[:4] == b"\x89PNG"
    from io import BytesIO

    from PIL import Image

    assert Image.open(BytesIO(page)).width > 1100  # A4 at 72 dpi is 595 points wide


class FakeEngine:
    """Stands in for RapidOCR: returns (box points, text, score) triples."""

    def __init__(self, lines):
        self.lines = lines
        self.calls = 0

    def __call__(self, image):
        self.calls += 1
        return self.lines, None


async def test_ocr_lines_carry_a_pixel_box_and_the_text_coverage():
    engine = FakeEngine(
        [
            [[[10, 20], [110, 20], [110, 40], [10, 40]], "first line", 0.98],
            [[[10, 60], [210, 60], [210, 80], [10, 80]], "second line", 0.91],
        ]
    )

    extraction = await OcrExtractor(engine=engine).extract(asset("s03-browser-502.png"))

    assert [line.text for line in extraction.lines] == ["first line", "second line"]
    assert extraction.lines[0].box == (10, 20, 110, 40)
    assert extraction.lines[1].confidence == pytest.approx(0.91)
    image_area = 1000 * 520
    assert extraction.coverage == (pytest.approx((100 * 20 + 200 * 20) / image_area),)


async def test_ocr_of_a_pdf_renders_each_page_first():
    engine = FakeEngine([[[[0, 0], [10, 0], [10, 10], [0, 10]], "row", 0.9]])

    extraction = await OcrExtractor(engine=engine).extract(
        asset("p04-maintenance-windows-scanned.pdf")
    )

    assert engine.calls == 1 and extraction.pages == 1
    assert extraction.lines[0].page == 1


async def test_an_image_with_no_text_gives_no_lines():
    extraction = await OcrExtractor(engine=FakeEngine(None)).extract(
        asset("c01-latency-by-region.png")
    )

    assert extraction.lines == () and extraction.coverage == (0.0,)


async def test_a_vlm_transcript_sends_the_image_and_returns_its_lines():
    provider = FakeProvider(
        [Transcript(lines=["p99 latency by region", "eu-west-1 spikes to 9 s"])]
    )

    extraction = await VlmTranscriber(provider, CONFIG).extract(asset("c01-latency-by-region.png"))

    assert extraction.method == "vlm-transcript"
    assert [line.text for line in extraction.lines][1] == "eu-west-1 spikes to 9 s"
    [call] = provider.calls
    assert call.prompt.name == "transcribe_image" and call.schema is Transcript
    assert call.prompt.images[0].media_type == "image/png"


async def test_a_vlm_transcript_of_a_pdf_sends_one_rendered_page_per_call():
    provider = FakeProvider([Transcript(lines=["Month Peak", "November 120"])])

    extraction = await VlmTranscriber(provider, CONFIG).extract(
        asset("p03-capacity-plan-scanned.pdf")
    )

    assert len(provider.calls) == 1 and extraction.lines[1].page == 1


async def test_a_failed_vlm_call_is_an_extraction_error_not_empty_text():
    with pytest.raises(ExtractionError, match="timed_out"):
        await VlmTranscriber(FakeProvider([LLMStatus.TIMED_OUT]), CONFIG).extract(
            asset("c01-latency-by-region.png")
        )


@pytest.mark.skipif(
    os.environ.get("RUN_REAL_OCR") != "1", reason="needs the ocr extra; RUN_REAL_OCR=1"
)
async def test_real_ocr_reads_the_kubectl_screenshot():
    extraction = await OcrExtractor().extract(asset("s02-kubectl-search-indexer.png"))

    squeezed = "".join(extraction.text.lower().split())
    assert "oomkilled" in squeezed and "mbzrw" in squeezed
