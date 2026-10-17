"""The graph held in memory: the reference implementation that Neo4j is tested against."""

from collections import defaultdict, deque
from collections.abc import Callable, Iterable, Sequence

from incidentops_ai.graph.catalogue import Catalogue, Incident
from incidentops_ai.graph.store import (
    FixRef,
    ImpactedService,
    IncidentRef,
    Ownership,
    RelatedDocument,
    ServiceContext,
    check_hops,
)

RECENT_INCIDENTS = 5


def _distances(
    starts: Iterable[str], neighbours: Callable[[str], Iterable[str]], max_hops: int
) -> dict[str, int]:
    """Breadth-first: every service reachable within `max_hops`, with its smallest distance."""
    seen = {start: 0 for start in starts}
    queue = deque(seen)
    while queue:
        current = queue.popleft()
        if seen[current] == max_hops:
            continue
        for nxt in neighbours(current):
            if nxt not in seen:
                seen[nxt] = seen[current] + 1
                queue.append(nxt)
    return seen


class _TenantGraph:
    def __init__(self, catalogue: Catalogue) -> None:
        self.catalogue = catalogue
        self.services = {s.name: s for s in catalogue.services}
        self.dependents: dict[str, list[str]] = defaultdict(list)
        for service in catalogue.services:
            for dependency in service.depends_on:
                self.dependents[dependency].append(service.name)

    def depends_on(self, name: str) -> tuple[str, ...]:
        service = self.services.get(name)
        return service.depends_on if service else ()

    def undirected(self, name: str) -> list[str]:
        return [*self.depends_on(name), *self.dependents.get(name, [])]

    def touched(self, incident: Incident) -> set[str]:
        return {*incident.affected, *incident.caused_by}


def _incident_ref(incident: Incident, services: Iterable[str]) -> IncidentRef:
    return IncidentRef(incident.id, incident.date, incident.severity, tuple(sorted(services)))


def _newest_first(refs: list[IncidentRef]) -> list[IncidentRef]:
    refs.sort(key=lambda r: r.id)
    refs.sort(key=lambda r: r.date, reverse=True)
    return refs


class InMemoryGraphStore:
    def __init__(self, catalogues: Sequence[Catalogue]) -> None:
        self._graphs = {c.tenant: _TenantGraph(c) for c in catalogues}

    def _graph(self, tenant: str) -> _TenantGraph | None:
        return self._graphs.get(tenant)

    async def known_services(self, *, tenant: str) -> set[str]:
        graph = self._graph(tenant)
        return set(graph.services) if graph else set()

    async def downstream_impact(
        self, service: str, *, tenant: str, max_hops: int = 3
    ) -> list[ImpactedService]:
        check_hops(max_hops, minimum=1)
        graph = self._graph(tenant)
        if graph is None or service not in graph.services:
            return []
        reach = _distances([service], lambda s: graph.dependents.get(s, []), max_hops)
        reach.pop(service)
        return sorted(
            (ImpactedService(name, hops) for name, hops in reach.items()),
            key=lambda i: (i.hops, i.service),
        )

    async def incident_history(
        self, service: str, *, tenant: str, max_hops: int = 2
    ) -> list[IncidentRef]:
        check_hops(max_hops)
        graph = self._graph(tenant)
        if graph is None or service not in graph.services:
            return []
        scope = set(_distances([service], graph.depends_on, max_hops))
        refs = [
            _incident_ref(incident, graph.touched(incident) & scope)
            for incident in graph.catalogue.incidents
            if graph.touched(incident) & scope
        ]
        return _newest_first(refs)

    async def ownership(self, service: str, *, tenant: str) -> Ownership | None:
        graph = self._graph(tenant)
        if graph is None or service not in graph.services:
            return None
        team_id = graph.services[service].team
        team = next(t for t in graph.catalogue.teams if t.id == team_id)
        members = [e for e in graph.catalogue.engineers if e.team == team_id]
        on_call = next((e.name for e in members if e.on_call), None)
        return Ownership(
            service, team.id, team.name, tuple(sorted(e.name for e in members)), on_call
        )

    async def open_fixes_upstream(
        self, service: str, *, tenant: str, max_hops: int = 2
    ) -> list[FixRef]:
        check_hops(max_hops, minimum=1)
        graph = self._graph(tenant)
        if graph is None or service not in graph.services:
            return []
        reach = _distances([service], graph.depends_on, max_hops)
        reach.pop(service)
        fixes = [
            FixRef(f.id, f.service, f.incident, f.kind, f.status, f.description, reach[f.service])
            for f in graph.catalogue.fixes
            if f.status == "open" and f.service in reach
        ]
        return sorted(fixes, key=lambda f: (f.hops, f.service, f.id))

    async def runbooks_for(
        self, service: str, *, tenant: str, max_hops: int = 1
    ) -> list[tuple[str, int]]:
        check_hops(max_hops)
        graph = self._graph(tenant)
        if graph is None or service not in graph.services:
            return []
        reach = _distances([service], graph.depends_on, max_hops)
        found = {
            runbook.id: min(reach[name] for name in runbook.covers if name in reach)
            for runbook in graph.catalogue.runbooks
            if any(name in reach for name in runbook.covers)
        }
        return sorted(found.items(), key=lambda item: (item[1], item[0]))

    async def related_documents(
        self, services: Sequence[str], *, tenant: str, hops: int = 1
    ) -> list[RelatedDocument]:
        check_hops(hops)
        graph = self._graph(tenant)
        if graph is None:
            return []
        anchors = [s for s in services if s in graph.services]
        reach = _distances(anchors, graph.undirected, hops)
        documents: dict[str, RelatedDocument] = {}

        def offer(doc_id: str, kind, distance: int) -> None:
            if doc_id not in documents or distance < documents[doc_id].distance:
                documents[doc_id] = RelatedDocument(doc_id, kind, distance)

        for runbook in graph.catalogue.runbooks:
            distances = [reach[name] for name in runbook.covers if name in reach]
            if distances:
                offer(runbook.id, "runbook", min(distances))
        for incident in graph.catalogue.incidents:
            distances = [reach[name] for name in graph.touched(incident) if name in reach]
            if distances:
                offer(incident.id, "postmortem", min(distances))
        return sorted(documents.values(), key=lambda d: (d.distance, d.kind, d.doc_id))

    async def service_context(self, service: str, *, tenant: str) -> ServiceContext | None:
        ownership = await self.ownership(service, tenant=tenant)
        graph = self._graph(tenant)
        if ownership is None or graph is None:
            return None
        recent = _newest_first(
            [
                _incident_ref(incident, {service})
                for incident in graph.catalogue.incidents
                if service in graph.touched(incident)
            ]
        )[:RECENT_INCIDENTS]
        return ServiceContext(
            service=service,
            team_name=ownership.team_name,
            on_call=ownership.on_call,
            depends_on=tuple(sorted(graph.depends_on(service))),
            dependents=tuple(sorted(graph.dependents.get(service, []))),
            recent_incidents=tuple(recent),
        )
