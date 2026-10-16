"""add llm run keys

Revision ID: c3f81d5a7b92
Revises: 0411fed4c072
Create Date: 2026-10-16 09:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3f81d5a7b92"
down_revision: str | Sequence[str] | None = "0411fed4c072"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "llm_run_keys",
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column(
            "claimed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["run_id"], ["llm_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("operation", "key"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("llm_run_keys")
