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


def build_chunk(document: Document, chunker: str, ordinal: int, start: int, end: int) -> Chunk:
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
        section=document.section_at(start),
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
            end = self._window_end(text, start, length)
            trimmed_end = end
            while trimmed_end > start and text[trimmed_end - 1].isspace():
                trimmed_end -= 1
            chunks.append(build_chunk(document, self.name, len(chunks), start, trimmed_end))
            if end >= length:
                break
            start = self._next_start(text, start, end)
        return chunks

    def _window_end(self, text: str, start: int, length: int) -> int:
        end = start + self.size
        if end >= length:
            return length
        for i in range(end, start + self.size // 2, -1):
            if text[i].isspace():
                return i
        return end  # no whitespace near the limit: cut inside the word

    def _next_start(self, text: str, start: int, end: int) -> int:
        candidate = max(end - self.overlap, start + 1)
        if candidate > 0 and not text[candidate - 1].isspace() and not text[candidate].isspace():
            # Landed inside a word: move on to the next word if there is one before `end`.
            for i in range(candidate, end):
                if text[i].isspace():
                    return _skip_whitespace(text, i)
        return _skip_whitespace(text, candidate)


def _skip_whitespace(text: str, index: int) -> int:
    while index < len(text) and text[index].isspace():
        index += 1
    return index


def chunker_from_name(name: str) -> Chunker:
    """Inverse of `Chunker.name`, so a stored or command-line name rebuilds the same chunker."""
    if match := re.fullmatch(r"fixed-(\d+)-(\d+)", name):
        return FixedChunker(size=int(match.group(1)), overlap=int(match.group(2)))
    raise ValueError(f"unknown chunker {name!r}")
