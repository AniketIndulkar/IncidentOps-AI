"""Shared helpers for tests that need the Neo4j test container (TEST_NEO4J_URI, default port 7688)."""

import os

from neo4j import AsyncDriver, AsyncGraphDatabase

TEST_NEO4J_URI = os.environ.get("TEST_NEO4J_URI", "bolt://localhost:7688")
TEST_NEO4J_AUTH = (
    os.environ.get("TEST_NEO4J_USER", "neo4j"),
    os.environ.get("TEST_NEO4J_PASSWORD", "incidentops-test"),
)


async def fresh_driver() -> AsyncDriver:
    """A driver to an empty test database. Every graph test starts from nothing."""
    driver = AsyncGraphDatabase.driver(TEST_NEO4J_URI, auth=TEST_NEO4J_AUTH)
    async with driver.session() as session:
        await session.run("MATCH (n) DETACH DELETE n")
    return driver
