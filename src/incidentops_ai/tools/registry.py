"""Tools the model may ask for, and the gate every request passes before anything runs.

The model's tool call is untrusted input, exactly like an HTTP request body:
1. the tool name must be one we registered
2. the arguments must be valid JSON matching the tool's Pydantic model, in strict mode
Only then is the handler called. A rejected call is never executed; the model gets an error
result it can read and correct.
"""

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from incidentops_ai.llm.provider import ToolCall, ToolOutput, ToolSpec

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Tool:
    spec: ToolSpec
    handler: Callable[[Any], Awaitable[Any]]  # receives the validated arguments model


class ToolExecution(BaseModel):
    """Audit record of one requested call: what was asked, whether it ran, how it ended."""

    call_id: str
    name: str
    executed: bool  # False when the call was rejected before running
    ok: bool
    error: str | None = None
    arguments: dict | None = None  # validated arguments; None when rejected
    duration_ms: int = 0


class ToolRegistry:
    def __init__(self, tools: Sequence[Tool]) -> None:
        self._tools = {tool.spec.name: tool for tool in tools}

    @property
    def specs(self) -> list[ToolSpec]:
        return [tool.spec for tool in self._tools.values()]

    async def run_all(self, calls: Sequence[ToolCall]) -> list[tuple[ToolExecution, ToolOutput]]:
        """Run every call concurrently. Results come back in the order the calls were given."""
        return list(await asyncio.gather(*(self._run(call) for call in calls)))

    async def _run(self, call: ToolCall) -> tuple[ToolExecution, ToolOutput]:
        def rejected(reason: str, details: list[dict] | None = None):
            body = {"error": {"reason": reason, **({"details": details} if details else {})}}
            return (
                ToolExecution(
                    call_id=call.call_id, name=call.name, executed=False, ok=False, error=reason
                ),
                ToolOutput(call_id=call.call_id, output=json.dumps(body)),
            )

        tool = self._tools.get(call.name)
        if tool is None:
            return rejected("unknown tool")
        try:
            arguments = tool.spec.arguments.model_validate_json(call.arguments_json, strict=True)
        except ValidationError as exc:
            # Field paths and error types only: never echo the submitted values.
            details = [
                {"field": ".".join(str(part) for part in err["loc"]), "problem": err["type"]}
                for err in exc.errors(include_input=False, include_url=False)
            ]
            return rejected("invalid arguments", details)

        started = time.perf_counter()
        try:
            value = await tool.handler(arguments)
        except Exception:
            logger.exception("Tool %s failed", call.name)
            execution = ToolExecution(
                call_id=call.call_id,
                name=call.name,
                executed=True,
                ok=False,
                error="tool failed",
                arguments=arguments.model_dump(mode="json"),
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
            body = {"error": {"reason": "tool failed"}}
            return execution, ToolOutput(call_id=call.call_id, output=json.dumps(body))

        execution = ToolExecution(
            call_id=call.call_id,
            name=call.name,
            executed=True,
            ok=True,
            arguments=arguments.model_dump(mode="json"),
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
        return execution, ToolOutput(
            call_id=call.call_id, output=json.dumps({"result": value}, default=str)
        )
