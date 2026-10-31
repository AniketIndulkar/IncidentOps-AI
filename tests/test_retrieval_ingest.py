from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from incidentops_ai.config import Settings
from incidentops_ai.retrieval.chunking import FixedChunker, chunker_from_name
from incidentops_ai.retrieval.documents import Document, load_corpus, parse_document
from incidentops_ai.retrieval.ingest import ingest_documents, run_ingest
from incidentops_ai.retrieval.orm import ChunkRow, DocumentRow

CORPUS = Path(__file__).resolve().parent.parent / "data" / "corpus"
BODY = "# Pool\n\n## Fix\n\n" + " ".join(f"word{i:03d}" for i in range(150))


def doc(
    doc_id: str = "d1", *, body: str = BODY, tenant: str = "default", title: str = "T"
) -> Document:
    raw = f"---\nid: {doc_id}\ntype: runbook\ntitle: {title}\nservice: orders-api\n---\n{body}"
    return parse_document(raw, source_path=f"{doc_id}.md", tenant_id=tenant)


async def ingest(factory, documents, chunker=None, **kwargs):
    async with factory() as session, session.begin():
        return await ingest_documents(
            session, documents, chunker or FixedChunker(size=300, overlap=30), **kwargs
        )


async def count(factory, model) -> int:
    async with factory() as session:
        return (await session.execute(select(func.count()).select_from(model))).scalar_one()


async def test_new_documents_are_stored_with_their_chunks(
    session_factory: async_sessionmaker[AsyncSession],
):
    report = await ingest(session_factory, [doc("d1"), doc("d2")])

    assert (report.added, report.updated, report.unchanged, report.removed) == (2, 0, 0, 0)
    assert report.chunks == await count(session_factory, ChunkRow) > 4
    assert await count(session_factory, DocumentRow) == 2


async def test_every_chunk_keeps_its_provenance(session_factory: async_sessionmaker[AsyncSession]):
    source = doc("d1")
    await ingest(session_factory, [source])

    async with session_factory() as session:
        rows = (await session.execute(select(ChunkRow).order_by(ChunkRow.ordinal))).scalars().all()
        stored = (await session.execute(select(DocumentRow))).scalar_one()

    assert stored.source_path == "d1.md"
    assert stored.text == source.text
    assert [r.ordinal for r in rows] == list(range(len(rows)))
    for row in rows:
        assert row.text == stored.text[row.start_char : row.end_char]
        assert row.tenant_id == "default"
        assert row.doc_id == "d1"
        assert row.doc_type == "runbook"
        assert row.service == "orders-api"
        assert row.chunker == "fixed-300-30"
        assert 1 <= row.page <= row.page_end
    assert rows[0].section == "Pool"
    assert any(r.section == "Pool > Fix" for r in rows)


async def test_running_the_same_ingestion_twice_changes_nothing(
    session_factory: async_sessionmaker[AsyncSession],
):
    await ingest(session_factory, [doc("d1")])
    async with session_factory() as session:
        before = (
            (await session.execute(select(ChunkRow.id).order_by(ChunkRow.ordinal))).scalars().all()
        )
        ingested_at = (await session.execute(select(DocumentRow.ingested_at))).scalar_one()

    report = await ingest(session_factory, [doc("d1")])

    assert (report.added, report.updated, report.unchanged) == (0, 0, 1)
    async with session_factory() as session:
        after = (
            (await session.execute(select(ChunkRow.id).order_by(ChunkRow.ordinal))).scalars().all()
        )
        assert (await session.execute(select(DocumentRow.ingested_at))).scalar_one() == ingested_at
    assert after == before


async def test_a_changed_document_replaces_its_old_chunks(
    session_factory: async_sessionmaker[AsyncSession],
):
    await ingest(session_factory, [doc("d1", body="# A\n\n" + "old " * 400)])

    report = await ingest(session_factory, [doc("d1", body="# A\n\nbrand new and much shorter")])

    assert (report.added, report.updated, report.unchanged) == (0, 1, 0)
    async with session_factory() as session:
        texts = (await session.execute(select(ChunkRow.text))).scalars().all()
    assert texts == ["# A\n\nbrand new and much shorter"]


async def test_a_different_chunker_rechunks_unchanged_documents(
    session_factory: async_sessionmaker[AsyncSession],
):
    await ingest(session_factory, [doc("d1")], FixedChunker(size=300, overlap=30))

    report = await ingest(session_factory, [doc("d1")], FixedChunker(size=600, overlap=30))

    assert report.updated == 1
    async with session_factory() as session:
        names = (await session.execute(select(ChunkRow.chunker).distinct())).scalars().all()
    assert names == ["fixed-600-30"]


