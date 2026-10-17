"""AIENG-56 / H36: the reader role can read the ops schema and nothing else, whatever the SQL."""

import time

import pytest
from ops_support import reader_url
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from incidentops_ai.ops.catalogue import ALLOWED
from incidentops_ai.ops.database import OpsDatabase, OpsQueryError

WRITES = [
    "DROP TABLE ops.alerts",
    "DELETE FROM ops.alerts",
    "UPDATE ops.error_budget SET availability = 1",
    "INSERT INTO ops.services VALUES ('x', 1, 'team', 'owner')",
    "TRUNCATE ops.alerts",
    "CREATE TABLE ops.extra (a int)",
    "ALTER TABLE ops.services ADD COLUMN extra int",
    "COMMENT ON TABLE ops.services IS 'changed'",
    "CREATE TABLE public.extra (a int)",
    "COPY ops.services TO '/tmp/services.csv'",
    "CREATE ROLE intruder",
    "SET ROLE postgres",
]


@pytest.fixture
async def reader(settings):
    engine = create_async_engine(await reader_url(settings.database_url))
    yield engine
    await engine.dispose()


async def test_the_reader_reads_the_allowed_tables(reader):
    result = await OpsDatabase(reader).run("SELECT name, tier FROM ops.services ORDER BY name")

    assert result.columns == ["name", "tier"]
    assert ["billing-service", 1] in result.rows and not result.truncated


async def test_rows_are_capped(reader):
    result = await OpsDatabase(reader, max_rows=10).run("SELECT * FROM ops.oncall_shifts")

    assert len(result.rows) == 10 and result.truncated


@pytest.mark.parametrize("statement", WRITES)
async def test_every_write_fails_in_the_read_only_path(reader, statement):
    with pytest.raises(OpsQueryError) as error:
        await OpsDatabase(reader).run(statement)

    assert error.value.kind in {"permission", "read_only"}


@pytest.mark.parametrize("statement", WRITES)
async def test_privileges_alone_stop_every_write_even_read_write(reader, statement):
    # The layer that does not depend on any parser: no privilege, whatever the transaction mode.
    async with reader.connect() as conn:
        await conn.exec_driver_sql("SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE")
        await conn.commit()
        with pytest.raises(Exception, match="permission denied|must be owner|not allowed"):
            await conn.exec_driver_sql(statement)


@pytest.mark.parametrize(
    "statement", ["SELECT * FROM public.incidents", "SELECT rolpassword FROM pg_authid"]
)
async def test_the_reader_cannot_read_our_tables_or_password_hashes(reader, statement):
    with pytest.raises(OpsQueryError) as error:
        await OpsDatabase(reader).run(statement)

    assert error.value.kind == "permission"


async def test_a_slow_query_is_cancelled_by_the_timeout(reader):
    started = time.perf_counter()
    with pytest.raises(OpsQueryError) as error:
        await OpsDatabase(reader).run("SELECT pg_sleep(5)")

    assert error.value.kind == "timeout" and time.perf_counter() - started < 4


async def test_the_allow_list_matches_the_database(settings):
    engine = create_async_engine(settings.database_url)
    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = 'ops'"
            )
        )
        actual: dict[str, set[str]] = {}
        for table, column in rows:
            actual.setdefault(f"ops.{table}", set()).add(column)
    await engine.dispose()

    assert {t: set(c) for t, c in ALLOWED.items()} == actual
