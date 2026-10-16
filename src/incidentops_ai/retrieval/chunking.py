"""Splitting documents into chunks, each with the provenance needed to cite it.

A chunk is an exact slice of `Document.text`. Offsets, page and section are computed from the
document, never guessed, so a citation can always be traced back to the source.
"""

import re
import uuid
from dataclasses import dataclass
from typing import Protocol

from incidentops_ai.retrieval.documents import DocType, Document


@dataclass(frozen=True, slots=True)
class Chunk:
    tenant_id: str
    doc_id: str
    doc_type: DocType
    service: str | None
    title: str
    source_path: str
    chunker: str  # name of the configuration that produced it, e.g. "fixed-1000-100"
    ordinal: int
    start: int  # character offsets into Document.text: text == document.text[start:end]
    end: int
    page: int  # page where the chunk starts
    page_end: int
    section: str | None  # heading path where the chunk starts
    text: str

    @property
    def id(self) -> uuid.UUID:
        """Stable across runs: the same tenant, document, chunker and position give the same id."""
        key = f"incidentops:chunk:{self.tenant_id}:{self.doc_id}:{self.chunker}:{self.ordinal}"
        return uuid.uuid5(uuid.NAMESPACE_URL, key)

    def context(self) -> str:
        """Document title and section path, e.g. "Orders pool exhausted > Fix"."""
        parts = self.section.split(" > ") if self.section else []
        if parts and parts[0].strip().lower() == self.title.strip().lower():
            parts = parts[1:]  # the top heading usually repeats the title
        return " > ".join([self.title, *parts])

    def embedding_text(self, *, with_context: bool) -> str:
        return f"{self.context()}\n\n{self.text}" if with_context else self.text


class Chunker(Protocol):
    name: str

    def chunk(self, document: Document) -> list[Chunk]: ...


def build_chunk(
    document: Document,
    chunker: str,
    ordinal: int,
    start: int,
    end: int,
    *,
    section_offset: int | None = None,
) -> Chunk:
    """`section_offset` lets a chunk be labelled by a later point than its first character."""
    return Chunk(
        tenant_id=document.tenant_id,
        doc_id=document.id,
        doc_type=document.type,
        service=document.service,
        title=document.title,
        source_path=document.source_path,
        chunker=chunker,
        ordinal=ordinal,
        start=start,
        end=end,
        page=document.page_at(start),
        page_end=document.page_at(end - 1),
        section=document.section_at(start if section_offset is None else section_offset),
        text=document.text[start:end],
    )


class FixedChunker:
    """Windows of at most `size` characters that repeat `overlap` characters at each seam.

    Cuts fall on whitespace when there is any near the limit, so words are not split.
    """

    def __init__(self, *, size: int, overlap: int) -> None:
        if size <= 0:
            raise ValueError("size must be positive")
        if not 0 <= overlap < size:
            raise ValueError("overlap must be at least 0 and smaller than size")
        self.size = size
        self.overlap = overlap
        self.name = f"fixed-{size}-{overlap}"

    def chunk(self, document: Document) -> list[Chunk]:
        text = document.text
        length = len(text)
        chunks: list[Chunk] = []
        start = _skip_whitespace(text, 0)
        while start < length:
            end = _window_end(text, start, length, self.size)
            trimmed_end = end
            while trimmed_end > start and text[trimmed_end - 1].isspace():
                trimmed_end -= 1
            chunks.append(build_chunk(document, self.name, len(chunks), start, trimmed_end))
            if end >= length:
                break
            start = self._next_start(text, start, end)
        return chunks

    def _next_start(self, text: str, start: int, end: int) -> int:
        candidate = max(end - self.overlap, start + 1)
        if candidate > 0 and not text[candidate - 1].isspace() and not text[candidate].isspace():
            # Landed inside a word: move on to the next word if there is one before `end`.
            for i in range(candidate, end):
                if text[i].isspace():
                    return _skip_whitespace(text, i)
        return _skip_whitespace(text, candidate)


