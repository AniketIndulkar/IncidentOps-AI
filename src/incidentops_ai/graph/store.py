"""The questions the graph answers, as an interface (AIENG-36).

Two implementations answer them the same way: Neo4j (the real store) and an in-memory reference that
tests hold Neo4j to. Every method takes a tenant, like retrieval: one customer's graph is never
visible to another.

Hop limits are small and bounded. They are written into Cypher as literals (variable-length bounds
cannot be query parameters), so they are validated here first.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

MAX_HOPS = 4


class GraphUnavailableError(RuntimeError):
    """The graph database could not answer (down, unreachable, refused). Callers degrade: the
    graph is context, never the only way to an answer."""


def check_hops(hops: int, *, minimum: int = 0) -> int:
    if not minimum <= hops <= MAX_HOPS:
        raise ValueError(f"hops must be between {minimum} and {MAX_HOPS}, not {hops}")
    return hops


@dataclass(frozen=True, slots=True)
class ImpactedService:
    service: str
    hops: int  # how many dependency steps away from the failing service


@dataclass(frozen=True, slots=True)
class IncidentRef:
    id: str  # also the postmortem's document id
    date: str
    severity: str
    services: tuple[str, ...]  # the services in scope this incident affected or was caused by


@dataclass(frozen=True, slots=True)
class Ownership:
    service: str
    team_id: str
    team_name: str
    members: tuple[str, ...]
    on_call: str | None


@dataclass(frozen=True, slots=True)
class FixRef:
    id: str
    service: str
    incident: str
    kind: str
    status: str
    description: str
    hops: int


@dataclass(frozen=True, slots=True)
class RelatedDocument:
    doc_id: str
    kind: Literal["runbook", "postmortem"]
    distance: int  # dependency steps between an anchor and the service the document is linked to


@dataclass(frozen=True, slots=True)
class ServiceContext:
    """What an on-call engineer wants to know about a service before reading anything."""

    service: str
    team_name: str
    on_call: str | None
    depends_on: tuple[str, ...]
    dependents: tuple[str, ...]
    recent_incidents: tuple[IncidentRef, ...]


class GraphStore(Protocol):
    async def known_services(self, *, tenant: str) -> set[str]: ...

    async def downstream_impact(
        self, service: str, *, tenant: str, max_hops: int = 3
    ) -> list[ImpactedService]:
        """T1: services that depend on `service`, directly or not, nearest first."""
        ...

    async def incident_history(
        self, service: str, *, tenant: str, max_hops: int = 2
    ) -> list[IncidentRef]:
        """T2: incidents that touched `service` or what it depends on, newest first."""
        ...

    async def ownership(self, service: str, *, tenant: str) -> Ownership | None:
        """T3: the owning team, its people and who is on call."""
        ...

    async def open_fixes_upstream(
        self, service: str, *, tenant: str, max_hops: int = 2
    ) -> list[FixRef]:
        """T4: open fixes on the services `service` depends on (risk it inherits)."""
        ...

    async def runbooks_for(
        self, service: str, *, tenant: str, max_hops: int = 1
    ) -> list[tuple[str, int]]:
        """T5: runbooks covering `service` and its dependencies, as (runbook id, distance)."""
        ...

    async def related_documents(
        self, services: Sequence[str], *, tenant: str, hops: int = 1
    ) -> list[RelatedDocument]:
        """Documents linked to the services within `hops` of any anchor, in either direction:
        runbooks that cover them and postmortems of incidents that touched them."""
        ...

    async def service_context(self, service: str, *, tenant: str) -> ServiceContext | None: ...
