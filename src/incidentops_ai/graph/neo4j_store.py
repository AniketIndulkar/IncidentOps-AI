"""The catalogue as a Neo4j graph: loading it (AIENG-35) and the Cypher behind each question (AIENG-36).

Schema
    (:Team)-[:OWNS]->(:Service)            (:Engineer)-[:MEMBER_OF]->(:Team)
    (:Service)-[:DEPENDS_ON]->(:Service)
    (:Incident)-[:AFFECTED]->(:Service)    (:Incident)-[:CAUSED_BY]->(:Service)
    (:Incident)-[:RESOLVED_BY]->(:Fix)-[:CHANGES]->(:Service)
    (:Runbook)-[:COVERS]->(:Service)

Every node carries `tenant_id` and a `uid` of "<tenant>/<id>", unique per label. Community edition
has no node-key or composite constraints, so one string key does the job. Loading replaces a
tenant's whole graph in one transaction: readers see the old graph or the new one, never half.
"""

from collections.abc import Sequence

from neo4j import AsyncDriver, AsyncManagedTransaction

from incidentops_ai.graph.catalogue import Catalogue
from incidentops_ai.graph.store import (
    FixRef,
    ImpactedService,
    IncidentRef,
    Ownership,
    RelatedDocument,
    ServiceContext,
    check_hops,
)

LABELS = ("Team", "Engineer", "Service", "Incident", "Fix", "Runbook")
RECENT_INCIDENTS = 5


def _uid(tenant: str, key: str) -> str:
    return f"{tenant}/{key}"


async def ensure_constraints(driver: AsyncDriver, *, database: str | None = None) -> None:
    async with driver.session(database=database) as session:
        for label in LABELS:
            await session.run(
                f"CREATE CONSTRAINT {label.lower()}_uid IF NOT EXISTS "
                f"FOR (n:{label}) REQUIRE n.uid IS UNIQUE"
            )


def _rows(catalogue: Catalogue) -> list[tuple[str, list[dict]]]:
    t = catalogue.tenant
    return [
        (
            "UNWIND $rows AS r CREATE (:Team {uid: r.uid, tenant_id: $tenant, id: r.id, name: r.name})",
            [{"uid": _uid(t, x.id), "id": x.id, "name": x.name} for x in catalogue.teams],
        ),
        (
            (
                "UNWIND $rows AS r MATCH (team:Team {uid: r.team}) "
                "CREATE (:Engineer {uid: r.uid, tenant_id: $tenant, id: r.id, name: r.name, "
                "on_call: r.on_call})-[:MEMBER_OF]->(team)"
            ),
            [
                {
                    "uid": _uid(t, e.id),
                    "id": e.id,
                    "name": e.name,
                    "on_call": e.on_call,
                    "team": _uid(t, e.team),
                }
                for e in catalogue.engineers
            ],
        ),
        (
            (
                "UNWIND $rows AS r MATCH (team:Team {uid: r.team}) "
                "CREATE (team)-[:OWNS]->(:Service {uid: r.uid, tenant_id: $tenant, name: r.name, "
                "kind: r.kind, tier: r.tier})"
            ),
            [
                {
                    "uid": _uid(t, s.name),
                    "name": s.name,
                    "kind": s.kind,
                    "tier": s.tier,
                    "team": _uid(t, s.team),
                }
                for s in catalogue.services
            ],
        ),
        (
            (
                "UNWIND $rows AS r MATCH (a:Service {uid: r.source}), (b:Service {uid: r.target}) "
                "CREATE (a)-[:DEPENDS_ON]->(b)"
            ),
            [
                {"source": _uid(t, s.name), "target": _uid(t, d)}
                for s in catalogue.services
                for d in s.depends_on
            ],
        ),
        (
            (
                "UNWIND $rows AS r CREATE (:Incident {uid: r.uid, tenant_id: $tenant, id: r.id, "
                "date: r.date, severity: r.severity, duration_minutes: r.duration})"
            ),
            [
                {
                    "uid": _uid(t, i.id),
                    "id": i.id,
                    "date": i.date,
                    "severity": i.severity,
                    "duration": i.duration_minutes,
                }
                for i in catalogue.incidents
            ],
        ),
        (
            (
                "UNWIND $rows AS r MATCH (i:Incident {uid: r.incident}), (s:Service {uid: r.service}) "
                "CREATE (i)-[:AFFECTED]->(s)"
            ),
            [
                {"incident": _uid(t, i.id), "service": _uid(t, s)}
                for i in catalogue.incidents
                for s in i.affected
            ],
        ),
        (
            (
                "UNWIND $rows AS r MATCH (i:Incident {uid: r.incident}), (s:Service {uid: r.service}) "
                "CREATE (i)-[:CAUSED_BY]->(s)"
            ),
            [
                {"incident": _uid(t, i.id), "service": _uid(t, s)}
                for i in catalogue.incidents
                for s in i.caused_by
            ],
        ),
        (
            (
                "UNWIND $rows AS r MATCH (i:Incident {uid: r.incident}), (s:Service {uid: r.service}) "
                "CREATE (i)-[:RESOLVED_BY]->(:Fix {uid: r.uid, tenant_id: $tenant, id: r.id, "
                "kind: r.kind, status: r.status, description: r.description})-[:CHANGES]->(s)"
            ),
            [
                {
                    "uid": _uid(t, f.id),
                    "id": f.id,
                    "kind": f.kind,
                    "status": f.status,
                    "description": f.description,
                    "incident": _uid(t, f.incident),
                    "service": _uid(t, f.service),
                }
                for f in catalogue.fixes
            ],
        ),
        (
            (
                "UNWIND $rows AS r CREATE (:Runbook {uid: r.uid, tenant_id: $tenant, id: r.id, "
                "general: r.general})"
            ),
            [{"uid": _uid(t, r.id), "id": r.id, "general": r.general} for r in catalogue.runbooks],
        ),
        (
            (
                "UNWIND $rows AS r MATCH (b:Runbook {uid: r.runbook}), (s:Service {uid: r.service}) "
                "CREATE (b)-[:COVERS]->(s)"
            ),
            [
                {"runbook": _uid(t, r.id), "service": _uid(t, s)}
                for r in catalogue.runbooks
                for s in r.covers
            ],
        ),
    ]


