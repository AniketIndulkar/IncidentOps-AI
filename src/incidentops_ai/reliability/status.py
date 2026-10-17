"""Which degraded mode is IncidentOps in, and why (AIENG-72; docs/runbook.md).

| Tier | When |
|---|---|
| FULL | everything healthy |
| REDUCED | an optional integration (graph, CMDB, ticket system) is degraded: runs still complete,
|  | and go to review with a named reason |
| MANUAL | the database is down, no model route is usable, or triage is not configured:
|  | operators work incidents by hand |

Every check is cheap and bounded (2 s): a database ping, the gateway's breakers, one graph lookup,
one CMDB lookup, one ticket search. No model call. Details never include connection strings.
"""

import asyncio
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import text

State = Literal["ok", "degraded", "down", "off"]
CHECK_TIMEOUT_S = 2.0


class Dependency(BaseModel):
    state: State
    detail: str = ""


class Status(BaseModel):
    tier: Literal["FULL", "REDUCED", "MANUAL"]
    dependencies: dict[str, Dependency]
    checked_at: datetime


async def _bounded(check) -> Dependency:
    try:
        async with asyncio.timeout(CHECK_TIMEOUT_S):
            return await check()
    except TimeoutError:
        return Dependency(state="degraded", detail="no answer within 2 s")
    except Exception as error:  # noqa: BLE001  (a check must never break the status page)
        return Dependency(state="degraded", detail=type(error).__name__)


async def check(app_state, *, tenant_id: str) -> Status:
    async def database() -> Dependency:
        try:
            async with app_state.session_factory() as session:
                await session.execute(text("SELECT 1"))
        except Exception as error:  # noqa: BLE001  (any failure to answer means down)
            return Dependency(state="down", detail=type(error).__name__)
        return Dependency(state="ok")

    async def model() -> Dependency:
        gateway = getattr(app_state, "gateway", None)
        if gateway is None or app_state.llm_config is None:
            return Dependency(state="off", detail="no model configured")
        models = gateway.models(app_state.llm_config.model)
        open_ = [m for m in models if m in gateway.open_models()]
        if len(open_) == len(models):
            return Dependency(
                state="down", detail=f"every route's breaker is open: {', '.join(open_)}"
            )
        if open_:
            return Dependency(state="degraded", detail=f"breaker open: {', '.join(open_)}")
        return Dependency(state="ok")

    async def graph() -> Dependency:
        store = app_state.graph_store
        if store is None:
            return Dependency(state="off")
        await store.service_context("health-check", tenant=tenant_id)
        return Dependency(state="ok")

    runner = app_state.workflow_runner

    async def cmdb() -> Dependency:
        client = runner.deps.cmdb if runner else None
        if client is None:
            return Dependency(state="off")
        lookup = await client.service_record("health-check")
        if lookup.status in ("ok", "unknown_service"):
            return Dependency(state="ok")
        return Dependency(state="degraded", detail=lookup.status)

    async def tickets() -> Dependency:
        sink = runner.deps.tickets if runner else None
        if sink is None:
            return Dependency(state="off")
        probe = getattr(sink, "probe", None)
        if probe is not None:
            await probe()
        return Dependency(state="ok")

    names = ("database", "model", "graph", "cmdb", "tickets")
    results = await asyncio.gather(*(_bounded(c) for c in (database, model, graph, cmdb, tickets)))
    deps = dict(zip(names, results, strict=True))
    deps["retrieval"] = Dependency(
        state="ok" if app_state.retriever is not None else "off",
        detail="" if app_state.retriever is not None else "no retrieval configured",
    )
    deps["triage"] = Dependency(
        state="ok" if runner is not None else "off",
        detail="" if runner is not None else "needs a model and retrieval",
    )
    if (
        deps["database"].state == "down"
        or deps["model"].state in ("down", "off")
        or deps["triage"].state == "off"
    ):
        tier = "MANUAL"
    elif any(deps[n].state == "degraded" for n in ("graph", "cmdb", "tickets", "model")):
        tier = "REDUCED"
    else:
        tier = "FULL"
    return Status(tier=tier, dependencies=deps, checked_at=datetime.now(UTC))
