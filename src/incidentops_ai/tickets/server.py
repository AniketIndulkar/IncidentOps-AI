"""The ticket system as an MCP server over stdio (AIENG-52).

    INCIDENTOPS_TICKETS_DB=tickets.db INCIDENTOPS_APPROVAL_SECRET=... \
        python -m incidentops_ai.tickets.server

Read tools (`search_tickets`, `get_ticket`) need nothing. The write tool (`create_ticket`) needs an
approval signed by the host over the exact arguments; anything else is refused with a ToolError the
caller can read. `INCIDENTOPS_TICKETS_FAULT_DELAY_S` (tests only) delays the answer to a write after
the ticket is stored: the "it worked but the reply timed out" fault.

Every tool is async: the SDK runs plain functions in worker threads, and the SQLite connection
belongs to the one thread (the event loop's) that opened it.
"""

import asyncio
import os
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from incidentops_ai.tickets.approval import Approval, verify
from incidentops_ai.tickets.store import TicketStore

server = MCPServer("incidentops-tickets")
_store: TicketStore | None = None


def store() -> TicketStore:
    global _store
    if _store is None:
        _store = TicketStore(Path(os.environ["INCIDENTOPS_TICKETS_DB"]))
    return _store


@server.tool()
async def search_tickets(query: str, service: str | None = None, limit: int = 5) -> list[dict]:
    """Find tickets whose title or body contains every word of the query (read-only)."""
    return store().search(query, service=service, limit=max(1, min(limit, 20)))


@server.tool()
async def get_ticket(ticket_id: str) -> dict:
    """One ticket by id, e.g. TCK-0003 (read-only)."""
    ticket = store().get(ticket_id)
    if ticket is None:
        raise ToolError(f"no ticket {ticket_id}")
    return ticket


@server.tool()
async def create_ticket(
    title: str,
    body: str,
    service: str,
    severity: str,
    idempotency_key: str,
    approval: Approval | None = None,
) -> dict:
    """Open a ticket. Needs an approval signed by IncidentOps for exactly these arguments.
    Idempotent: the same key returns the ticket already created."""
    arguments = {
        "title": title,
        "body": body,
        "service": service,
        "severity": severity,
        "idempotency_key": idempotency_key,
    }
    secret = os.environ.get("INCIDENTOPS_APPROVAL_SECRET")
    if not secret or approval is None or not verify(secret, approval, arguments):
        raise ToolError("approval required: no valid approval for these arguments")
    ticket, created = store().create(**arguments, approved_by=approval.reviewer)
    if delay := float(os.environ.get("INCIDENTOPS_TICKETS_FAULT_DELAY_S", "0")):
        await asyncio.sleep(delay)
    return {**ticket, "created": created}


if __name__ == "__main__":
    server.run()
