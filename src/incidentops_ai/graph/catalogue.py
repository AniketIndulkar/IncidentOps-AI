"""The service catalogue: teams, people, services, incidents, fixes and runbook coverage (AIENG-34).

Loaded from TOML and checked twice: for internal consistency when parsed (every reference resolves,
dependencies form no cycle, one on-call engineer per team), and against the document corpus (every
incident is a postmortem there, every runbook exists and covers its own service). A graph built from
a catalogue that passed both checks cannot point at something that does not exist.
"""

import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from incidentops_ai.retrieval.documents import DocType, Document

SERVICE_KINDS = ("internal", "infrastructure", "external")
FIX_KINDS = ("mitigation", "prevention")
FIX_STATUSES = ("done", "open")
SEVERITIES = ("SEV1", "SEV2", "SEV3", "SEV4")


class CatalogueError(ValueError):
    """The catalogue is malformed or disagrees with the corpus. The message says where."""


@dataclass(frozen=True, slots=True)
class Team:
    id: str
    name: str


@dataclass(frozen=True, slots=True)
class Engineer:
    id: str
    name: str
    team: str
    on_call: bool


@dataclass(frozen=True, slots=True)
class Service:
    name: str
    team: str
    kind: str
    tier: int
    depends_on: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Incident:
    id: str  # also the id of its postmortem document
    date: str
    severity: str
    duration_minutes: int
    affected: tuple[str, ...]
    caused_by: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Fix:
    id: str
    incident: str
    service: str
    kind: str
    status: str
    description: str


@dataclass(frozen=True, slots=True)
class Runbook:
    id: str  # the runbook's document id
    covers: tuple[str, ...]
    general: bool  # applies to every service (the triage runbook)


@dataclass(frozen=True)
class Catalogue:
    tenant: str
    teams: tuple[Team, ...]
    engineers: tuple[Engineer, ...]
    services: tuple[Service, ...]
    incidents: tuple[Incident, ...]
    fixes: tuple[Fix, ...]
    runbooks: tuple[Runbook, ...]

    def service(self, name: str) -> Service:
        for service in self.services:
            if service.name == name:
                return service
        raise KeyError(name)


def load_catalogue(path: Path) -> Catalogue:
    return parse_catalogue(path.read_text(encoding="utf-8"))


def _require(record: dict, field: str, kind: type, where: str):
    value = record.get(field)
    if not isinstance(value, kind) or (isinstance(value, str) and not value.strip()):
        raise CatalogueError(f"{where}: needs {field!r} ({kind.__name__})")
    return value


def _strings(record: dict, field: str, where: str) -> tuple[str, ...]:
    value = record.get(field, [])
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise CatalogueError(f"{where}: {field!r} must be a list of names")
    return tuple(value)


def _one_of(value: str, allowed: Sequence[str], where: str, field: str) -> str:
    if value not in allowed:
        raise CatalogueError(f"{where}: {field} must be one of {', '.join(allowed)}, not {value!r}")
    return value


def _unique(ids: Sequence[str], what: str) -> None:
    seen: set[str] = set()
    for item in ids:
        if item in seen:
            raise CatalogueError(f"duplicate {what} id {item!r}")
        seen.add(item)


def parse_catalogue(text: str) -> Catalogue:
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise CatalogueError(f"not valid TOML: {exc}") from exc

    tenant = _require(raw, "tenant", str, "catalogue")
    teams = tuple(
        Team(_require(t, "id", str, "team"), _require(t, "name", str, "team"))
        for t in raw.get("teams", [])
    )
    engineers = tuple(
        Engineer(
            id=_require(e, "id", str, "engineer"),
            name=_require(e, "name", str, "engineer"),
            team=_require(e, "team", str, "engineer"),
            on_call=_require(e, "on_call", bool, "engineer"),
        )
        for e in raw.get("engineers", [])
    )
    services = tuple(
        Service(
            name=(name := _require(s, "name", str, "service")),
            team=_require(s, "team", str, name),
            kind=_one_of(_require(s, "kind", str, name), SERVICE_KINDS, name, "kind"),
            tier=_require(s, "tier", int, name),
            depends_on=_strings(s, "depends_on", name),
        )
        for s in raw.get("services", [])
    )
    incidents = tuple(
        Incident(
            id=(iid := _require(i, "id", str, "incident")),
            date=_require(i, "date", str, iid),
            severity=_one_of(_require(i, "severity", str, iid), SEVERITIES, iid, "severity"),
            duration_minutes=_require(i, "duration_minutes", int, iid),
            affected=_strings(i, "affected", iid),
            caused_by=_strings(i, "caused_by", iid),
        )
        for i in raw.get("incidents", [])
    )
    fixes = tuple(
        Fix(
            id=(fid := _require(f, "id", str, "fix")),
            incident=_require(f, "incident", str, fid),
            service=_require(f, "service", str, fid),
            kind=_one_of(_require(f, "kind", str, fid), FIX_KINDS, fid, "kind"),
            status=_one_of(_require(f, "status", str, fid), FIX_STATUSES, fid, "status"),
            description=_require(f, "description", str, fid),
        )
        for f in raw.get("fixes", [])
    )
    runbooks = tuple(
        Runbook(
            id=(rid := _require(r, "id", str, "runbook")),
            covers=_strings(r, "covers", rid),
            general=bool(r.get("general", False)),
        )
        for r in raw.get("runbooks", [])
    )
    catalogue = Catalogue(tenant, teams, engineers, services, incidents, fixes, runbooks)
    _check_references(catalogue)
    return catalogue


