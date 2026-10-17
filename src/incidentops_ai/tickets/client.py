"""The host side of the ticket MCP integration (AIENG-52).

`TicketClient` starts the ticket server as a subprocess and talks MCP to it over stdio.
`TicketActions` is what the workflow's `act` step uses: it turns an approved proposal into the
write's arguments, signs the approval over exactly those arguments, and calls the tool. The
idempotency key is the run id, so a retried write returns the ticket the first attempt created.
"""

import json
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp.shared.exceptions import MCPError
from mcp_types import REQUEST_TIMEOUT

from incidentops_ai.tickets.approval import Approval, sign

WRITE_TIMEOUT_S = 10.0


class TicketToolError(Exception):
    """The ticket server refused or failed the call (an `is_error` result)."""


class TicketTimeoutError(TimeoutError):
    """No answer in time. The write may still have happened: retry with the same key."""


class TicketClient:
    def __init__(self, client: Client) -> None:
        self._client = client

    @classmethod
    @asynccontextmanager
    async def connect(
        cls, *, db_path: Path, secret: str | None, extra_env: dict[str, str] | None = None
    ) -> AsyncIterator["TicketClient"]:
        # The secret travels in the server's environment, never on its command line.
        env = {"INCIDENTOPS_TICKETS_DB": str(db_path), **(extra_env or {})}
        if secret:
            env["INCIDENTOPS_APPROVAL_SECRET"] = secret
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "incidentops_ai.tickets.server"], env=env
        )
        async with Client(params) as client:
            yield cls(client)

    async def call(self, name: str, arguments: dict, *, timeout_s: float | None = None) -> Any:
        try:
            result = await self._client.call_tool(name, arguments, read_timeout_seconds=timeout_s)
        except MCPError as error:
            if error.error.code == REQUEST_TIMEOUT:
                raise TicketTimeoutError(f"{name} did not answer in {timeout_s} s") from error
            raise
        if result.is_error:
            raise TicketToolError(" ".join(getattr(c, "text", "") for c in result.content))
        content = result.structured_content
        if content is None:  # a plain `dict` return value arrives as JSON text only
            text = "".join(getattr(c, "text", "") for c in result.content)
            return json.loads(text) if text else None
        if isinstance(content, dict) and set(content) == {"result"}:
            return content["result"]  # a list return value comes wrapped
        return content

    async def search(self, query: str, *, service: str | None = None, limit: int = 5) -> list:
        return await self.call(
            "search_tickets", {"query": query, "service": service, "limit": limit}
        )

    async def get(self, ticket_id: str) -> dict:
        return await self.call("get_ticket", {"ticket_id": ticket_id})


def ticket_arguments(proposal: dict, *, run_id: str) -> dict:
    return {
        "title": proposal["title"],
        "body": proposal["body"],
        "service": proposal["service"],
        "severity": proposal["severity"],
        "idempotency_key": run_id,
    }


class TicketActions:
    """The one write the workflow may make, and only with a recorded decision to show for it."""

    def __init__(self, client: TicketClient, secret: str, *, timeout_s: float = WRITE_TIMEOUT_S):
        self._client = client
        self._secret = secret
        self._timeout_s = timeout_s

    async def probe(self) -> None:
        """A cheap read for the status page (W12)."""
        await self._client.search("health", limit=1)

    async def create(self, proposal: dict, *, run_id: str, decision: dict) -> dict:
        if decision.get("decision") != "approve":
            raise TicketToolError("no approval: the write is not attempted")
        arguments = ticket_arguments(proposal, run_id=run_id)
        approval: Approval = sign(
            self._secret,
            run_id=run_id,
            decision_id=decision["decision_id"],
            reviewer=decision["reviewer"],
            arguments=arguments,
        )
        return await self._client.call(
            "create_ticket",
            {**arguments, "approval": approval.model_dump()},
            timeout_s=self._timeout_s,
        )