def _window_end(text: str, start: int, limit: int, size: int) -> int:
    """End of a window of at most `size` characters from `start`, on whitespace if possible."""
    end = start + size
    if end >= limit:
        return limit
    for i in range(end, start + size // 2, -1):
        if text[i].isspace():
            return i
    return end  # no whitespace near the limit: cut inside the word


def _trim(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _paragraph_spans(text: str, lo: int, hi: int) -> list[tuple[int, int]]:
    """Paragraphs (separated by blank lines) of text[lo:hi], without surrounding whitespace."""
    spans: list[tuple[int, int]] = []
    position = lo
    for separator in [*re.finditer(r"\n[ \t]*\n\s*", text[lo:hi]), None]:
        stop = hi if separator is None else lo + separator.start()
        start, end = _trim(text, position, stop)
        if start < end:
            spans.append((start, end))
        if separator is not None:
            position = lo + separator.end()
    return spans


def _split_long(text: str, start: int, end: int, size: int) -> list[tuple[int, int]]:
    pieces: list[tuple[int, int]] = []
    position = start
    while position < end:
        stop = _window_end(text, position, end, size)
        piece_start, piece_end = _trim(text, position, stop)
        if piece_start < piece_end:
            pieces.append((piece_start, piece_end))
        position = _skip_whitespace(text, stop)
    return pieces


def _pack(text: str, spans: list[tuple[int, int]], max_size: int) -> list[tuple[int, int]]:
    """Merge neighbouring paragraphs while they fit in `max_size`; split any that never fit."""
    packed: list[tuple[int, int]] = []
    current: tuple[int, int] | None = None
    for start, end in spans:
        if end - start > max_size:
            if current is not None:
                packed.append(current)
                current = None
            packed.extend(_split_long(text, start, end, max_size))
        elif current is None:
            current = (start, end)
        elif end - current[0] <= max_size:
            current = (current[0], end)
        else:
            packed.append(current)
            current = (start, end)
    if current is not None:
        packed.append(current)
    return packed


def _skip_whitespace(text: str, index: int) -> int:
    while index < len(text) and text[index].isspace():
        index += 1
    return index


class ParagraphChunker:
    """Whole paragraphs packed together up to `max_size` characters. A paragraph that is longer
    than that on its own is split on whitespace."""

    def __init__(self, *, max_size: int) -> None:
        if max_size <= 0:
            raise ValueError("max_size must be positive")
        self.max_size = max_size
        self.name = f"paragraph-{max_size}"

    def chunk(self, document: Document) -> list[Chunk]:
        text = document.text
        spans = _pack(text, _paragraph_spans(text, 0, len(text)), self.max_size)
        return [build_chunk(document, self.name, i, s, e) for i, (s, e) in enumerate(spans)]


# A section shorter than this (a title line, a heading followed at once by a sub-heading) has
# nothing to retrieve on its own and is joined to the section after it.
MIN_SECTION_CHARS = 150


class SectionChunker:
    """One chunk per heading section, so a runbook step stays with its heading.

    Tiny sections join the next one; a section longer than `max_size` is packed by paragraph.
    A chunk is labelled by the section of its main part, not by a title line joined to it.
    """

    def __init__(self, *, max_size: int) -> None:
        if max_size <= 0:
            raise ValueError("max_size must be positive")
        self.max_size = max_size
        self.name = f"section-{max_size}"

    def chunk(self, document: Document) -> list[Chunk]:
        text = document.text
        starts = sorted({0, *document.heading_offsets})
        ends = [*starts[1:], len(text)]
        regions: list[tuple[int, int, int]] = []  # start, end, offset that names the section
        carry: int | None = None
        for index, (start, stop) in enumerate(zip(starts, ends, strict=True)):
            start, end = _trim(text, start, stop)
            if start >= end:
                continue
            label = start
            if carry is not None:
                start, carry = carry, None
            if end - start < MIN_SECTION_CHARS and index < len(starts) - 1:
                carry = start
                continue
            regions.append((start, end, label))
        if carry is not None:  # the document ends in a tiny section
            regions.append((carry, len(text.rstrip()), carry))

        chunks: list[Chunk] = []
        for start, end, label in regions:
            if end - start <= self.max_size:
                pieces = [(start, end)]
            else:
                pieces = _pack(text, _paragraph_spans(text, start, end), self.max_size)
            for number, (piece_start, piece_end) in enumerate(pieces):
                chunks.append(
                    build_chunk(
                        document,
                        self.name,
                        len(chunks),
                        piece_start,
                        piece_end,
                        section_offset=label if number == 0 else None,
                    )
                )
        return chunks


def chunker_from_name(name: str) -> Chunker:
    """Inverse of `Chunker.name`, so a stored or command-line name rebuilds the same chunker."""
    if match := re.fullmatch(r"fixed-(\d+)-(\d+)", name):
        return FixedChunker(size=int(match.group(1)), overlap=int(match.group(2)))
    if match := re.fullmatch(r"paragraph-(\d+)", name):
        return ParagraphChunker(max_size=int(match.group(1)))
    if match := re.fullmatch(r"section-(\d+)", name):
        return SectionChunker(max_size=int(match.group(1)))
    raise ValueError(f"unknown chunker {name!r}")
