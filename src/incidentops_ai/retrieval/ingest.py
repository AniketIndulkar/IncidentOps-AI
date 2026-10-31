"""Load the corpus into PostgreSQL: parse, chunk, store with provenance.

    uv run python -m incidentops_ai.retrieval.ingest data/corpus --chunker fixed-2000-200

Repeatable: documents whose file, parser version and chunker are unchanged are skipped; changed
ones are replaced together with their chunks. Everything runs in one transaction, so a failure
leaves the previous state untouched.

Ingestion stores text only. Embeddings are added afterwards by `index.py`, so the two steps can
fail, be re-run and be changed (e.g. a new embedding model) independently.
"""

import argparse
import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from incidentops_ai.config import Settings
from incidentops_ai.db import create_engine, create_session_factory
from incidentops_ai.retrieval.chunking import Chunker, chunker_from_name
from incidentops_ai.retrieval.documents import (
    DEFAULT_TENANT,
    PARSER_VERSION,
    Document,
    load_corpus,
)
from incidentops_ai.retrieval.orm import ChunkRow, DocumentRow

# Winner of the AIENG-26 chunking experiment (evals/retrieval/runs/2026-10-16-chunking-text-embedding-3-small.md).
DEFAULT_CHUNKER = "fixed-2000-200"


@dataclass(frozen=True)
class IngestReport:
    """What one ingestion run did, counted in documents (except `chunks`)."""

    added: int  # new documents
    updated: int  # documents whose file, parser version or chunker changed, re-chunked
    unchanged: int  # skipped: stored chunks are still correct
    removed: int  # deleted because they left the corpus (only with prune)
    chunks: int  # written by this run


async def ingest_documents(
    session: AsyncSession,
    documents: Sequence[Document],
    chunker: Chunker,
    *,
    tenant_id: str = DEFAULT_TENANT,
    prune: bool = False,
) -> IngestReport:
    """Store `documents` for one tenant. The caller owns the transaction (commit or roll back).

    Args:
        session: open session inside a transaction; this function flushes but never commits.
        documents: the parsed corpus, all belonging to `tenant_id`.
        chunker: how to cut changed or new documents into chunks.
        tenant_id: whose documents these are.
        prune: also delete stored documents that are not in `documents`. Off by default, so a
            run against a wrong or partial directory cannot wipe the table.
    """
    for document in documents:
        if document.tenant_id != tenant_id:
            raise ValueError(
                f"document {document.id!r} belongs to tenant {document.tenant_id!r}, "
                f"not {tenant_id!r}"
            )

    # Everything this tenant already has, keyed by document id.
    stored = {
        row.id: row
        for row in (
            await session.execute(select(DocumentRow).where(DocumentRow.tenant_id == tenant_id))
        ).scalars()
    }
    added = updated = unchanged = chunks_written = 0
    for document in documents:
        previous = stored.get(document.id)
        # Same file, parsed by the same parser version, cut by the same chunker: nothing to do.
        if (
            previous is not None
            and previous.content_sha256 == document.content_sha256
            and previous.parser_version == PARSER_VERSION
            and previous.chunker == chunker.name
        ):
            unchanged += 1
            continue

        chunks = chunker.chunk(document)
        if previous is not None:
            # Chunks go with the document (ON DELETE CASCADE). A Core delete runs now, before the
            # inserts below, which a unit-of-work delete + insert of the same key would not guarantee.
            await session.execute(
                delete(DocumentRow).where(
                    DocumentRow.tenant_id == tenant_id, DocumentRow.id == document.id
                )
            )
            updated += 1
        else:
            added += 1
        session.add(
            DocumentRow(
                tenant_id=tenant_id,
                id=document.id,
                type=document.type.value,
                title=document.title,
                service=document.service,
                source_path=document.source_path,
                content_sha256=document.content_sha256,
                parser_version=PARSER_VERSION,
                chunker=chunker.name,
                text=document.text,
            )
        )
        await session.flush()  # the document row must exist before its chunks
        # New chunks have no embedding yet; `index.py` fills it in.
        session.add_all(
            ChunkRow(
                id=chunk.id,
                tenant_id=chunk.tenant_id,
                doc_id=chunk.doc_id,
                ordinal=chunk.ordinal,
                chunker=chunk.chunker,
                doc_type=chunk.doc_type.value,
                service=chunk.service,
                start_char=chunk.start,
                end_char=chunk.end,
                page=chunk.page,
                page_end=chunk.page_end,
                section=chunk.section,
                text=chunk.text,
            )
            for chunk in chunks
        )
        chunks_written += len(chunks)

    removed = 0
    if prune:
        keep = {document.id for document in documents}
        gone = [doc_id for doc_id in stored if doc_id not in keep]
        if gone:
            await session.execute(
                delete(DocumentRow).where(
                    DocumentRow.tenant_id == tenant_id, DocumentRow.id.in_(gone)
                )
            )
        removed = len(gone)

    await session.flush()
    return IngestReport(added, updated, unchanged, removed, chunks_written)


async def run_ingest(
    settings: Settings,
    corpus_dir: Path,
    *,
    tenant_id: str = DEFAULT_TENANT,
    chunker_name: str = DEFAULT_CHUNKER,
    prune: bool = False,
) -> IngestReport:
    """Parse `corpus_dir`, then ingest it in one transaction that commits only on success."""
    documents = load_corpus(
        corpus_dir, tenant_id=tenant_id
    )  # parse everything before touching the DB
    chunker = chunker_from_name(chunker_name)
    engine = create_engine(settings)
    try:
        # `session.begin()` commits when the block exits normally and rolls back on an exception.
        async with create_session_factory(engine)() as session, session.begin():
            return await ingest_documents(
                session, documents, chunker, tenant_id=tenant_id, prune=prune
            )
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> None:
    """Command-line entry point. `argv` is for tests; None means the real command line."""
    parser = argparse.ArgumentParser(description="Load the retrieval corpus into PostgreSQL.")
    parser.add_argument("corpus", nargs="?", type=Path, default=Path("data/corpus"))
    parser.add_argument("--tenant", default=DEFAULT_TENANT)
    parser.add_argument("--chunker", default=DEFAULT_CHUNKER, help="e.g. fixed-2000-200")
    parser.add_argument(
        "--prune", action="store_true", help="delete documents no longer in the corpus"
    )
    args = parser.parse_args(argv)

    report = asyncio.run(
        run_ingest(
            Settings(),
            args.corpus,
            tenant_id=args.tenant,
            chunker_name=args.chunker,
            prune=args.prune,
        )
    )
    print(
        f"added {report.added}, updated {report.updated}, unchanged {report.unchanged}, "
        f"removed {report.removed}; wrote {report.chunks} chunks"
    )


if __name__ == "__main__":
    main()
