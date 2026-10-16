"""Tables for the retrieval corpus: documents and the chunks cut from them.

Everything is keyed by tenant first. The tenant is carried on every row from the start so that
permission filtering (W10) is a WHERE clause, not a retrofit.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from incidentops_ai.db import Base


class DocumentRow(Base):
    __tablename__ = "documents"

    tenant_id: Mapped[str] = mapped_column(String(63), primary_key=True)
    id: Mapped[str] = mapped_column(
        String(100), primary_key=True
    )  # the id in the file's front matter
    type: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(Text)
    service: Mapped[str | None] = mapped_column(String(63))
    source_path: Mapped[str] = mapped_column(Text)
    content_sha256: Mapped[str] = mapped_column(String(64))
    parser_version: Mapped[int] = mapped_column(Integer)
    chunker: Mapped[str] = mapped_column(String(64))  # configuration the stored chunks came from
    text: Mapped[str] = mapped_column(Text)  # cleaned body; chunk offsets index into it
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ChunkRow(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "doc_id"],
            ["documents.tenant_id", "documents.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("tenant_id", "doc_id", "ordinal"),
        Index("ix_chunks_tenant_service", "tenant_id", "service"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)  # Chunk.id, stable across runs
    tenant_id: Mapped[str] = mapped_column(String(63))
    doc_id: Mapped[str] = mapped_column(String(100))
    ordinal: Mapped[int] = mapped_column(Integer)
    chunker: Mapped[str] = mapped_column(String(64))
    # Copied from the document so metadata filters need no join.
    doc_type: Mapped[str] = mapped_column(String(32))
    service: Mapped[str | None] = mapped_column(String(63))
    start_char: Mapped[int] = mapped_column(Integer)
    end_char: Mapped[int] = mapped_column(Integer)
    page: Mapped[int] = mapped_column(Integer)
    page_end: Mapped[int] = mapped_column(Integer)
    section: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
