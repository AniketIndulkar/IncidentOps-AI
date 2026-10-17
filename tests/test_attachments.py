"""AIENG-42: attachments are validated, routed to the right extractor, stored once, and listed."""

from pathlib import Path

import pytest
from sqlalchemy import text

from incidentops_ai.incidents.models import IncidentCreate
from incidentops_ai.incidents.repository import IncidentRepository
from incidentops_ai.multimodal.attachments import (
    AttachmentError,
    AttachmentProcessor,
    AttachmentRepository,
    attachment_evidence,
)
from incidentops_ai.multimodal.extract import Extraction, ExtractionError, TextLine

ASSETS = Path(__file__).resolve().parent.parent / "evals" / "multimodal" / "assets"


def asset(name: str) -> bytes:
    return (ASSETS / name).read_bytes()


class Recording:
    def __init__(self, method: str, lines=("line one",), *, fail: bool = False) -> None:
        self.method = method
        self.lines = lines
        self.fail = fail
        self.calls = 0

    async def extract(self, data: bytes) -> Extraction:
        self.calls += 1
        if self.fail:
            raise ExtractionError("vision transcript failed: timed_out")
        lines = tuple(
            TextLine(t, 1, (0, i * 20, 100, i * 20 + 18)) for i, t in enumerate(self.lines)
        )
        return Extraction(self.method, lines, 1)


def processor(*, vlm=True, vlm_fails=False):
    ocr = Recording("ocr", ("ocr text",))
    vision = Recording("vlm-transcript", ("vlm text",), fail=vlm_fails) if vlm else None
    return AttachmentProcessor(pdf=_RealPdf(), ocr=ocr, vlm=vision), ocr, vision


class _RealPdf:
    """The real text-layer extractor: cheap, local, and what decides between text and scan."""

    method = "pdf-text"

    async def extract(self, data):
        from incidentops_ai.multimodal.extract import PdfTextExtractor

        return await PdfTextExtractor().extract(data)


async def test_a_pdf_with_a_text_layer_uses_it_and_nothing_else():
    proc, ocr, vision = processor()

    result = await proc.process(asset("p01-oncall-rota.pdf"))

    assert result.method == "pdf-text" and any("Tom Hughes" in l.text for l in result.lines)
    assert ocr.calls == 0 and vision.calls == 0


async def test_a_scanned_pdf_goes_to_ocr():
    proc, ocr, vision = processor()

    result = await proc.process(asset("p03-capacity-plan-scanned.pdf"))

    assert result.method == "ocr" and ocr.calls == 1 and vision.calls == 0


async def test_an_image_goes_to_the_vision_model_when_there_is_one():
    proc, ocr, vision = processor()

    result = await proc.process(asset("c01-latency-by-region.png"))

    assert result.method == "vlm-transcript" and vision.calls == 1 and ocr.calls == 0


async def test_an_image_falls_back_to_ocr_without_a_model_or_when_the_model_fails():
    without, ocr_a, _ = processor(vlm=False)
    failing, ocr_b, _ = processor(vlm_fails=True)

    assert (await without.process(asset("s02-kubectl-search-indexer.png"))).method == "ocr"
    assert (await failing.process(asset("s02-kubectl-search-indexer.png"))).method == "ocr"
    assert ocr_a.calls == ocr_b.calls == 1


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (b"GIF89a-not-allowed", "unsupported_media"),
        (b"%PDF-1.4 this is not really a pdf", "unreadable_file"),
        (b"\x89PNG\r\n\x1a\n" + b"0" * (5 * 1024 * 1024), "file_too_large"),
    ],
    ids=["gif", "broken-pdf", "too-large"],
)
async def test_bad_files_are_refused_with_a_stable_code(data, code):
    proc, _, _ = processor()

    with pytest.raises(AttachmentError) as error:
        await proc.process(data)

    assert error.value.code == code


async def test_a_pdf_with_too_many_pages_is_refused():
    from fpdf import FPDF

    pdf = FPDF()
    for _ in range(11):
        pdf.add_page()
    proc, _, _ = processor()

    with pytest.raises(AttachmentError) as error:
        await proc.process(bytes(pdf.output()))

    assert error.value.code == "too_many_pages"


async def new_incident(session_factory) -> str:
    async with session_factory() as session:
        created = await IncidentRepository(session).create(
            IncidentCreate(
                title="Card payments failing in Europe",
                description="Charges time out after nine seconds in one region.",
                service="payments-gateway",
                severity="SEV1",
                environment="production",
            )
        )
        return created.incident.id


async def test_an_attachment_is_stored_once_and_listed(session_factory):
    incident_id = await new_incident(session_factory)
    extraction = Extraction("ocr", (TextLine("eu-west-1 9.1 s", 1, (10, 20, 200, 40), 0.97),), 1)
    data = asset("s04-alert-payments.png")

    async with session_factory() as session:
        repo = AttachmentRepository(session)
        first, created = await repo.add(
            tenant_id="default",
            incident_id=incident_id,
            filename="alert.png",
            media_type="image/png",
            data=data,
            extraction=extraction,
        )
        again, created_again = await repo.add(
            tenant_id="default",
            incident_id=incident_id,
            filename="alert-copy.png",
            media_type="image/png",
            data=data,
            extraction=extraction,
        )
        await session.commit()
        listed = await repo.list_for_incident(incident_id)
        stored = await repo.texts_for_incident(incident_id)

    assert created and not created_again and again.id == first.id
    assert [a.filename for a in listed] == ["alert.png"]
    assert (first.method, first.line_count, first.size_bytes) == ("ocr", 1, len(data))
    assert first.preview == "eu-west-1 9.1 s"
    [source] = stored
    assert source.lines[0].box == (10, 20, 200, 40)


async def test_deleting_the_incident_deletes_its_attachments(session_factory):
    incident_id = await new_incident(session_factory)
    async with session_factory() as session:
        await AttachmentRepository(session).add(
            tenant_id="default",
            incident_id=incident_id,
            filename="a.png",
            media_type="image/png",
            data=asset("s03-browser-502.png"),
            extraction=Extraction("ocr", (), 1),
        )
        await session.execute(text("DELETE FROM incidents WHERE id = :id"), {"id": incident_id})
        await session.commit()
        count = (await session.execute(text("SELECT count(*) FROM attachments"))).scalar_one()

    assert count == 0


async def test_the_best_attachment_lines_become_evidence_with_their_boxes():
    from incidentops_ai.multimodal.attachments import AttachmentText

    alert = AttachmentText(
        id="11111111-1111-1111-1111-111111111111",
        filename="alert.png",
        method="ocr",
        lines=(
            TextLine("[FIRING:1] PaymentsChargeLatencyHigh", 1, (30, 54, 600, 80)),
            TextLine("p99 /v1/charges = 9.1 s (threshold 3 s)", 1, (30, 160, 560, 182)),
        ),
    )
    page = AttachmentText(
        id="22222222-2222-2222-2222-222222222222",
        filename="unrelated.pdf",
        method="pdf-text",
        lines=(TextLine("Hand-over happens on Monday at 10:00 UTC.", 1, None),),
    )

    evidence = await attachment_evidence(
        "charges latency 9 seconds", [alert, page], tenant_id="default", k=1
    )

    [item] = evidence
    assert item.id == "A1"
    assert item.chunk.doc_id == "att-11111111-1111-1111-1111-111111111111"
    assert item.chunk.title == "alert.png" and item.extraction == "ocr"
    assert [r.box for r in item.regions] == [(30, 54, 600, 80), (30, 160, 560, 182)]
