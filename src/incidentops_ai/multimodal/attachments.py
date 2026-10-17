"""Attachments: files an incident carries as evidence (AIENG-42).

A file is checked (type from its bytes, size, page count, readable), routed to the extractor that
the W06 benchmark chose for its kind, and stored once per incident with its text and line boxes.
At diagnosis time its best lines become evidence `A1..An` beside the retrieved `E1..E5`; they are
never put in the shared index, so one incident's files cannot surface in another's retrieval.

Routing (from evals/multimodal, docs/adr/003): a PDF with a text layer uses it; a scanned PDF is
OCR'd; an image goes to the vision model when one is configured, else to OCR.
"""

import dataclasses
import hashlib
import io
import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from incidentops_ai.diagnosis.evidence import Evidence, LineRegion
from incidentops_ai.multimodal.extract import (
    Extraction,
    ExtractionError,
    Extractor,
    TextLine,
    UnsupportedMediaError,
    detect_media_type,
)
from incidentops_ai.multimodal.orm import AttachmentRow
from incidentops_ai.retrieval.bm25 import Bm25Retriever
from incidentops_ai.retrieval.chunking import Chunk
from incidentops_ai.retrieval.documents import DocType
from incidentops_ai.retrieval.retriever import RetrievalFilter

logger = logging.getLogger(__name__)

MAX_BYTES = 5 * 1024 * 1024
MAX_PDF_PAGES = 10
MIN_PDF_CHARS = 200  # per page, to trust a text layer (the W06 rule)
CHUNK_CHARS = 800  # attachment lines are grouped into evidence chunks of about this size
PREVIEW_CHARS = 300


