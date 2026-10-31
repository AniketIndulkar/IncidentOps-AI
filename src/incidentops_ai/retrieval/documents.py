"""Corpus documents: front matter, cleaned text, pages and headings.

A document file is `---` front matter followed by a body. Page breaks are form feeds (what PDF
text extractors emit); they are removed from the text and remembered as offsets, so every
character offset in `Document.text` can be mapped back to a page and a heading path.

Example file:

    ---
    id: rb-orders-db-pool
    type: runbook
    title: Orders pool exhausted
    service: orders-api
    ---
    # Orders pool exhausted
    ## Symptoms
    ...

Cleaning only touches whitespace (trailing spaces, runs of blank lines). Lines are never
re-wrapped, so code blocks and tables survive and offsets stay easy to reason about.
"""

import hashlib
import re
from bisect import bisect_right
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

# Tenant used when none is given. Multi-tenant isolation arrives later; the field exists now so
# every query is already scoped.
DEFAULT_TENANT = "default"
# Bump when cleaning or heading/page logic changes: ingestion re-processes documents parsed by an older version.
PARSER_VERSION = 1
SUPPORTED_SUFFIXES = frozenset({".md", ".txt"})

# `---` line, the metadata block (captured), and a closing `---` line, at the very start of a file.
_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---[ \t]*(?:\n|\Z)", re.DOTALL)
# The only front matter keys allowed; anything else is treated as a typo and rejected.
_FIELDS = frozenset({"id", "type", "title", "service"})
# Document ids: start with a letter or digit, then letters, digits, ".", "_" or "-", 100 max.
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
# A markdown ATX heading: 1-6 `#`, the title (captured), optional closing `#`s.
_HEADING = re.compile(r"(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*")
# Three or more newlines in a row, collapsed to one blank line by cleaning.
_BLANK_RUN = re.compile(r"\n{3,}")


class CorpusError(ValueError):
    """A corpus file is malformed. The message names the file."""


class DocType(StrEnum):
    """Kind of document. A `StrEnum` member compares equal to its string value."""

    RUNBOOK = "runbook"  # how to diagnose and fix a known problem
    POSTMORTEM = "postmortem"  # what happened in a past incident and why


@dataclass(frozen=True, slots=True)
class Document:
    """A parsed corpus file. Immutable: parsing produces it, nothing changes it afterwards.

    Pages and headings are stored as parallel tuples of offsets so that "which page / section is
    character N in?" is a binary search (`page_at`, `section_at`).
    """

    id: str  # from front matter; unique within the corpus
    type: DocType
    title: str
    service: str | None  # service the document is about, None if it is not service-specific
    tenant_id: str
    source_path: str  # path relative to the corpus root, with forward slashes
    text: str  # cleaned body; all chunk offsets index into this string
    content_sha256: str  # of the whole file (metadata included), so any change is noticed
    page_starts: tuple[int, ...]  # offset where each non-empty page begins
    page_numbers: tuple[int, ...]  # source page number of each entry above (1-based)
    heading_offsets: tuple[int, ...]  # offset of each heading line, in order
    heading_paths: tuple[str, ...]  # "H1 > H2 > H3" in force from the matching offset on

    def page_at(self, offset: int) -> int:
        """Source page number (1-based) that contains character `offset` of `text`.

        `bisect_right` finds the last page that starts at or before `offset`.
        """
        index = max(bisect_right(self.page_starts, offset) - 1, 0)
        return self.page_numbers[index]

    def section_at(self, offset: int) -> str | None:
        """Heading path in force at character `offset`, or None before the first heading."""
        index = bisect_right(self.heading_offsets, offset) - 1
        return self.heading_paths[index] if index >= 0 else None


def parse_document(raw: str, *, source_path: str, tenant_id: str = DEFAULT_TENANT) -> Document:
    """Parse the raw contents of one corpus file into a `Document`.

    Args:
        raw: the whole file as text.
        source_path: where it came from, used in error messages and citations.
        tenant_id: owner of the document.

    Raises `CorpusError` for missing or invalid front matter or an empty body.
    """
    raw = raw.replace("\r\n", "\n")  # Windows line endings would shift every offset
    match = _FRONT_MATTER.match(raw)
    if match is None:
        raise CorpusError(f"{source_path}: missing or unterminated front matter")
    meta = _parse_front_matter(match.group(1), source_path)

    text, page_starts, page_numbers = _clean_pages(raw[match.end() :])
    if not text:
        raise CorpusError(f"{source_path}: document body is empty")
    heading_offsets, heading_paths = _headings(text)

    return Document(
        id=meta["id"],
        type=_doc_type(meta["type"], source_path),
        title=meta["title"],
        service=meta.get("service") or None,  # an empty `service:` line means "none"
        tenant_id=tenant_id,
        source_path=source_path,
        text=text,
        content_sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        page_starts=page_starts,
        page_numbers=page_numbers,
        heading_offsets=heading_offsets,
        heading_paths=heading_paths,
    )


