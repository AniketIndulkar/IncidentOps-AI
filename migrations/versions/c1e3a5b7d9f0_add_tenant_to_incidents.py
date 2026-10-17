"""add tenant to incidents and to idempotency keys

Revision ID: c1e3a5b7d9f0
Revises: b8f0a2c4e6d8
Create Date: 2026-10-17 22:00:00.000000

Incidents belong to a tenant (W10). Idempotency keys were global: a second tenant reusing a key
would have replayed the first tenant's incident. The tenant is now part of their primary key.
Existing rows belong to "default".
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c1e3a5b7d9f0"
down_revision: str | Sequence[str] | None = "b8f0a2c4e6d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "incidents",
        sa.Column("tenant_id", sa.String(length=63), server_default="default", nullable=False),
    )
    op.create_index(op.f("ix_incidents_tenant_id"), "incidents", ["tenant_id"], unique=False)
    op.add_column(
        "idempotency_keys",
        sa.Column("tenant_id", sa.String(length=63), server_default="default", nullable=False),
    )
    op.drop_constraint("idempotency_keys_pkey", "idempotency_keys", type_="primary")
    op.create_primary_key(
        "idempotency_keys_pkey", "idempotency_keys", ["tenant_id", "scope", "key"]
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("idempotency_keys_pkey", "idempotency_keys", type_="primary")
    op.create_primary_key("idempotency_keys_pkey", "idempotency_keys", ["scope", "key"])
    op.drop_column("idempotency_keys", "tenant_id")
    op.drop_index(op.f("ix_incidents_tenant_id"), table_name="incidents")
    op.drop_column("incidents", "tenant_id")
