"""add workflow events (append-only audit)

Revision ID: f3c5e7a9b1d4
Revises: e2b4d6f8a1c3
Create Date: 2026-10-17 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f3c5e7a9b1d4"
down_revision: str | Sequence[str] | None = "e2b4d6f8a1c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Shared by every append-only table (W08: workflow_events, then memories). TRUNCATE, used by the
# test suite between tests, is a different statement and is not blocked.
FORBID_CHANGE = """
CREATE OR REPLACE FUNCTION forbid_audit_change() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '% is append-only: % is not allowed', TG_TABLE_NAME, TG_OP;
END;
$$ LANGUAGE plpgsql
"""


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "workflow_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=63), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["run_id"], ["workflow_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_workflow_events_created_at"), "workflow_events", ["created_at"], unique=False
    )
    op.create_index(op.f("ix_workflow_events_run_id"), "workflow_events", ["run_id"], unique=False)
    op.execute(FORBID_CHANGE)
    op.execute(
        "CREATE TRIGGER workflow_events_append_only BEFORE UPDATE OR DELETE ON workflow_events "
        "FOR EACH ROW EXECUTE FUNCTION forbid_audit_change()"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TRIGGER workflow_events_append_only ON workflow_events")
    op.execute("DROP FUNCTION forbid_audit_change()")
    op.drop_index(op.f("ix_workflow_events_run_id"), table_name="workflow_events")
    op.drop_index(op.f("ix_workflow_events_created_at"), table_name="workflow_events")
    op.drop_table("workflow_events")
