"""AIENG-35: the load command validates the catalogue against the corpus, then loads Neo4j."""

from pathlib import Path

import pytest
from graph_support import TEST_NEO4J_AUTH, TEST_NEO4J_URI, fresh_driver
from pydantic import SecretStr

from incidentops_ai.config import Settings
from incidentops_ai.graph.load import run_load

ROOT = Path(__file__).resolve().parent.parent


def neo4j_settings() -> Settings:
    return Settings(
        _env_file=None,
        neo4j_uri=TEST_NEO4J_URI,
        neo4j_user=TEST_NEO4J_AUTH[0],
        neo4j_password=SecretStr(TEST_NEO4J_AUTH[1]),
    )


async def test_the_command_loads_the_validated_catalogue():
    driver = await fresh_driver()
    await driver.close()

    counts = await run_load(
        neo4j_settings(), ROOT / "data" / "graph" / "catalogue.toml", ROOT / "data" / "corpus"
    )

    assert counts["Service"] == 15 and counts["Incident"] == 7


async def test_the_command_refuses_a_catalogue_that_disagrees_with_the_corpus(tmp_path: Path):
    bad = tmp_path / "catalogue.toml"
    text = (ROOT / "data" / "graph" / "catalogue.toml").read_text(encoding="utf-8")
    bad.write_text(text.replace('id = "rb-checkout-502"', 'id = "rb-not-there"'), encoding="utf-8")

    with pytest.raises(ValueError, match="rb-not-there is not a runbook"):
        await run_load(neo4j_settings(), bad, ROOT / "data" / "corpus")


async def test_the_command_needs_neo4j_settings():
    with pytest.raises(ValueError, match="INCIDENTOPS_NEO4J_URI"):
        await run_load(
            Settings(_env_file=None),
            ROOT / "data" / "graph" / "catalogue.toml",
            ROOT / "data" / "corpus",
        )
