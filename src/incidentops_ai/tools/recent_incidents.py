"""Read-only tool: recent incidents for one service, from our own database."""

import re
import uuid
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from incidentops_ai.incidents.repository import IncidentRepository
from incidentops_ai.llm.provider import ToolSpec
from incidentops_ai.tools.registry import Tool

MAX_LIMIT = 10
_SERVICE_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


def _service(value: str) -> str:
    if not _SERVICE_NAME.fullmatch(value):
        raise ValueError("must be lowercase kebab-case")
    return value


def _limit(value: int) -> int:
    if not 1 <= value <= MAX_LIMIT:
        raise ValueError(f"must be between 1 and {MAX_LIMIT}")
    return value


class GetRecentIncidentsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service: Annotated[str, AfterValidator(_service)]
    limit: Annotated[int, AfterValidator(_limit)]


SPEC = ToolSpec(
    name="get_recent_incidents",
    description=(
        "List the most recent incidents recorded for one service, newest first. "
        "Use it to check whether a new incident repeats or relates to an earlier one. "
        f"service is the lowercase kebab-case service name; limit is 1 to {MAX_LIMIT}. "
        "Read-only. To check several services, call this tool once per service in the same turn."
    ),
    arguments=GetRecentIncidentsArgs,
)


def make_recent_incidents_tool(
    session_factory: async_sessionmaker[AsyncSession], *, exclude_id: uuid.UUID | None = None
) -> Tool:
    """`exclude_id`: the incident being analysed, so it is never reported as related to itself."""

    async def handler(args: GetRecentIncidentsArgs) -> list[dict]:
        # Own session per call: tool calls run concurrently, and a session must not be shared
        # between concurrent tasks.
        async with session_factory() as session:
            incidents = await IncidentRepository(session).list_recent_by_service(
                args.service, args.limit, exclude_id=exclude_id
            )
        # Titles only, never descriptions: less sensitive text sent to the model, and less room
        # for text planted in an old incident to steer it.
        return [
            {
                "id": str(i.id),
                "title": i.title,
                "severity": i.severity,
                "status": i.status,
                "environment": i.environment,
                "created_at": i.created_at.isoformat(),
            }
            for i in incidents
        ]

    return Tool(spec=SPEC, handler=handler)
