"""add attachments

Revision ID: d7a1c3e5f9b2
Revises: 9e3c71d4b8a2
Create Date: 2026-10-17 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "d7a1c3e5f9b2"
down_revision: str | Sequence[str] | None = "9e3c71d4b8a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "attachments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=63), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("media_type", sa.String(length=32), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("method", sa.String(length=32), nullable=False),
        sa.Column("pages", sa.Integer(), nullable=False),
        sa.Column("lines", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("incident_id", "sha256", name="uq_attachments_incident_sha256"),
    )
    op.create_index("ix_attachments_incident_id", "attachments", ["incident_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_attachments_incident_id", table_name="attachments")
    op.drop_table("attachments")
