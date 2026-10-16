from dataclasses import replace
from itertools import pairwise

import pytest

from incidentops_ai.retrieval.chunking import FixedChunker
from incidentops_ai.retrieval.documents import Document, parse_document


def make_doc(body: str, *, tenant_id: str = "default", doc_id: str = "d1") -> Document:
    raw = f"---\nid: {doc_id}\ntype: runbook\ntitle: Demo\nservice: demo-api\n---\n{body}"
    return parse_document(raw, source_path="demo.md", tenant_id=tenant_id)


WORDS = " ".join(f"word{i:03d}" for i in range(200))  # 1,599 characters, no newlines


def test_every_chunk_is_an_exact_slice_of_the_document():
    doc = make_doc(WORDS)

    chunks = FixedChunker(size=300, overlap=50).chunk(doc)

    assert len(chunks) > 3
    for chunk in chunks:
        assert chunk.text == doc.text[chunk.start : chunk.end]
        assert chunk.text == chunk.text.strip()


def test_chunks_respect_the_size_limit_and_cover_the_whole_text():
    doc = make_doc(WORDS)

    chunks = FixedChunker(size=300, overlap=50).chunk(doc)

    assert all(len(c.text) <= 300 for c in chunks)
    covered = set()
    for c in chunks:
        covered.update(range(c.start, c.end))
    uncovered = [i for i, ch in enumerate(doc.text) if not ch.isspace() and i not in covered]
    assert uncovered == []


def test_neighbouring_chunks_overlap_and_always_move_forward():
    doc = make_doc(WORDS)

    chunks = FixedChunker(size=300, overlap=60).chunk(doc)

    for earlier, later in pairwise(chunks):
        assert later.start > earlier.start
        assert later.start < earlier.end  # the seam is repeated, not lost
        assert earlier.end - later.start <= 60 + 10  # about `overlap`, give or take a word


def test_chunks_do_not_cut_words_when_there_is_whitespace_to_cut_at():
    doc = make_doc(WORDS)

    for chunk in FixedChunker(size=300, overlap=50).chunk(doc):
        assert chunk.start == 0 or doc.text[chunk.start - 1].isspace()
        assert chunk.end == len(doc.text) or doc.text[chunk.end].isspace()


def test_a_short_document_is_one_chunk():
    doc = make_doc("Just one short paragraph.")

    chunks = FixedChunker(size=500, overlap=50).chunk(doc)

    assert [c.text for c in chunks] == ["Just one short paragraph."]


def test_text_without_any_whitespace_is_cut_at_the_size_limit():
    doc = make_doc("x" * 1000)

    chunks = FixedChunker(size=300, overlap=50).chunk(doc)

    assert all(len(c.text) <= 300 for c in chunks)
    assert chunks[-1].end == 1000
    assert len(chunks) == 4


def test_ordinals_names_and_provenance_are_filled_in():
    doc = make_doc(WORDS, tenant_id="acme", doc_id="rb-9")

    chunks = FixedChunker(size=400, overlap=40).chunk(doc)

    assert [c.ordinal for c in chunks] == list(range(len(chunks)))
    first = chunks[0]
    assert first.chunker == "fixed-400-40"
    assert (first.tenant_id, first.doc_id, first.doc_type.value) == ("acme", "rb-9", "runbook")
    assert (first.service, first.title, first.source_path) == ("demo-api", "Demo", "demo.md")


def test_page_and_section_come_from_where_the_chunk_starts():
    body = "# Top\n\n" + "intro " * 30 + "\n\n## Fix\n\n" + "step " * 60 + "\f" + "later " * 60
    doc = make_doc(body)

    chunks = FixedChunker(size=200, overlap=20).chunk(doc)

    assert chunks[0].page == 1
    assert chunks[0].section == "Top"
    in_fix = next(c for c in chunks if c.text.startswith("step"))
    assert in_fix.section == "Top > Fix"
    last = chunks[-1]
    assert last.page == last.page_end == 2


def test_a_chunk_that_crosses_a_page_break_reports_both_pages():
    doc = make_doc("alpha " * 30 + "\f" + "beta " * 30)

    chunks = FixedChunker(size=2000, overlap=0).chunk(doc)

    assert len(chunks) == 1
    assert (chunks[0].page, chunks[0].page_end) == (1, 2)


def test_chunk_ids_are_stable_and_depend_on_tenant_document_chunker_and_position():
    doc = make_doc(WORDS)
    again = FixedChunker(size=300, overlap=50).chunk(doc)
    first = FixedChunker(size=300, overlap=50).chunk(doc)
    other_tenant = FixedChunker(size=300, overlap=50).chunk(make_doc(WORDS, tenant_id="acme"))
    other_config = FixedChunker(size=400, overlap=50).chunk(doc)

    assert [c.id for c in first] == [c.id for c in again]
    assert len({c.id for c in first}) == len(first)
    assert first[0].id != other_tenant[0].id
    assert first[0].id != other_config[0].id


def test_embedding_text_can_carry_the_title_and_section_as_context():
    doc = make_doc("# Demo\n\n## Fix\n\nRestart the pod.")
    chunk = replace(FixedChunker(size=500, overlap=0).chunk(doc)[0], section="Demo > Fix")

    assert chunk.embedding_text(with_context=False) == chunk.text
    with_context = chunk.embedding_text(with_context=True)
    assert with_context.startswith("Demo > Fix\n\n")  # H1 equals the title, so it is not repeated
    assert with_context.endswith(chunk.text)


def test_embedding_text_without_a_section_uses_just_the_title():
    doc = make_doc("plain text with no headings")
    chunk = FixedChunker(size=500, overlap=0).chunk(doc)[0]

    assert chunk.embedding_text(with_context=True).startswith("Demo\n\n")


@pytest.mark.parametrize("size,overlap", [(0, 0), (-5, 0), (100, -1), (100, 100), (100, 150)])
def test_nonsense_parameters_are_rejected(size, overlap):
    with pytest.raises(ValueError):
        FixedChunker(size=size, overlap=overlap)
