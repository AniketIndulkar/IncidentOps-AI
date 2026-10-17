"""add workflow runs

Revision ID: e2b4d6f8a1c3
Revises: d7a1c3e5f9b2
Create Date: 2026-10-17 15:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "e2b4d6f8a1c3"
down_revision: str | Sequence[str] | None = "d7a1c3e5f9b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # LangGraph's checkpoint tables are created by AsyncPostgresSaver.setup() at startup, which
    # versions them itself; only our own run index is an Alembic table.
    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=63), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("route", sa.String(length=32), nullable=True),
        sa.Column("reasons", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("error", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_workflow_runs_incident_id", "workflow_runs", ["incident_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_workflow_runs_incident_id", table_name="workflow_runs")
    op.drop_table("workflow_runs")
