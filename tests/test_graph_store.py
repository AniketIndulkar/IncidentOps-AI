"""AIENG-35/36: the five traversal questions, run on Neo4j and on the in-memory reference alike."""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from graph_support import fresh_driver

from incidentops_ai.graph.catalogue import load_catalogue, parse_catalogue
from incidentops_ai.graph.memory import InMemoryGraphStore
from incidentops_ai.graph.neo4j_store import Neo4jGraphStore, load_graph
from incidentops_ai.graph.store import GraphStore, ImpactedService, Ownership, RelatedDocument

ROOT = Path(__file__).resolve().parent.parent
CATALOGUE = load_catalogue(ROOT / "data" / "graph" / "catalogue.toml")
OTHER_TENANT = parse_catalogue(
    """
tenant = "acme"
[[teams]]
id = "core"
name = "Core"
[[engineers]]
id = "ann"
name = "Ann"
team = "core"
on_call = true
[[services]]
name = "api"
team = "core"
kind = "internal"
tier = 1
depends_on = ["orders-db"]
[[services]]
name = "orders-db"
team = "core"
kind = "infrastructure"
tier = 1
depends_on = []
"""
)
T = "default"


@pytest.fixture(params=["memory", "neo4j"])
async def store(request) -> AsyncIterator[GraphStore]:
    if request.param == "memory":
        yield InMemoryGraphStore([CATALOGUE, OTHER_TENANT])
        return
    driver = await fresh_driver()
    try:
        await load_graph(driver, CATALOGUE)
        await load_graph(driver, OTHER_TENANT)
        yield Neo4jGraphStore(driver)
    finally:
        await driver.close()


async def test_t1_downstream_impact_lists_dependents_by_distance(store: GraphStore):
    assert await store.downstream_impact("orders-db", tenant=T) == [
        ImpactedService("orders-api", 1),
        ImpactedService("checkout-api", 2),
    ]
    assert await store.downstream_impact("kafka", tenant=T) == [
        ImpactedService("notification-worker", 1),
        ImpactedService("search-indexer", 1),
        ImpactedService("billing-service", 2),
    ]
    assert await store.downstream_impact("kafka", tenant=T, max_hops=1) == [
        ImpactedService("notification-worker", 1),
        ImpactedService("search-indexer", 1),
    ]


async def test_t2_incident_history_covers_the_service_and_what_it_depends_on(store: GraphStore):
    history = await store.incident_history("checkout-api", tenant=T, max_hops=2)

    assert [i.id for i in history] == [
        "pm-2026-08-session",
        "pm-2026-06-payments",
        "pm-2026-05-orders-pool",
        "pm-2026-03-checkout",
    ]
    by_id = {i.id: i for i in history}
    assert by_id["pm-2026-06-payments"].services == ("card-acquirers", "payments-gateway")
    assert by_id["pm-2026-06-payments"].severity == "SEV1"
    assert [i.id for i in await store.incident_history("checkout-api", tenant=T, max_hops=0)] == [
        "pm-2026-03-checkout"
    ]


async def test_t3_ownership_names_the_team_its_people_and_who_is_on_call(store: GraphStore):
    assert await store.ownership("payments-gateway", tenant=T) == Ownership(
        service="payments-gateway",
        team_id="payments",
        team_name="Payments",
        members=("Daniel Kim", "Sofia Marquez"),
        on_call="Sofia Marquez",
    )
    assert await store.ownership("no-such-service", tenant=T) is None


async def test_t4_open_fixes_on_what_a_service_depends_on(store: GraphStore):
    fixes = await store.open_fixes_upstream("checkout-api", tenant=T, max_hops=2)

    assert [(f.service, f.id) for f in fixes] == [
        ("orders-api", "fix-orders-lint"),
        ("payments-gateway", "fix-payments-alert-labels"),
        ("payments-gateway", "fix-payments-circuit-breaker"),
        ("orders-db", "fix-orders-pgbouncer"),
        ("redis-sessions", "fix-session-failover-test"),
    ]
    assert all(f.status == "open" for f in fixes)
    assert fixes[0].incident == "pm-2026-05-orders-pool" and fixes[0].hops == 1


async def test_t5_runbooks_for_a_service_and_its_direct_dependencies(store: GraphStore):
    assert await store.runbooks_for("checkout-api", tenant=T, max_hops=1) == [
        ("rb-checkout-502", 0),
        ("rb-orders-db-pool", 1),
        ("rb-payments-latency", 1),
        ("rb-session-invalid", 1),
    ]


async def test_related_documents_walk_dependencies_both_ways_and_follow_incidents(
    store: GraphStore,
):
    related = await store.related_documents(["payments-gateway"], tenant=T, hops=1)

    assert set(related) == {
        RelatedDocument("rb-payments-latency", "runbook", 0),
        RelatedDocument("pm-2026-06-payments", "postmortem", 0),
        RelatedDocument("rb-checkout-502", "runbook", 1),
        RelatedDocument("pm-2026-03-checkout", "postmortem", 1),
        RelatedDocument("rb-billing-duplicates", "runbook", 1),
        RelatedDocument("pm-2026-09-billing", "postmortem", 1),
    }
    assert [r.distance for r in related] == sorted(r.distance for r in related)
    assert set(await store.related_documents(["payments-gateway"], tenant=T, hops=0)) == {
        RelatedDocument("rb-payments-latency", "runbook", 0),
        RelatedDocument("pm-2026-06-payments", "postmortem", 0),
    }


async def test_related_documents_link_an_incident_to_a_service_with_no_documents_of_its_own(
    store: GraphStore,
):
    # orders-db has one runbook, but the pool incident is a postmortem about orders-api that also
    # affected orders-db: only the graph knows that.
    related = await store.related_documents(["orders-db"], tenant=T, hops=0)

    assert RelatedDocument("pm-2026-05-orders-pool", "postmortem", 0) in related
    assert RelatedDocument("rb-orders-db-pool", "runbook", 0) in related


async def test_service_context_summarises_a_service(store: GraphStore):
    context = await store.service_context("checkout-api", tenant=T)

    assert context is not None
    assert (context.team_name, context.on_call) == ("Commerce Platform", "Amara Okafor")
    assert context.depends_on == ("orders-api", "payments-gateway", "session-service")
    assert context.dependents == ()
    assert [i.id for i in context.recent_incidents] == ["pm-2026-03-checkout"]
    assert await store.service_context("no-such-service", tenant=T) is None


async def test_tenants_never_see_each_others_graph(store: GraphStore):
    assert await store.downstream_impact("orders-db", tenant="acme") == [ImpactedService("api", 1)]
    assert "api" not in await store.known_services(tenant=T)
    assert len(await store.known_services(tenant=T)) == 15
    assert await store.known_services(tenant="nobody") == set()


async def test_hop_limits_are_bounded(store: GraphStore):
    with pytest.raises(ValueError, match="hops"):
        await store.downstream_impact("kafka", tenant=T, max_hops=9)


async def test_an_unreachable_neo4j_is_reported_as_graph_unavailable():
    from neo4j import AsyncGraphDatabase

    from incidentops_ai.graph.store import GraphUnavailableError

    driver = AsyncGraphDatabase.driver(
        "bolt://127.0.0.1:1", auth=("neo4j", "x"), connection_timeout=1
    )
    try:
        with pytest.raises(GraphUnavailableError):
            await Neo4jGraphStore(driver).known_services(tenant=T)
    finally:
        await driver.close()
