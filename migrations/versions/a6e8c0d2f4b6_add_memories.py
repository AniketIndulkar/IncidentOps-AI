"""add memories (append-only notes)

Revision ID: a6e8c0d2f4b6
Revises: f3c5e7a9b1d4
Create Date: 2026-10-17 19:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a6e8c0d2f4b6"
down_revision: str | Sequence[str] | None = "f3c5e7a9b1d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "memories",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=63), nullable=False),
        sa.Column("service", sa.String(length=128), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("author", sa.Text(), nullable=False),
        sa.Column("source_run_id", sa.Uuid(), nullable=True),
        sa.Column("supersedes", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["source_run_id"], ["workflow_runs.id"]),
        sa.ForeignKeyConstraint(["supersedes"], ["memories.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("supersedes"),
    )
    op.create_index("ix_memories_scope", "memories", ["tenant_id", "service"], unique=False)
    # forbid_audit_change() was created with workflow_events (f3c5e7a9b1d4).
    op.execute(
        "CREATE TRIGGER memories_append_only BEFORE UPDATE OR DELETE ON memories "
        "FOR EACH ROW EXECUTE FUNCTION forbid_audit_change()"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TRIGGER memories_append_only ON memories")
    op.drop_index("ix_memories_scope", table_name="memories")
    op.drop_table("memories")
