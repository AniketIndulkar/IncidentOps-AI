"""Corpus documents: front matter, cleaned text, pages and headings.

A document file is `---` front matter followed by a body. Page breaks are form feeds (what PDF
text extractors emit); they are removed from the text and remembered as offsets, so every
character offset in `Document.text` can be mapped back to a page and a heading path.
"""

import hashlib
import re
from bisect import bisect_right
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

DEFAULT_TENANT = "default"
# Bump when cleaning or heading/page logic changes: ingestion re-processes documents parsed by an older version.
PARSER_VERSION = 1
SUPPORTED_SUFFIXES = frozenset({".md", ".txt"})

_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---[ \t]*(?:\n|\Z)", re.DOTALL)
_FIELDS = frozenset({"id", "type", "title", "service"})
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
_HEADING = re.compile(r"(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*")
_BLANK_RUN = re.compile(r"\n{3,}")


class CorpusError(ValueError):
    """A corpus file is malformed. The message names the file."""


class DocType(StrEnum):
    RUNBOOK = "runbook"
    POSTMORTEM = "postmortem"
    ATTACHMENT = "attachment"  # a file attached to one incident (W06); never in the shared corpus


@dataclass(frozen=True, slots=True)
class Document:
    id: str
    type: DocType
    title: str
    service: str | None
    tenant_id: str
    source_path: str
    text: str  # cleaned body; all chunk offsets index into this string
    content_sha256: str  # of the whole file (metadata included), so any change is noticed
    page_starts: tuple[int, ...]  # offset where each non-empty page begins
    page_numbers: tuple[int, ...]  # source page number of each entry above (1-based)
    heading_offsets: tuple[int, ...]
    heading_paths: tuple[str, ...]  # "H1 > H2 > H3" in force from the matching offset on

    def page_at(self, offset: int) -> int:
        index = max(bisect_right(self.page_starts, offset) - 1, 0)
        return self.page_numbers[index]

    def section_at(self, offset: int) -> str | None:
        index = bisect_right(self.heading_offsets, offset) - 1
        return self.heading_paths[index] if index >= 0 else None


def parse_document(raw: str, *, source_path: str, tenant_id: str = DEFAULT_TENANT) -> Document:
    raw = raw.replace("\r\n", "\n")
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
        service=meta.get("service") or None,
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
    """Every .md/.txt file under `root`, in path order, so ingestion is repeatable."""
    if not root.is_dir():
        raise CorpusError(f"{root}: not a directory")
    documents: list[Document] = []
    seen: dict[str, str] = {}
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
            value = value[1:-1]
        meta[key] = value
    for required in ("id", "type", "title"):
        if not meta.get(required):
            raise CorpusError(f"{source_path}: front matter needs a non-empty {required!r}")
    if not _ID.fullmatch(meta["id"]):
        raise CorpusError(f"{source_path}: invalid document id {meta['id']!r}")
    return meta


def _doc_type(value: str, source_path: str) -> DocType:
    try:
        return DocType(value)
    except ValueError:
        allowed = ", ".join(t.value for t in DocType)
        raise CorpusError(f"{source_path}: unknown type {value!r} (allowed: {allowed})") from None


def _clean_pages(body: str) -> tuple[str, tuple[int, ...], tuple[int, ...]]:
    """Clean each page, join them with a blank line, and remember where each page starts."""
    parts: list[str] = []
    starts: list[int] = []
    numbers: list[int] = []
    length = 0
    for number, page in enumerate(body.split("\f"), start=1):
        cleaned = _BLANK_RUN.sub("\n\n", "\n".join(line.rstrip() for line in page.split("\n")))
        cleaned = cleaned.strip()
        if not cleaned:
            continue  # blank pages keep their number out of the text but still count
        if parts:
            length += 2
        starts.append(length)
        numbers.append(number)
        parts.append(cleaned)
        length += len(cleaned)
    return "\n\n".join(parts), tuple(starts), tuple(numbers)


def _headings(text: str) -> tuple[tuple[int, ...], tuple[str, ...]]:
    offsets: list[int] = []
    paths: list[str] = []
    stack: list[tuple[int, str]] = []
    in_fence = False
    offset = 0
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
        offset += len(line) + 1
    return tuple(offsets), tuple(paths)
