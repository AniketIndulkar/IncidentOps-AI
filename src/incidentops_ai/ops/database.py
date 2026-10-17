"""Running a validated query against the ops database as the reader role (AIENG-56, AIENG-57).

Every query runs in a READ ONLY transaction with a local statement timeout, through an engine that
logs in as a user granted only `incidentops_ops_reader`. The SQL is sent as is (no SQLAlchemy text
parsing, which would read `12:00` as a bind parameter); it has been validated before it gets here.
"""

from typing import Literal

from pydantic import BaseModel
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

MAX_ROWS = 100
TIMEOUT_MS = 2000

FailureKind = Literal["permission", "read_only", "timeout", "failed"]
_SQLSTATES: dict[str, FailureKind] = {
    "42501": "permission",  # insufficient_privilege
    "25006": "read_only",  # read_only_sql_transaction
    "57014": "timeout",  # query_canceled (statement_timeout)
}


class OpsQueryError(Exception):
    def __init__(self, kind: FailureKind, sqlstate: str | None) -> None:
        super().__init__(f"the ops query failed: {kind}" + (f" ({sqlstate})" if sqlstate else ""))
        self.kind, self.sqlstate = kind, sqlstate


class OpsResult(BaseModel):
    columns: list[str]
    rows: list[list]
    truncated: bool  # more rows existed than were returned


def _sqlstate(error: DBAPIError) -> str | None:
    original = error.orig
    return getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)


class OpsDatabase:
    def __init__(
        self, engine: AsyncEngine, *, timeout_ms: int = TIMEOUT_MS, max_rows: int = MAX_ROWS
    ) -> None:
        self._engine = engine
        self._timeout_ms = int(timeout_ms)
        self._max_rows = max_rows

    async def run(self, sql: str) -> OpsResult:
        try:
            async with self._engine.connect() as connection, connection.begin():
                await connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                await connection.exec_driver_sql(
                    f"SET LOCAL statement_timeout = {self._timeout_ms}"
                )
                result = await connection.exec_driver_sql(sql)
                rows = result.fetchmany(self._max_rows + 1)
                columns = list(result.keys())
        except DBAPIError as error:
            state = _sqlstate(error)
            raise OpsQueryError(_SQLSTATES.get(state or "", "failed"), state) from None
        return OpsResult(
            columns=columns,
            rows=[list(r) for r in rows[: self._max_rows]],
            truncated=len(rows) > self._max_rows,
        )
