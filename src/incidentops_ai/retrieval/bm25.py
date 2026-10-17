"""BM25 sparse retrieval over chunks, held in memory (AIENG-29).

Dense search matches meaning; BM25 matches the words themselves, which is what error codes,
commands and variable names need. Parameters are the textbook values and are not tuned on the eval
set (docs/roadmap-w04.md): k1 = 1.2, b = 0.75.

Statistics (document frequency, average length) are kept per tenant, so one tenant's text can never
change what a word is worth for another. Filters restrict the candidates *after* scoring a corpus,
not the statistics: a service filter must not change a word's weight.
"""

import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from incidentops_ai.retrieval.chunking import Chunk
from incidentops_ai.retrieval.retriever import QueryHints, RetrievalFilter, RetrievedChunk

K1 = 1.2
B = 0.75

STOP_WORDS = frozenset(
    {
        *("a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from", "has", "have"),
        *("how", "i", "if", "in", "into", "is", "it", "its", "of", "on", "or", "s", "t", "that"),
        *("the", "their", "then", "there", "these", "this", "to", "was", "were", "what", "when"),
        *("where", "which", "who", "why", "will", "with", "you", "your"),
    }
)

# A run of letters and digits, optionally joined by - _ . ("PAY-1042", "pool_size", "kafka.errors").
# A trailing full stop is punctuation, not part of the word.
_RUN = re.compile(r"[a-z0-9]+(?:[-_.][a-z0-9]+)*")
_PART = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    """Lowercase words plus, for identifiers, the whole identifier next to its parts.

    "PAY-1042" gives `pay-1042`, `pay` and `1042`, so a query for the code matches the exact form
    and a query for "1042" still matches. No stemming; stop words are dropped.
    """
    tokens: list[str] = []
    for run in _RUN.findall(text.lower()):
        parts = _PART.findall(run)
        if len(parts) > 1:
            tokens.append(run)
        tokens.extend(part for part in parts if part not in STOP_WORDS)
    return tokens


@dataclass(frozen=True, slots=True)
class _TenantStats:
    documents: int
    average_length: float
    document_frequency: dict[str, int]


class Bm25Retriever:
    name = "bm25"

    def __init__(
        self,
        chunks: Sequence[Chunk],
        *,
        with_context: bool = False,
        k1: float = K1,
        b: float = B,
    ) -> None:
        self._chunks = list(chunks)
        self._k1 = k1
        self._b = b
        self._counts = [
            Counter(tokenize(chunk.embedding_text(with_context=with_context)))
            for chunk in self._chunks
        ]
        self._lengths = [sum(counts.values()) for counts in self._counts]
        self._stats = self._tenant_stats()

    def _tenant_stats(self) -> dict[str, _TenantStats]:
        lengths: dict[str, list[int]] = {}
        frequency: dict[str, Counter[str]] = {}
        for chunk, counts, length in zip(self._chunks, self._counts, self._lengths, strict=True):
            lengths.setdefault(chunk.tenant_id, []).append(length)
            frequency.setdefault(chunk.tenant_id, Counter()).update(counts.keys())
        return {
            tenant: _TenantStats(
                documents=len(sizes),
                average_length=(sum(sizes) / len(sizes)) or 1.0,
                document_frequency=dict(frequency[tenant]),
            )
            for tenant, sizes in lengths.items()
        }

    async def retrieve(
        self,
        query: str,
        *,
        k: int,
        filters: RetrievalFilter,
        hints: QueryHints | None = None,
    ) -> list[RetrievedChunk]:
        if k <= 0:
            raise ValueError("k must be positive")
        stats = self._stats.get(filters.tenant_id)
        terms = list(dict.fromkeys(tokenize(query)))  # a repeated query word counts once
        if stats is None or not terms:
            return []

        idf = {
            term: math.log(
                1
                + (stats.documents - stats.document_frequency.get(term, 0) + 0.5)
                / (stats.document_frequency.get(term, 0) + 0.5)
            )
            for term in terms
        }
        scored: list[tuple[float, int]] = []
        for index, chunk in enumerate(self._chunks):
            if not filters.allows(chunk):
                continue
            counts = self._counts[index]
            norm = 1 - self._b + self._b * self._lengths[index] / stats.average_length
            score = sum(
                idf[term] * counts[term] * (self._k1 + 1) / (counts[term] + self._k1 * norm)
                for term in terms
                if counts[term]
            )
            if score > 0:
                scored.append((score, index))

        scored.sort(key=lambda pair: (-pair[0], pair[1]))  # ties keep corpus order
        return [
            RetrievedChunk(chunk=self._chunks[index], score=score, rank=rank)
            for rank, (score, index) in enumerate(scored[:k], start=1)
        ]
