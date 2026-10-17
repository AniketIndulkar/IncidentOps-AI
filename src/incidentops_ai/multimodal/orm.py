"""Files attached to an incident, with the text extracted from them (W06)."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from incidentops_ai.db import Base


class AttachmentRow(Base):
    __tablename__ = "attachments"
    # The same file attached twice to one incident is stored once.
    __table_args__ = (
        UniqueConstraint("incident_id", "sha256", name="uq_attachments_incident_sha256"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(63))
    incident_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), index=True
    )
    filename: Mapped[str] = mapped_column(String(255))  # as uploaded; never used as a path
    media_type: Mapped[str] = mapped_column(String(32))  # from the bytes, not from the upload
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    data: Mapped[bytes] = mapped_column(LargeBinary)
    method: Mapped[str] = mapped_column(String(32))  # pdf-text | ocr | vlm-transcript
    pages: Mapped[int] = mapped_column(Integer)
    lines: Mapped[list] = mapped_column(JSONB)  # [{text, page, box, confidence}]
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), server_default=func.now()
    )