def load_corpus(root: Path, *, tenant_id: str = DEFAULT_TENANT) -> list[Document]:
    """Every .md/.txt file under `root`, in path order, so ingestion is repeatable.

    Hidden files are skipped. Raises `CorpusError` on a bad file or a duplicate document id.
    """
    if not root.is_dir():
        raise CorpusError(f"{root}: not a directory")
    documents: list[Document] = []
    seen: dict[str, str] = {}  # document id -> path that used it first, for the duplicate error
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name.startswith(".") or path.suffix not in SUPPORTED_SUFFIXES:
            continue
        source_path = path.relative_to(root).as_posix()
        try:
            raw = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise CorpusError(f"{source_path}: not valid UTF-8") from exc
        document = parse_document(raw, source_path=source_path, tenant_id=tenant_id)
        if document.id in seen:
            raise CorpusError(
                f"duplicate document id {document.id!r} in {seen[document.id]} and {source_path}"
            )
        seen[document.id] = source_path
        documents.append(document)
    return documents


def _parse_front_matter(block: str, source_path: str) -> dict[str, str]:
    """Parse `key: value` lines. Deliberately tiny instead of a YAML parser: four flat string
    fields are all a document needs, and unknown keys fail loudly instead of being ignored."""
    meta: dict[str, str] = {}
    for line in block.splitlines():
        if not line.strip():
            continue
        key, separator, value = line.partition(":")
        key = key.strip()
        if not separator or key not in _FIELDS:
            raise CorpusError(f"{source_path}: unexpected front matter line {line!r}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1]  # allow `title: "A: B"` for values that contain a colon
        meta[key] = value
    for required in ("id", "type", "title"):
        if not meta.get(required):
            raise CorpusError(f"{source_path}: front matter needs a non-empty {required!r}")
    if not _ID.fullmatch(meta["id"]):
        raise CorpusError(f"{source_path}: invalid document id {meta['id']!r}")
    return meta


def _doc_type(value: str, source_path: str) -> DocType:
    """Convert the front matter `type` to a `DocType`, with an error that lists valid values."""
    try:
        return DocType(value)
    except ValueError:
        allowed = ", ".join(t.value for t in DocType)
        raise CorpusError(f"{source_path}: unknown type {value!r} (allowed: {allowed})") from None


def _clean_pages(body: str) -> tuple[str, tuple[int, ...], tuple[int, ...]]:
    """Clean each page, join them with a blank line, and remember where each page starts.

    Returns `(text, page_starts, page_numbers)`; see the matching `Document` fields.
    """
    parts: list[str] = []
    starts: list[int] = []
    numbers: list[int] = []
    length = 0  # length of the joined text so far, i.e. where the next page will start
    for number, page in enumerate(body.split("\f"), start=1):  # "\f" is the form feed character
        cleaned = _BLANK_RUN.sub("\n\n", "\n".join(line.rstrip() for line in page.split("\n")))
        cleaned = cleaned.strip()
        if not cleaned:
            continue  # blank pages keep their number out of the text but still count
        if parts:
            length += 2  # the "\n\n" separator added by the join below
        starts.append(length)
        numbers.append(number)
        parts.append(cleaned)
        length += len(cleaned)
    return "\n\n".join(parts), tuple(starts), tuple(numbers)


def _headings(text: str) -> tuple[tuple[int, ...], tuple[str, ...]]:
    """Find markdown headings and the full heading path in force at each one.

    A stack holds the open headings: a new heading pops every heading of the same or deeper level,
    so "## Fix" after "## Symptoms" replaces it, and "### Step 1" nests under "## Fix".
    Lines inside ``` code fences are ignored, because "# comment" in a shell block is not a heading.
    """
    offsets: list[int] = []
    paths: list[str] = []
    stack: list[tuple[int, str]] = []  # (level, title) of each open heading, outermost first
    in_fence = False
    offset = 0  # offset of the current line in `text`
    for line in text.split("\n"):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        elif not in_fence and (heading := _HEADING.fullmatch(line)):
            level = len(heading.group(1))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, heading.group(2)))
            offsets.append(offset)
            paths.append(" > ".join(title for _, title in stack))
        offset += len(line) + 1  # +1 for the "\n" removed by split
    return tuple(offsets), tuple(paths)
