"""AIENG-35: loading the catalogue into Neo4j is repeatable, replaces a tenant's graph, and is constrained."""

from dataclasses import replace
from pathlib import Path

from graph_support import fresh_driver
from test_graph_store import OTHER_TENANT

from incidentops_ai.graph.catalogue import load_catalogue
from incidentops_ai.graph.neo4j_store import LABELS, Neo4jGraphStore, load_graph

ROOT = Path(__file__).resolve().parent.parent
CATALOGUE = load_catalogue(ROOT / "data" / "graph" / "catalogue.toml")
EXPECTED = {"Team": 9, "Engineer": 18, "Service": 15, "Incident": 7, "Fix": 24, "Runbook": 11}


async def test_loading_twice_gives_the_same_graph():
    driver = await fresh_driver()
    try:
        first = await load_graph(driver, CATALOGUE)
        second = await load_graph(driver, CATALOGUE)
    finally:
        await driver.close()

    assert first == second == EXPECTED


async def test_a_reload_replaces_the_tenants_graph_so_removed_facts_disappear():
    driver = await fresh_driver()
    try:
        await load_graph(driver, CATALOGUE)
        smaller = replace(CATALOGUE, fixes=CATALOGUE.fixes[1:])
        counts = await load_graph(driver, smaller)
        store = Neo4jGraphStore(driver)
        fixes = await store.open_fixes_upstream("notification-worker", tenant="default", max_hops=1)
    finally:
        await driver.close()

    assert counts["Fix"] == 23
    assert [f.id for f in fixes] == ["fix-kafka-cert-manager"]  # kafka's open fix is still there


async def test_loading_one_tenant_leaves_another_alone():
    driver = await fresh_driver()
    try:
        await load_graph(driver, OTHER_TENANT)
        await load_graph(driver, CATALOGUE)
        await load_graph(driver, CATALOGUE)
        services = await Neo4jGraphStore(driver).known_services(tenant="acme")
    finally:
        await driver.close()

    assert services == {"api", "orders-db"}


async def test_every_label_has_a_uniqueness_constraint_on_uid():
    driver = await fresh_driver()
    try:
        await load_graph(driver, CATALOGUE)
        async with driver.session() as session:
            result = await session.run(
                "SHOW CONSTRAINTS YIELD name, labelsOrTypes, properties, type"
            )
            rows = await result.data()
    finally:
        await driver.close()

    constrained = {r["labelsOrTypes"][0] for r in rows if r["properties"] == ["uid"]}
    assert constrained >= set(LABELS)
    assert all("UNIQUENESS" in r["type"] for r in rows if r["properties"] == ["uid"])
