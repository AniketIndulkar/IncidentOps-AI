"""Embed the stored chunks so they can be searched.

    uv run python -m incidentops_ai.retrieval.index [--context]

Only chunks without a vector, or with one from another model or context setting, are embedded,
so it is safe to run after every ingestion.
"""

import argparse
import asyncio
from collections.abc import Sequence

from incidentops_ai.config import Settings
from incidentops_ai.db import create_engine, create_session_factory
from incidentops_ai.retrieval.documents import DEFAULT_TENANT
from incidentops_ai.retrieval.embeddings import Embedder
from incidentops_ai.retrieval.factory import build_embedder
from incidentops_ai.retrieval.store import index_chunks


async def run_index(
    settings: Settings,
    embedder: Embedder,
    *,
    tenant_id: str = DEFAULT_TENANT,
    with_context: bool = False,
) -> int:
    """Embed this tenant's stale chunks in one transaction; returns how many were embedded.

    If any embedding call fails, the transaction rolls back and existing vectors are kept.
    """
    engine = create_engine(settings)
    try:
        # `session.begin()` commits when the block exits normally and rolls back on an exception.
        async with create_session_factory(engine)() as session, session.begin():
            return await index_chunks(
                session, embedder, tenant_id=tenant_id, with_context=with_context
            )
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> None:
    """Command-line entry point. `argv` is for tests; None means the real command line."""
    parser = argparse.ArgumentParser(description="Embed stored chunks into pgvector.")
    parser.add_argument("--tenant", default=DEFAULT_TENANT)
    parser.add_argument(
        "--context", action="store_true", help="embed 'title > section' together with each chunk"
    )
    args = parser.parse_args(argv)

    settings = Settings()
    embedded = asyncio.run(
        run_index(
            settings, build_embedder(settings), tenant_id=args.tenant, with_context=args.context
        )
    )
    print(f"embedded {embedded} chunks with {settings.embedding_model}")


if __name__ == "__main__":
    main()
