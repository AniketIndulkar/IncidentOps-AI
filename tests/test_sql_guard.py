"""AIENG-57 / H37: the validator refuses every attack and accepts every reference query."""

import json
from pathlib import Path

import pytest
from ops_support import reader_url
from sqlalchemy.ext.asyncio import create_async_engine

from incidentops_ai.ops.database import OpsDatabase
from incidentops_ai.ops.sql_guard import SqlRefused, validate

pytestmark = pytest.mark.redteam  # security regressions: their own CI step (W10)

ATTACKS = [json.loads(line) for line in Path("evals/sql/attacks.jsonl").read_text().splitlines()]
QUESTIONS = [
    json.loads(line) for line in Path("evals/sql/questions.jsonl").read_text().splitlines()
]
RULE = {
    "write": {"select_only"},
    "multi-statement": {"one_statement"},
    "catalog": {"table_not_allowed"},
    "function": {"function_not_allowed"},
    "locking": {"select_only"},
    "into": {"select_only"},
    "copy": {"select_only"},
    "session": {"select_only"},
    "unknown-table": {"table_not_allowed"},
    "unknown-column": {"column_not_allowed"},
    "write-in-cte": {"select_only"},
}


@pytest.mark.parametrize("attack", ATTACKS, ids=lambda a: f"{a['id']}-{a['kind']}")
def test_every_attack_is_refused_with_its_rule(attack):
    with pytest.raises(SqlRefused) as refused:
        validate(attack["sql"])

    assert refused.value.rule in RULE[attack["kind"]]


@pytest.mark.parametrize("question", QUESTIONS, ids=lambda q: q["id"])
async def test_every_reference_query_is_accepted_and_runs_as_the_reader(settings, question):
    safe = validate(question["reference_sql"])
    engine = create_async_engine(await reader_url(settings.database_url))
    try:
        result = await OpsDatabase(engine).run(safe)
    finally:
        await engine.dispose()

    assert result.rows, question["id"]  # every question has a non-empty answer in the seed


@pytest.mark.parametrize(
    ("sql", "limit"),
    [
        ("SELECT name FROM ops.services", "LIMIT 100"),
        ("SELECT name FROM ops.services LIMIT 5", "LIMIT 5"),
        ("SELECT name FROM ops.services LIMIT 5000", "LIMIT 100"),
    ],
)
def test_a_limit_is_added_or_capped(sql, limit):
    assert validate(sql).endswith(limit)


@pytest.mark.parametrize(
    "sql",
    [
        (
            "WITH recent AS (SELECT service FROM ops.alerts) SELECT service, count(*) AS n "
            "FROM recent GROUP BY service ORDER BY n DESC"
        ),
        "SELECT d.service FROM ops.deployments AS d JOIN ops.services AS s ON s.name = d.service",
        "SELECT name FROM ops.services UNION SELECT service FROM ops.alerts",
    ],
)
def test_ctes_joins_and_unions_over_allowed_tables_are_accepted(sql):
    assert validate(sql)


@pytest.mark.parametrize(
    ("sql", "rule"),
    [
        ("", "unparseable"),
        ("SELEC name FROM", "unparseable"),
        ("SELECT name FROM services", "table_not_allowed"),  # unqualified: not on the list
        ("SELECT name FROM ops.services LIMIT (SELECT 1)", "limit_not_literal"),
    ],
)
def test_other_refusals(sql, rule):
    with pytest.raises(SqlRefused) as refused:
        validate(sql)

    assert refused.value.rule == rule


def test_unquoted_names_fold_to_lower_case_as_postgres_does():
    assert "ops.services" in validate("select NAME from OPS.SERVICES")


def test_a_quoted_name_keeps_its_case_and_is_not_on_the_list():
    with pytest.raises(SqlRefused) as refused:
        validate('SELECT name FROM "OPS"."SERVICES"')

    assert refused.value.rule == "table_not_allowed"


@pytest.mark.parametrize("function", ["current_user", "session_user", "current_database()"])
def test_identity_functions_are_refused(function):
    with pytest.raises(SqlRefused) as refused:
        validate(f"SELECT name FROM ops.services WHERE name = {function}")

    assert refused.value.rule == "function_not_allowed"
