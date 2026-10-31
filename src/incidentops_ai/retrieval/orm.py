"""Tables for the retrieval corpus: documents and the chunks cut from them.

Everything is keyed by tenant first. The tenant is carried on every row from the start so that
permission filtering (W10) is a WHERE clause, not a retrofit.

These are SQLAlchemy ORM models: each class is a table, each `Mapped[...]` attribute a column.
The schema itself is created by the Alembic migrations, which must stay in step with this file.
"""

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
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

# Vectors of every model are stored at this size (OpenAI's text-embedding-3 models can be asked
# for it). A different size is a new column and a re-embed, never a silent mix.
EMBEDDING_DIMENSIONS = 1536


class DocumentRow(Base):
    """One corpus document per tenant, as of its last ingestion.

    `content_sha256`, `parser_version` and `chunker` together decide whether a re-ingest can skip
    this document: if all three match, its stored chunks are still correct.
    """

    __tablename__ = "documents"

    # Composite primary key (tenant_id, id): two tenants may use the same document id.
    tenant_id: Mapped[str] = mapped_column(String(63), primary_key=True)
    id: Mapped[str] = mapped_column(
        String(100), primary_key=True
    )  # the id in the file's front matter
    type: Mapped[str] = mapped_column(String(32))  # a `DocType` value
    title: Mapped[str] = mapped_column(Text)
    service: Mapped[str | None] = mapped_column(String(63))
    source_path: Mapped[str] = mapped_column(Text)  # corpus-relative path, for citations
    content_sha256: Mapped[str] = mapped_column(String(64))  # hash of the raw file
    parser_version: Mapped[int] = mapped_column(Integer)  # `documents.PARSER_VERSION` used
    chunker: Mapped[str] = mapped_column(String(64))  # configuration the stored chunks came from
    text: Mapped[str] = mapped_column(Text)  # cleaned body; chunk offsets index into it
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ChunkRow(Base):
    """One chunk of a document, and (once indexed) its embedding."""

    __tablename__ = "chunks"
    __table_args__ = (
        # A chunk belongs to a document of the same tenant. Deleting the document deletes its
        # chunks, which is how re-ingesting a changed document replaces them.
        ForeignKeyConstraint(
            ["tenant_id", "doc_id"],
            ["documents.tenant_id", "documents.id"],
            ondelete="CASCADE",
        ),
        # No two chunks may claim the same position in one document.
        UniqueConstraint("tenant_id", "doc_id", "ordinal"),
        # Serves the tenant and service filters applied before ranking.
        Index("ix_chunks_tenant_service", "tenant_id", "service"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)  # Chunk.id, stable across runs
    tenant_id: Mapped[str] = mapped_column(String(63))
    doc_id: Mapped[str] = mapped_column(String(100))
    ordinal: Mapped[int] = mapped_column(Integer)  # 0-based position within the document
    chunker: Mapped[str] = mapped_column(String(64))
    # Copied from the document so metadata filters need no join.
    doc_type: Mapped[str] = mapped_column(String(32))
    service: Mapped[str | None] = mapped_column(String(63))
    # Exclusive character range into DocumentRow.text: text == document.text[start_char:end_char].
    start_char: Mapped[int] = mapped_column(Integer)
    end_char: Mapped[int] = mapped_column(Integer)
    page: Mapped[int] = mapped_column(Integer)  # page where the chunk starts
    page_end: Mapped[int] = mapped_column(Integer)  # page where it ends
    section: Mapped[str | None] = mapped_column(Text)  # heading path, e.g. "Runbook > Fix"
    text: Mapped[str] = mapped_column(Text)
    # Filled by indexing (store.index_chunks). NULL means "not embedded yet". The model and the
    # context setting say how, so a vector is never compared with one made differently.
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    embedding_model: Mapped[str | None] = mapped_column(String(128))
    # Whether title and section were prepended before embedding (`Chunk.embedding_text`).
    embedding_context: Mapped[bool | None] = mapped_column(Boolean)
