"""AIENG-42: uploading evidence files to an incident, and citing them in the diagnosis."""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from httpx import ASGITransport, AsyncClient
from test_analysis_api import INCIDENT
from test_diagnosis import StaticRetriever, cause, model_says

from app.main import create_app
from incidentops_ai.llm.fake_provider import FakeProvider
from incidentops_ai.multimodal.attachments import AttachmentProcessor
from incidentops_ai.multimodal.extract import Extraction, TextLine

ASSETS = Path(__file__).resolve().parent.parent / "evals" / "multimodal" / "assets"
ALERT = (ASSETS / "s04-alert-payments.png").read_bytes()


class FakeOcr:
    method = "ocr"

    async def extract(self, data: bytes) -> Extraction:
        return Extraction(
            "ocr",
            (
                TextLine("[FIRING:1] PaymentsChargeLatencyHigh", 1, (30, 54, 600, 80), 0.98),
                TextLine("service=payments-gateway region=eu-west-1", 1, (30, 110, 720, 132), 0.97),
                TextLine("p99 /v1/charges = 9.1 s (threshold 3 s)", 1, (30, 160, 560, 182), 0.96),
            ),
            1,
            coverage=(0.12,),
        )


class NoPdf:
    method = "pdf-text"

    async def extract(self, data: bytes) -> Extraction:
        return Extraction("pdf-text", (), 1, (0,))


PROCESSOR = AttachmentProcessor(pdf=NoPdf(), ocr=FakeOcr(), vlm=None)


@asynccontextmanager
async def client(
    settings, provider=None, retriever=None, *, token=True
) -> AsyncIterator[AsyncClient]:
    app = create_app(
        settings, llm_provider=provider, retriever=retriever, attachment_processor=PROCESSOR
    )
    headers = {"Authorization": f"Bearer {settings.api_token.get_secret_value()}"} if token else {}
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://t", headers=headers) as c,
    ):
        yield c


async def new_incident(c: AsyncClient) -> str:
    return (await c.post("/incidents", json=INCIDENT)).json()["id"]


def upload(name: str, data: bytes, kind: str = "image/png"):
    return {"file": (name, data, kind)}


async def test_an_uploaded_image_is_extracted_stored_and_listed(settings):
    async with client(settings) as c:
        incident_id = await new_incident(c)

        first = await c.post(
            f"/incidents/{incident_id}/attachments", files=upload("alert.png", ALERT)
        )
        again = await c.post(
            f"/incidents/{incident_id}/attachments", files=upload("copy.png", ALERT)
        )
        listed = await c.get(f"/incidents/{incident_id}/attachments")

    assert first.status_code == 201 and again.status_code == 200
    body = first.json()
    assert again.json()["id"] == body["id"]
    assert (body["filename"], body["media_type"], body["method"]) == (
        "alert.png",
        "image/png",
        "ocr",
    )
    assert body["line_count"] == 3 and body["preview"].startswith("[FIRING:1]")
    assert [a["id"] for a in listed.json()] == [body["id"]]


async def test_the_file_name_is_kept_as_a_label_never_as_a_path(settings):
    async with client(settings) as c:
        incident_id = await new_incident(c)

        resp = await c.post(
            f"/incidents/{incident_id}/attachments", files=upload("../../etc/passwd.png", ALERT)
        )

    assert resp.json()["filename"] == "passwd.png"


async def test_the_type_comes_from_the_bytes_not_the_declared_content_type(settings):
    async with client(settings) as c:
        incident_id = await new_incident(c)

        resp = await c.post(
            f"/incidents/{incident_id}/attachments",
            files=upload("notes.png", b"GIF89a fake image", "image/png"),
        )

    assert resp.status_code == 415 and resp.json()["code"] == "unsupported_media"


async def test_a_file_over_five_megabytes_is_refused_without_reading_it_all(settings):
    async with client(settings) as c:
        incident_id = await new_incident(c)

        resp = await c.post(
            f"/incidents/{incident_id}/attachments",
            files=upload("big.png", b"\x89PNG\r\n\x1a\n" + b"0" * (6 * 1024 * 1024)),
        )

    assert resp.status_code == 413 and resp.json()["code"] == "file_too_large"


async def test_uploads_need_the_token_and_an_existing_incident(settings):
    async with client(settings, token=False) as c:
        anonymous = await c.post(
            f"/incidents/{uuid.uuid4()}/attachments", files=upload("a.png", ALERT)
        )
    async with client(settings) as c:
        missing = await c.post(
            f"/incidents/{uuid.uuid4()}/attachments", files=upload("a.png", ALERT)
        )

    assert anonymous.status_code == 401
    assert missing.status_code == 404 and missing.json()["code"] == "incident_not_found"


async def test_the_diagnosis_cites_an_attachment_with_the_box_of_the_quoted_line(settings):
    provider = FakeProvider(
        [model_says(cause("eu-west-1 charges are slow.", ("A1", "region=eu-west-1")))]
    )
    async with client(settings, provider, StaticRetriever([])) as c:
        incident_id = await new_incident(c)
        await c.post(f"/incidents/{incident_id}/attachments", files=upload("alert.png", ALERT))

        resp = await c.post(f"/incidents/{incident_id}/diagnosis")

    assert resp.status_code == 200
    diagnosis = resp.json()["diagnosis"]
    assert diagnosis["status"] == "answered"
    [citation] = diagnosis["causes"][0]["citations"]
    assert citation["evidence_id"] == "A1" and citation["doc_id"].startswith("att-")
    assert citation["title"] == "alert.png" and citation["extraction"] == "ocr"
    assert citation["region"] == [30, 110, 720, 132]
    assert [e["evidence_id"] for e in diagnosis["evidence"]] == ["A1"]
    prompt = provider.calls[0].prompt
    assert "[A1] alert.png" in prompt.input and prompt.version == "v3"


async def test_with_no_corpus_hits_and_no_attachments_the_model_is_still_not_called(settings):
    provider = FakeProvider([])
    async with client(settings, provider, StaticRetriever([])) as c:
        resp = await c.post(f"/incidents/{await new_incident(c)}/diagnosis")

    assert resp.json()["diagnosis"]["no_answer_reason"] == "no_evidence" and provider.calls == []
