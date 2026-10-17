"""Graph-assisted retrieval (AIENG-37): look where the incident's service points, without a filter.

The incident carries a service (the anchor). A scope turns the anchor into a narrower search: the
anchor's own documents (metadata only), or the documents the graph links to it and to its
neighbours. Retrieval inside that scope is fused by reciprocal rank with ordinary, open retrieval, so
a document outside the scope can still win. That is the difference from a filter, which would hide
the right document whenever it belongs to another service.
"""

import asyncio
import re
from collections.abc import Iterable, Sequence
from dataclasses import replace
from typing import Protocol

from incidentops_ai.graph.store import GraphStore, check_hops
from incidentops_ai.retrieval.hybrid import CANDIDATE_DEPTH, reciprocal_rank_fusion
from incidentops_ai.retrieval.retriever import (
    QueryHints,
    RetrievalFilter,
    RetrievedChunk,
    Retriever,
)

_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def link_services(text: str, known: Iterable[str]) -> tuple[str, ...]:
    """Services named in the text, by whole name, in the order they first appear."""
    names = set(known)
    found: list[str] = []
    for token in _NAME.findall(text.lower()):
        if token in names and token not in found:
            found.append(token)
    return tuple(found)


class Scope(Protocol):
    name: str

    async def known_services(self, *, tenant: str) -> set[str]:
        """Services the scope can link from question text; empty if it relies on hints only."""
        ...

    async def narrow(
        self, filters: RetrievalFilter, anchors: Sequence[str]
    ) -> RetrievalFilter | None:
        """The filter for the scoped search, or None when the scope adds nothing."""
        ...


class OwnDocuments:
    """The anchor's own documents, by their front-matter service. Metadata only, no graph."""

    name = "anchor"

    async def known_services(self, *, tenant: str) -> set[str]:
        return set()

    async def narrow(
        self, filters: RetrievalFilter, anchors: Sequence[str]
    ) -> RetrievalFilter | None:
        services = (
            tuple(anchors)
            if filters.services is None
            else tuple(s for s in anchors if s in filters.services)
        )
        return replace(filters, services=services) if services else None


class GraphNeighbourhood:
    """Documents the graph links to the anchors and to services within `hops` of them."""

    def __init__(self, store: GraphStore, *, hops: int = 1) -> None:
        self.name = f"graph{check_hops(hops)}"
        self._store = store
        self._hops = hops

    async def known_services(self, *, tenant: str) -> set[str]:
        return await self._store.known_services(tenant=tenant)

    async def narrow(
        self, filters: RetrievalFilter, anchors: Sequence[str]
    ) -> RetrievalFilter | None:
        related = await self._store.related_documents(
            anchors, tenant=filters.tenant_id, hops=self._hops
        )
        doc_ids = tuple(d.doc_id for d in related)
        if filters.doc_ids is not None:
            doc_ids = tuple(d for d in doc_ids if d in filters.doc_ids)
        return replace(filters, doc_ids=doc_ids) if doc_ids else None


class AnchoredRetriever:
    def __init__(self, base: Retriever, scope: Scope, *, depth: int = CANDIDATE_DEPTH) -> None:
        self.name = f"{base.name}+{scope.name}"
        self._base = base
        self._scope = scope
        self._depth = depth

    async def _anchors(self, query: str, tenant: str, hints: QueryHints | None) -> tuple[str, ...]:
        if hints is not None and hints.services:
            return tuple(hints.services)
        return link_services(query, await self._scope.known_services(tenant=tenant))

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
        fetch = max(self._depth, k)
        anchors = await self._anchors(query, filters.tenant_id, hints)
        scoped = await self._scope.narrow(filters, anchors) if anchors else None
        if scoped is None:
            return (await self._base.retrieve(query, k=fetch, filters=filters, hints=hints))[:k]
        open_list, scoped_list = await asyncio.gather(
            self._base.retrieve(query, k=fetch, filters=filters, hints=hints),
            self._base.retrieve(query, k=fetch, filters=scoped, hints=hints),
        )
        return reciprocal_rank_fusion([open_list, scoped_list])[:k]
