from pathlib import Path

import pytest

from incidentops_ai.retrieval.documents import CorpusError, DocType, load_corpus, parse_document

RAW = """---
id: rb-demo
type: runbook
title: Demo runbook
service: demo-api
---

# Demo runbook

Intro text.

## Fix

Do the thing.
"""


def test_parse_reads_front_matter_and_body():
    doc = parse_document(RAW, source_path="runbooks/demo.md")

    assert doc.id == "rb-demo"
    assert doc.type is DocType.RUNBOOK
    assert doc.title == "Demo runbook"
    assert doc.service == "demo-api"
    assert doc.tenant_id == "default"
    assert doc.source_path == "runbooks/demo.md"
    assert doc.text.startswith("# Demo runbook")
    assert "---" not in doc.text


def test_quoted_title_may_contain_a_colon():
    raw = RAW.replace("title: Demo runbook", 'title: "Postmortem: demo outage"')

    assert parse_document(raw, source_path="x.md").title == "Postmortem: demo outage"


def test_empty_service_means_no_service():
    raw = RAW.replace("service: demo-api", "service:")

    assert parse_document(raw, source_path="x.md").service is None


def test_tenant_is_taken_from_the_caller_not_the_file():
    doc = parse_document(RAW, source_path="x.md", tenant_id="acme")

    assert doc.tenant_id == "acme"


@pytest.mark.parametrize(
    "raw",
    [
        "# No front matter\n\nBody.",
        "---\nid: a\ntype: runbook\n# never closed\n",
        RAW.replace("id: rb-demo\n", ""),
        RAW.replace("title: Demo runbook\n", ""),
        RAW.replace("type: runbook", "type: memo"),
    ],
)
def test_bad_front_matter_is_rejected_with_the_file_name(raw):
    with pytest.raises(CorpusError, match="runbooks/bad.md"):
        parse_document(raw, source_path="runbooks/bad.md")


def test_empty_body_is_rejected():
    raw = "---\nid: a\ntype: runbook\ntitle: T\n---\n\n   \n"

    with pytest.raises(CorpusError, match="empty"):
        parse_document(raw, source_path="x.md")


def test_cleaning_normalises_line_endings_and_blank_runs():
    raw = "---\nid: a\ntype: runbook\ntitle: T\n---\r\nline one   \r\n\r\n\r\n\r\n\r\nline two\r\n"

    doc = parse_document(raw, source_path="x.md")

    assert doc.text == "line one\n\nline two"


def test_content_hash_covers_the_whole_file_including_metadata():
    a = parse_document(RAW, source_path="x.md")
    b = parse_document(RAW, source_path="x.md")
    renamed = parse_document(
        RAW.replace("Demo runbook\nservice", "Other\nservice"), source_path="x.md"
    )

    assert a.content_sha256 == b.content_sha256
    assert len(a.content_sha256) == 64
    assert renamed.content_sha256 != a.content_sha256


def test_form_feed_splits_pages_and_is_not_kept_in_the_text():
    raw = "---\nid: a\ntype: runbook\ntitle: T\n---\nfirst page\fsecond page\fthird page\n"

    doc = parse_document(raw, source_path="x.txt")

    assert "\f" not in doc.text
    assert len(doc.page_starts) == 3
    assert doc.page_at(doc.text.index("first")) == 1
    assert doc.page_at(doc.text.index("second")) == 2
    assert doc.page_at(doc.text.index("third")) == 3


def test_a_document_without_form_feeds_is_one_page():
    doc = parse_document(RAW, source_path="x.md")

    assert doc.page_starts == (0,)
    assert doc.page_at(len(doc.text) - 1) == 1


def test_section_is_the_heading_path_at_an_offset():
    doc = parse_document(RAW, source_path="x.md")

    assert doc.section_at(doc.text.index("Intro text")) == "Demo runbook"
    assert doc.section_at(doc.text.index("Do the thing")) == "Demo runbook > Fix"


def test_text_before_the_first_heading_has_no_section():
    raw = "---\nid: a\ntype: runbook\ntitle: T\n---\nplain start\n\n# Later\n\nbody\n"

    doc = parse_document(raw, source_path="x.md")

    assert doc.section_at(doc.text.index("plain start")) is None
    assert doc.section_at(doc.text.index("body")) == "Later"


def test_a_sibling_heading_replaces_the_previous_one():
    raw = "---\nid: a\ntype: runbook\ntitle: T\n---\n# A\n\n## One\n\nx1\n\n## Two\n\nx2\n"

    doc = parse_document(raw, source_path="x.md")

    assert doc.section_at(doc.text.index("x2")) == "A > Two"


def test_hash_lines_inside_code_fences_are_not_headings():
    raw = (
        "---\nid: a\ntype: runbook\ntitle: T\n---\n"
        "# Real\n\n```\n# just a shell comment\nls\n```\n\nafter\n"
    )

    doc = parse_document(raw, source_path="x.md")

    assert doc.section_at(doc.text.index("after")) == "Real"


def test_load_corpus_reads_markdown_and_text_in_a_stable_order(tmp_path: Path):
    (tmp_path / "runbooks").mkdir()
    (tmp_path / "runbooks" / "b.md").write_text(RAW.replace("rb-demo", "rb-b"))
    (tmp_path / "runbooks" / "a.txt").write_text(
        "---\nid: rb-a\ntype: runbook\ntitle: A\n---\ntext\n"
    )
    (tmp_path / "runbooks" / ".DS_Store").write_bytes(b"\x00junk")
    (tmp_path / "notes.pdf").write_bytes(b"%PDF")

    docs = load_corpus(tmp_path)

    assert [d.id for d in docs] == ["rb-a", "rb-b"]
    assert [d.source_path for d in docs] == ["runbooks/a.txt", "runbooks/b.md"]


def test_load_corpus_rejects_duplicate_ids(tmp_path: Path):
    (tmp_path / "one.md").write_text(RAW)
    (tmp_path / "two.md").write_text(RAW)

    with pytest.raises(CorpusError, match="rb-demo"):
        load_corpus(tmp_path)


def test_load_corpus_of_a_missing_directory_fails_loudly(tmp_path: Path):
    with pytest.raises(CorpusError, match="not a directory"):
        load_corpus(tmp_path / "nope")