async def test_prune_removes_documents_that_left_the_corpus_but_only_for_that_tenant(
    session_factory: async_sessionmaker[AsyncSession],
):
    await ingest(session_factory, [doc("keep"), doc("gone")])
    await ingest(session_factory, [doc("other", tenant="acme")], tenant_id="acme")

    report = await ingest(session_factory, [doc("keep")], prune=True)

    assert report.removed == 1
    async with session_factory() as session:
        rows = (await session.execute(select(DocumentRow.tenant_id, DocumentRow.id))).all()
        chunk_docs = (await session.execute(select(ChunkRow.doc_id).distinct())).scalars().all()
    assert sorted(tuple(r) for r in rows) == [("acme", "other"), ("default", "keep")]
    assert sorted(chunk_docs) == ["keep", "other"]


async def test_without_prune_missing_documents_stay(
    session_factory: async_sessionmaker[AsyncSession],
):
    await ingest(session_factory, [doc("keep"), doc("gone")])

    report = await ingest(session_factory, [doc("keep")])

    assert report.removed == 0
    assert await count(session_factory, DocumentRow) == 2


async def test_the_same_document_id_can_exist_in_two_tenants(
    session_factory: async_sessionmaker[AsyncSession],
):
    await ingest(session_factory, [doc("d1")])
    await ingest(
        session_factory, [doc("d1", tenant="acme", body="# Z\n\nacme only")], tenant_id="acme"
    )

    assert await count(session_factory, DocumentRow) == 2
    async with session_factory() as session:
        acme = (
            (await session.execute(select(ChunkRow.text).where(ChunkRow.tenant_id == "acme")))
            .scalars()
            .all()
        )
    assert acme == ["# Z\n\nacme only"]


async def test_documents_of_another_tenant_are_refused(
    session_factory: async_sessionmaker[AsyncSession],
):
    with pytest.raises(ValueError, match="tenant"):
        await ingest(session_factory, [doc("d1", tenant="acme")], tenant_id="default")

    assert await count(session_factory, DocumentRow) == 0


async def test_a_failure_part_way_leaves_nothing_behind(
    session_factory: async_sessionmaker[AsyncSession],
):
    class Exploding:
        name = "exploding"

        def __init__(self) -> None:
            self.calls = 0

        def chunk(self, document):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("boom")
            return FixedChunker(size=300, overlap=30).chunk(document)

    with pytest.raises(RuntimeError):
        await ingest(session_factory, [doc("d1"), doc("d2")], Exploding())

    assert await count(session_factory, DocumentRow) == 0
    assert await count(session_factory, ChunkRow) == 0


async def test_the_real_corpus_ingests_with_full_provenance(
    session_factory: async_sessionmaker[AsyncSession],
):
    documents = load_corpus(CORPUS)

    report = await ingest(session_factory, documents, FixedChunker(size=1000, overlap=100))

    assert report.added == len(documents) == 18
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(ChunkRow, DocumentRow.source_path, DocumentRow.text).join(
                    DocumentRow,
                    (DocumentRow.tenant_id == ChunkRow.tenant_id)
                    & (DocumentRow.id == ChunkRow.doc_id),
                )
            )
        ).all()
    assert len(rows) == report.chunks
    assert all(source_path and row.page >= 1 for row, source_path, _ in rows)
    assert all(row.text == full[row.start_char : row.end_char] for row, _, full in rows)
    # The paged export keeps its page numbers, so a citation can say "page 2".
    failover = [row for row, path, _ in rows if path.endswith("orders-db-failover.txt")]
    pages = {n for row in failover for n in range(row.page, row.page_end + 1)}
    assert pages == {1, 2, 3}


def test_chunker_names_round_trip():
    assert chunker_from_name("fixed-500-50").name == "fixed-500-50"
    with pytest.raises(ValueError, match="unknown chunker"):
        chunker_from_name("semantic-magic")


async def test_run_ingest_loads_a_directory(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
):
    (tmp_path / "a.md").write_text("---\nid: a\ntype: runbook\ntitle: A\n---\n# A\n\ntext here\n")

    report = await run_ingest(settings, tmp_path, chunker_name="fixed-500-50")

    assert report.added == 1
    assert await count(session_factory, DocumentRow) == 1
