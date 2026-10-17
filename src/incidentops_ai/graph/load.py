"""Load the service catalogue into Neo4j (AIENG-35).

    uv run python -m incidentops_ai.graph.load [--catalogue data/graph/catalogue.toml]

Needs INCIDENTOPS_NEO4J_URI and INCIDENTOPS_NEO4J_PASSWORD. The catalogue is checked against the
document corpus first, so a graph can never point at a runbook or postmortem that does not exist.
Loading replaces the tenant's graph, so it is safe to run again after any edit.
"""

import argparse
import asyncio
from collections.abc import Sequence
from pathlib import Path

from neo4j import AsyncGraphDatabase

from incidentops_ai.config import Settings
from incidentops_ai.graph.catalogue import load_catalogue, validate_against_corpus
from incidentops_ai.graph.neo4j_store import load_graph
from incidentops_ai.retrieval.documents import load_corpus


async def run_load(settings: Settings, catalogue_path: Path, corpus: Path) -> dict[str, int]:
    if not settings.neo4j_uri or settings.neo4j_password is None:
        raise ValueError(
            "set INCIDENTOPS_NEO4J_URI and INCIDENTOPS_NEO4J_PASSWORD to load the graph"
        )
    catalogue = load_catalogue(catalogue_path)
    validate_against_corpus(catalogue, load_corpus(corpus))
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password.get_secret_value())
    )
    try:
        return await load_graph(driver, catalogue)
    finally:
        await driver.close()


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Load the service catalogue into Neo4j.")
    parser.add_argument("--catalogue", type=Path, default=Path("data/graph/catalogue.toml"))
    parser.add_argument("--corpus", type=Path, default=Path("data/corpus"))
    args = parser.parse_args(argv)
    counts = asyncio.run(run_load(Settings(), args.catalogue, args.corpus))
    print(", ".join(f"{label} {count}" for label, count in sorted(counts.items())))


if __name__ == "__main__":
    main()
