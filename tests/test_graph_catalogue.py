"""AIENG-34/35: the service catalogue loads, and every fact it states is consistent with the corpus."""

from pathlib import Path

import pytest

from incidentops_ai.graph.catalogue import (
    CatalogueError,
    load_catalogue,
    parse_catalogue,
    validate_against_corpus,
)
from incidentops_ai.retrieval.documents import load_corpus

ROOT = Path(__file__).resolve().parent.parent
CATALOGUE = load_catalogue(ROOT / "data" / "graph" / "catalogue.toml")
DOCUMENTS = load_corpus(ROOT / "data" / "corpus")

MINIMAL = """
tenant = "t1"
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
depends_on = ["db"]
[[services]]
name = "db"
team = "core"
kind = "infrastructure"
tier = 1
depends_on = []
"""


def test_the_real_catalogue_has_the_expected_shape():
    assert CATALOGUE.tenant == "default"
    assert (len(CATALOGUE.teams), len(CATALOGUE.engineers), len(CATALOGUE.services)) == (9, 18, 15)
    assert (len(CATALOGUE.incidents), len(CATALOGUE.fixes), len(CATALOGUE.runbooks)) == (7, 24, 11)
    checkout = CATALOGUE.service("checkout-api")
    assert checkout.depends_on == ("payments-gateway", "orders-api", "session-service")
    assert checkout.team == "commerce-platform"


def test_the_real_catalogue_agrees_with_the_corpus():
    validate_against_corpus(CATALOGUE, DOCUMENTS)


def test_a_minimal_catalogue_parses():
    catalogue = parse_catalogue(MINIMAL)

    assert catalogue.tenant == "t1"
    assert [s.name for s in catalogue.services] == ["api", "db"]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            lambda t: t.replace(
                'team = "core"\nkind = "internal"', 'team = "nobody"\nkind = "internal"'
            ),
            "unknown team 'nobody'",
        ),
        (
            lambda t: t.replace('depends_on = ["db"]', 'depends_on = ["cache"]'),
            "depends on unknown service 'cache'",
        ),
        (lambda t: t.replace('depends_on = ["db"]', 'depends_on = ["api"]'), "depends on itself"),
        (lambda t: t.replace("depends_on = []", 'depends_on = ["api"]'), "dependency cycle"),
        (lambda t: t.replace("on_call = true", "on_call = false"), "exactly one on-call engineer"),
        (lambda t: t + '[[teams]]\nid = "core"\nname = "Again"\n', "duplicate team id 'core'"),
        (lambda t: t.replace('kind = "internal"', 'kind = "magic"'), "kind must be one of"),
    ],
    ids=[
        "unknown-team",
        "unknown-dependency",
        "self-dependency",
        "cycle",
        "on-call",
        "duplicate",
        "kind",
    ],
)
def test_inconsistent_catalogues_are_refused_with_the_reason(change, message):
    with pytest.raises(CatalogueError, match=message):
        parse_catalogue(change(MINIMAL))


def test_an_incident_must_be_a_postmortem_in_the_corpus():
    text = MINIMAL + (
        '[[incidents]]\nid = "pm-imaginary"\ndate = "2026-01-01"\nseverity = "SEV2"\n'
        'duration_minutes = 5\naffected = ["api"]\ncaused_by = ["api"]\n'
    )

    with pytest.raises(CatalogueError, match="pm-imaginary is not a postmortem in the corpus"):
        validate_against_corpus(parse_catalogue(text), DOCUMENTS)


def test_an_incident_must_list_its_postmortems_service_as_affected():
    text = CATALOGUE_TEXT.replace(
        'affected = ["checkout-api"]\ncaused_by = ["checkout-api"]',
        'affected = ["orders-api"]\ncaused_by = ["checkout-api"]',
    )

    with pytest.raises(CatalogueError, match="pm-2026-03-checkout.*checkout-api"):
        validate_against_corpus(parse_catalogue(text), DOCUMENTS)


def test_a_runbook_must_cover_its_own_front_matter_service():
    text = CATALOGUE_TEXT.replace(
        'id = "rb-checkout-502"\ncovers = ["checkout-api"]',
        'id = "rb-checkout-502"\ncovers = ["cdn"]',
    )

    with pytest.raises(CatalogueError, match="rb-checkout-502 must cover checkout-api"):
        validate_against_corpus(parse_catalogue(text), DOCUMENTS)


def test_a_fix_must_point_at_a_known_incident_and_service():
    text = CATALOGUE_TEXT.replace('incident = "pm-2026-02-kafka"', 'incident = "pm-gone"', 1)

    with pytest.raises(CatalogueError, match="fix-kafka-renew-cert: unknown incident 'pm-gone'"):
        parse_catalogue(text)


CATALOGUE_TEXT = (ROOT / "data" / "graph" / "catalogue.toml").read_text(encoding="utf-8")
