"""add documents and chunks

Revision ID: 5b1f0e7c2a94
Revises: c3f81d5a7b92
Create Date: 2026-10-16 17:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5b1f0e7c2a94"
down_revision: str | Sequence[str] | None = "c3f81d5a7b92"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "documents",
        sa.Column("tenant_id", sa.String(length=63), nullable=False),
        sa.Column("id", sa.String(length=100), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("service", sa.String(length=63), nullable=True),
        sa.Column("source_path", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("parser_version", sa.Integer(), nullable=False),
        sa.Column("chunker", sa.String(length=64), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("tenant_id", "id"),
    )
    op.create_table(
        "chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=63), nullable=False),
        sa.Column("doc_id", sa.String(length=100), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("chunker", sa.String(length=64), nullable=False),
        sa.Column("doc_type", sa.String(length=32), nullable=False),
        sa.Column("service", sa.String(length=63), nullable=True),
        sa.Column("start_char", sa.Integer(), nullable=False),
        sa.Column("end_char", sa.Integer(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=False),
        sa.Column("page_end", sa.Integer(), nullable=False),
        sa.Column("section", sa.Text(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "doc_id"],
            ["documents.tenant_id", "documents.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "doc_id", "ordinal"),
    )
    op.create_index("ix_chunks_tenant_service", "chunks", ["tenant_id", "service"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_chunks_tenant_service", table_name="chunks")
    op.drop_table("chunks")
    op.drop_table("documents")