def _check_references(catalogue: Catalogue) -> None:
    _unique([t.id for t in catalogue.teams], "team")
    _unique([e.id for e in catalogue.engineers], "engineer")
    _unique([s.name for s in catalogue.services], "service")
    _unique([i.id for i in catalogue.incidents], "incident")
    _unique([f.id for f in catalogue.fixes], "fix")
    _unique([r.id for r in catalogue.runbooks], "runbook")

    teams = {t.id for t in catalogue.teams}
    services = {s.name for s in catalogue.services}
    incidents = {i.id for i in catalogue.incidents}

    for engineer in catalogue.engineers:
        if engineer.team not in teams:
            raise CatalogueError(f"{engineer.id}: unknown team {engineer.team!r}")
    for team in catalogue.teams:
        on_call = [e for e in catalogue.engineers if e.team == team.id and e.on_call]
        if len(on_call) != 1:
            raise CatalogueError(f"team {team.id} must have exactly one on-call engineer")
    for service in catalogue.services:
        if service.team not in teams:
            raise CatalogueError(f"{service.name}: unknown team {service.team!r}")
        for dependency in service.depends_on:
            if dependency == service.name:
                raise CatalogueError(f"{service.name} depends on itself")
            if dependency not in services:
                raise CatalogueError(f"{service.name} depends on unknown service {dependency!r}")
    _check_acyclic(catalogue.services)
    for incident in catalogue.incidents:
        for name in (*incident.affected, *incident.caused_by):
            if name not in services:
                raise CatalogueError(f"{incident.id}: unknown service {name!r}")
        if not incident.affected:
            raise CatalogueError(f"{incident.id}: needs at least one affected service")
    for fix in catalogue.fixes:
        if fix.incident not in incidents:
            raise CatalogueError(f"{fix.id}: unknown incident {fix.incident!r}")
        if fix.service not in services:
            raise CatalogueError(f"{fix.id}: unknown service {fix.service!r}")
    for runbook in catalogue.runbooks:
        for name in runbook.covers:
            if name not in services:
                raise CatalogueError(f"{runbook.id}: covers unknown service {name!r}")


def _check_acyclic(services: Sequence[Service]) -> None:
    depends = {s.name: s.depends_on for s in services}
    state: dict[str, str] = {}  # "visiting" or "done"

    def visit(name: str, path: list[str]) -> None:
        if state.get(name) == "done":
            return
        if state.get(name) == "visiting":
            cycle = " -> ".join([*path[path.index(name) :], name])
            raise CatalogueError(f"dependency cycle: {cycle}")
        state[name] = "visiting"
        for dependency in depends.get(name, ()):
            visit(dependency, [*path, name])
        state[name] = "done"

    for name in depends:
        visit(name, [])


def validate_against_corpus(catalogue: Catalogue, documents: Sequence[Document]) -> None:
    """Every incident is a postmortem and every runbook a runbook in the corpus, and each one lists
    its document's own service (an incident as affected, a runbook as covered)."""
    by_id = {d.id: d for d in documents if d.tenant_id == catalogue.tenant}
    for incident in catalogue.incidents:
        document = by_id.get(incident.id)
        if document is None or document.type is not DocType.POSTMORTEM:
            raise CatalogueError(f"{incident.id} is not a postmortem in the corpus")
        if document.service and document.service not in incident.affected:
            raise CatalogueError(
                f"incident {incident.id} must list {document.service} (its postmortem's service) "
                "as affected"
            )
    for runbook in catalogue.runbooks:
        document = by_id.get(runbook.id)
        if document is None or document.type is not DocType.RUNBOOK:
            raise CatalogueError(f"{runbook.id} is not a runbook in the corpus")
        if document.service and document.service not in runbook.covers:
            raise CatalogueError(f"{runbook.id} must cover {document.service}")