async def load_graph(
    driver: AsyncDriver, catalogue: Catalogue, *, database: str | None = None
) -> dict[str, int]:
    """Replace the tenant's graph with the catalogue. Returns the node count per label."""
    await ensure_constraints(driver, database=database)

    async def replace(tx: AsyncManagedTransaction) -> None:
        await tx.run("MATCH (n {tenant_id: $tenant}) DETACH DELETE n", tenant=catalogue.tenant)
        for statement, rows in _rows(catalogue):
            if rows:
                await tx.run(statement, rows=rows, tenant=catalogue.tenant)

    async with driver.session(database=database) as session:
        await session.execute_write(replace)
        result = await session.run(
            "MATCH (n {tenant_id: $tenant}) RETURN labels(n)[0] AS label, count(*) AS n",
            tenant=catalogue.tenant,
        )
        return {record["label"]: record["n"] async for record in result}


class Neo4jGraphStore:
    def __init__(self, driver: AsyncDriver, *, database: str | None = None) -> None:
        self._driver = driver
        self._database = database

    async def _read(self, query: str, **params) -> list[dict]:
        async with self._driver.session(database=self._database) as session:
            result = await session.run(query, **params)
            return await result.data()

    async def known_services(self, *, tenant: str) -> set[str]:
        rows = await self._read(
            "MATCH (s:Service {tenant_id: $tenant}) RETURN s.name AS name", tenant=tenant
        )
        return {row["name"] for row in rows}

    async def downstream_impact(
        self, service: str, *, tenant: str, max_hops: int = 3
    ) -> list[ImpactedService]:
        hops = check_hops(max_hops, minimum=1)
        rows = await self._read(
            "MATCH (target:Service {uid: $uid}) "
            f"MATCH p = (s:Service)-[:DEPENDS_ON*1..{hops}]->(target) "
            "RETURN s.name AS service, min(length(p)) AS hops ORDER BY hops, service",
            uid=_uid(tenant, service),
        )
        return [ImpactedService(row["service"], row["hops"]) for row in rows]

    async def incident_history(
        self, service: str, *, tenant: str, max_hops: int = 2
    ) -> list[IncidentRef]:
        hops = check_hops(max_hops)
        rows = await self._read(
            f"MATCH (:Service {{uid: $uid}})-[:DEPENDS_ON*0..{hops}]->(s:Service) "
            "WITH DISTINCT s "
            "MATCH (i:Incident)-[:AFFECTED|CAUSED_BY]->(s) "
            "WITH i, collect(DISTINCT s.name) AS services "
            "RETURN i.id AS id, i.date AS date, i.severity AS severity, services "
            "ORDER BY date DESC, id",
            uid=_uid(tenant, service),
        )
        return [
            IncidentRef(row["id"], row["date"], row["severity"], tuple(sorted(row["services"])))
            for row in rows
        ]

    async def ownership(self, service: str, *, tenant: str) -> Ownership | None:
        rows = await self._read(
            "MATCH (team:Team)-[:OWNS]->(:Service {uid: $uid}) "
            "MATCH (e:Engineer)-[:MEMBER_OF]->(team) "
            "RETURN team.id AS team_id, team.name AS team_name, collect(e.name) AS members, "
            "[x IN collect(e) WHERE x.on_call | x.name] AS on_call",
            uid=_uid(tenant, service),
        )
        if not rows:
            return None
        row = rows[0]
        on_call = row["on_call"][0] if row["on_call"] else None
        return Ownership(
            service, row["team_id"], row["team_name"], tuple(sorted(row["members"])), on_call
        )

    async def open_fixes_upstream(
        self, service: str, *, tenant: str, max_hops: int = 2
    ) -> list[FixRef]:
        hops = check_hops(max_hops, minimum=1)
        rows = await self._read(
            f"MATCH p = (:Service {{uid: $uid}})-[:DEPENDS_ON*1..{hops}]->(s:Service) "
            "WITH s, min(length(p)) AS hops "
            "MATCH (i:Incident)-[:RESOLVED_BY]->(f:Fix {status: 'open'})-[:CHANGES]->(s) "
            "RETURN f.id AS id, s.name AS service, i.id AS incident, f.kind AS kind, "
            "f.status AS status, f.description AS description, hops "
            "ORDER BY hops, service, id",
            uid=_uid(tenant, service),
        )
        return [FixRef(**row) for row in rows]

    async def runbooks_for(
        self, service: str, *, tenant: str, max_hops: int = 1
    ) -> list[tuple[str, int]]:
        hops = check_hops(max_hops)
        rows = await self._read(
            f"MATCH p = (:Service {{uid: $uid}})-[:DEPENDS_ON*0..{hops}]->(s:Service) "
            "WITH s, min(length(p)) AS hops "
            "MATCH (r:Runbook)-[:COVERS]->(s) "
            "RETURN r.id AS id, min(hops) AS distance ORDER BY distance, id",
            uid=_uid(tenant, service),
        )
        return [(row["id"], row["distance"]) for row in rows]

    async def related_documents(
        self, services: Sequence[str], *, tenant: str, hops: int = 1
    ) -> list[RelatedDocument]:
        limit = check_hops(hops)
        uids = [_uid(tenant, s) for s in services]
        reach = (
            "MATCH (a:Service) WHERE a.uid IN $uids "
            f"MATCH p = (a)-[:DEPENDS_ON*0..{limit}]-(s:Service) "
            "WITH s, min(length(p)) AS d "
        )
        runbooks = await self._read(
            reach + "MATCH (r:Runbook)-[:COVERS]->(s) RETURN r.id AS doc, min(d) AS distance",
            uids=uids,
        )
        incidents = await self._read(
            reach + "MATCH (i:Incident)-[:AFFECTED|CAUSED_BY]->(s) "
            "RETURN i.id AS doc, min(d) AS distance",
            uids=uids,
        )
        documents = [RelatedDocument(r["doc"], "runbook", r["distance"]) for r in runbooks] + [
            RelatedDocument(r["doc"], "postmortem", r["distance"]) for r in incidents
        ]
        return sorted(documents, key=lambda d: (d.distance, d.kind, d.doc_id))

    async def service_context(self, service: str, *, tenant: str) -> ServiceContext | None:
        rows = await self._read(
            "MATCH (team:Team)-[:OWNS]->(s:Service {uid: $uid}) "
            "OPTIONAL MATCH (e:Engineer {on_call: true})-[:MEMBER_OF]->(team) "
            "OPTIONAL MATCH (s)-[:DEPENDS_ON]->(d:Service) "
            "OPTIONAL MATCH (u:Service)-[:DEPENDS_ON]->(s) "
            "RETURN team.name AS team_name, collect(DISTINCT e.name) AS on_call, "
            "collect(DISTINCT d.name) AS depends_on, collect(DISTINCT u.name) AS dependents",
            uid=_uid(tenant, service),
        )
        if not rows:
            return None
        row = rows[0]
        incidents = await self._read(
            "MATCH (i:Incident)-[:AFFECTED|CAUSED_BY]->(:Service {uid: $uid}) "
            "RETURN DISTINCT i.id AS id, i.date AS date, i.severity AS severity "
            f"ORDER BY date DESC, id LIMIT {RECENT_INCIDENTS}",
            uid=_uid(tenant, service),
        )
        return ServiceContext(
            service=service,
            team_name=row["team_name"],
            on_call=row["on_call"][0] if row["on_call"] else None,
            depends_on=tuple(sorted(row["depends_on"])),
            dependents=tuple(sorted(row["dependents"])),
            recent_incidents=tuple(
                IncidentRef(i["id"], i["date"], i["severity"], (service,)) for i in incidents
            ),
        )