class AttachmentError(ValueError):
    """A file we will not accept. `code` is stable, for clients and tests."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _pdf_pages(data: bytes) -> int:
    import pypdfium2 as pdfium

    try:
        document = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as error:
        raise AttachmentError("unreadable_file", "the PDF could not be read") from error
    try:
        return len(document)
    finally:
        document.close()


def _check_image(data: bytes) -> None:
    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()  # also refuses decompression bombs (Pillow's pixel limit)
    except Image.DecompressionBombError as error:
        raise AttachmentError("file_too_large", "the image has too many pixels") from error
    except Exception as error:
        raise AttachmentError("unreadable_file", "the image could not be read") from error


class AttachmentProcessor:
    def __init__(self, *, pdf: Extractor, ocr: Extractor, vlm: Extractor | None) -> None:
        self._pdf = pdf
        self._ocr = ocr
        self._vlm = vlm

    async def process(self, data: bytes) -> Extraction:
        if len(data) > MAX_BYTES:
            raise AttachmentError(
                "file_too_large", f"files are limited to {MAX_BYTES // 1024**2} MB"
            )
        try:
            media_type = detect_media_type(data)
        except UnsupportedMediaError as error:
            raise AttachmentError("unsupported_media", str(error)) from error

        if media_type == "application/pdf":
            if _pdf_pages(data) > MAX_PDF_PAGES:
                raise AttachmentError(
                    "too_many_pages", f"PDFs are limited to {MAX_PDF_PAGES} pages"
                )
            text = await self._pdf.extract(data)
            if text.chars_per_page and min(text.chars_per_page) >= MIN_PDF_CHARS:
                return text
            return await self._ocr.extract(data)

        _check_image(data)
        if self._vlm is not None:
            try:
                return await self._vlm.extract(data)
            except ExtractionError as error:
                # A transcript is better, but OCR text is still evidence: degrade, don't fail.
                logger.warning("Vision transcript failed, falling back to OCR: %s", error)
        return await self._ocr.extract(data)


class AttachmentRead(BaseModel):
    id: uuid.UUID
    incident_id: uuid.UUID
    filename: str
    media_type: str
    size_bytes: int
    sha256: str
    method: str
    pages: int
    line_count: int
    preview: str
    created_at: datetime


@dataclass(frozen=True)
class AttachmentText:
    id: str
    filename: str
    method: str
    lines: tuple[TextLine, ...]

    def redacted(self) -> "AttachmentText":
        """The same text with PII and secrets replaced (W10), line boxes kept."""
        from incidentops_ai.security.pii import redact

        lines = tuple(dataclasses.replace(line, text=redact(line.text).text) for line in self.lines)
        return dataclasses.replace(self, lines=lines)


def _read(row: AttachmentRow) -> AttachmentRead:
    return AttachmentRead(
        id=row.id,
        incident_id=row.incident_id,
        filename=row.filename,
        media_type=row.media_type,
        size_bytes=row.size_bytes,
        sha256=row.sha256,
        method=row.method,
        pages=row.pages,
        line_count=len(row.lines),
        preview=row.text[:PREVIEW_CHARS],
        created_at=row.created_at,
    )


def _line(record: dict) -> TextLine:
    box = tuple(record["box"]) if record.get("box") else None
    return TextLine(record["text"], record["page"], box, record.get("confidence"))


class AttachmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _existing(self, incident_id: uuid.UUID, sha256: str) -> AttachmentRow | None:
        return (
            await self._session.execute(
                select(AttachmentRow).where(
                    AttachmentRow.incident_id == incident_id, AttachmentRow.sha256 == sha256
                )
            )
        ).scalar_one_or_none()

    async def add(
        self,
        *,
        tenant_id: str,
        incident_id: uuid.UUID,
        filename: str,
        media_type: str,
        data: bytes,
        extraction: Extraction,
    ) -> tuple[AttachmentRead, bool]:
        """Store the file, or return the copy already attached. The bool says whether it is new."""
        sha256 = hashlib.sha256(data).hexdigest()
        if existing := await self._existing(incident_id, sha256):
            return _read(existing), False
        row = AttachmentRow(
            tenant_id=tenant_id,
            incident_id=incident_id,
            filename=filename[:255] or "attachment",
            media_type=media_type,
            size_bytes=len(data),
            sha256=sha256,
            data=data,
            method=extraction.method,
            pages=extraction.pages,
            lines=[
                {
                    "text": l.text,
                    "page": l.page,
                    "box": list(l.box) if l.box else None,
                    "confidence": l.confidence,
                }
                for l in extraction.lines
            ],
            text=extraction.text,
        )
        try:
            async with self._session.begin_nested():
                self._session.add(row)
        except IntegrityError:
            # Two uploads of the same file raced; the other one won. Return its row.
            existing = await self._existing(incident_id, sha256)
            if existing is None:
                raise
            return _read(existing), False
        return _read(row), True

    async def list_for_incident(self, incident_id: uuid.UUID) -> list[AttachmentRead]:
        rows = await self._session.execute(
            select(AttachmentRow)
            .where(AttachmentRow.incident_id == incident_id)
            .order_by(AttachmentRow.created_at, AttachmentRow.id)
        )
        return [_read(row) for row in rows.scalars()]

    async def texts_for_incident(self, incident_id: uuid.UUID) -> list[AttachmentText]:
        rows = await self._session.execute(
            select(AttachmentRow)
            .where(AttachmentRow.incident_id == incident_id)
            .order_by(AttachmentRow.created_at, AttachmentRow.id)
        )
        return [
            AttachmentText(
                str(row.id), row.filename, row.method, tuple(_line(r) for r in row.lines)
            )
            for row in rows.scalars()
        ]


def _chunks(source: AttachmentText, tenant_id: str) -> list[tuple[Chunk, tuple[LineRegion, ...]]]:
    """Group an attachment's lines into chunks of about CHUNK_CHARS, keeping each line's box."""
    groups: list[list[TextLine]] = [[]]
    for line in source.lines:
        if groups[-1] and sum(len(l.text) + 1 for l in groups[-1]) + len(line.text) > CHUNK_CHARS:
            groups.append([])
        groups[-1].append(line)
    chunks = []
    offset = 0
    for ordinal, group in enumerate(g for g in groups if g):
        regions, position = [], 0
        for line in group:
            regions.append(LineRegion(position, position + len(line.text), line.page, line.box))
            position += len(line.text) + 1
        text = "\n".join(line.text for line in group)
        chunk = Chunk(
            tenant_id=tenant_id,
            doc_id=f"att-{source.id}",
            doc_type=DocType.ATTACHMENT,
            service=None,
            title=source.filename,
            source_path=f"attachment:{source.id}",
            chunker="attachment-lines",
            ordinal=ordinal,
            start=offset,
            end=offset + len(text),
            page=group[0].page,
            page_end=group[-1].page,
            section=None,
            text=text,
        )
        chunks.append((chunk, tuple(regions)))
        offset += len(text) + 1
    return chunks


async def attachment_evidence(
    query: str, sources: Sequence[AttachmentText], *, tenant_id: str, k: int = 3
) -> list[Evidence]:
    """The incident's own attachment chunks most related to its text, numbered A1..Ak.

    Ranked by BM25 against the incident text; when fewer than k chunks share a word with it, the
    rest are filled in upload order, because a file attached to the incident is its evidence even
    when its words differ from the report's."""
    candidates = {
        chunk.id: (chunk, regions, source.method)
        for source in sources
        for chunk, regions in _chunks(source, tenant_id)
    }
    if not candidates:
        return []
    ranked = await Bm25Retriever([chunk for chunk, _, _ in candidates.values()]).retrieve(
        query, k=k, filters=RetrievalFilter(tenant_id=tenant_id)
    )
    chosen = [r.chunk.id for r in ranked]
    chosen += [chunk_id for chunk_id in candidates if chunk_id not in chosen]
    return [
        Evidence(f"A{number}", chunk, regions=regions, extraction=method)
        for number, (chunk, regions, method) in enumerate(
            (candidates[chunk_id] for chunk_id in chosen[:k]), start=1
        )
    ]
