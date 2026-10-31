"""add chunk embeddings (pgvector)

Revision ID: 9e3c71d4b8a2
Revises: 5b1f0e7c2a94
Create Date: 2026-10-16 18:30:00.000000

"""

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9e3c71d4b8a2"
down_revision: str | Sequence[str] | None = "5b1f0e7c2a94"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Needs a Postgres with pgvector installed (the pgvector/pgvector image, or the extension on
    # a managed service) and a role allowed to create extensions.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.add_column(
        "chunks", sa.Column("embedding", pgvector.sqlalchemy.Vector(dim=1536), nullable=True)
    )
    op.add_column("chunks", sa.Column("embedding_model", sa.String(length=128), nullable=True))
    op.add_column("chunks", sa.Column("embedding_context", sa.Boolean(), nullable=True))
    # No vector index on purpose: an exact scan is faster than HNSW at this size and never misses.
    # Add one when the table passes roughly 10,000 chunks (docs/roadmap-w03.md).


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("chunks", "embedding_context")
    op.drop_column("chunks", "embedding_model")
    op.drop_column("chunks", "embedding")
    # The extension stays: dropping it could break other databases' objects that rely on it.
