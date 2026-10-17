"""Runs a tool conversation: ask the model, run the calls it requests, send results, repeat.

Bounded by `max_rounds` so a model that keeps asking for tools cannot loop forever. Tool calls
are only run when their results can still be sent back: on the last round the calls are recorded
but not run, so a tool with side effects never runs for an answer nobody will read.

W08 adds two more limits. A call with the same tool and arguments as an earlier one is not run
again; the model is told it already has that result. A round that asks only for repeats is a loop,
and the conversation stops there (`loop_detected`). And `max_tool_calls` caps how many calls run in
total (`tool_budget`). Every ending has a `stop_reason`; `executions` is the trace.
"""

import json
from typing import Literal

from pydantic import BaseModel

from incidentops_ai.llm.provider import (
    LLMProvider,
    LLMStatus,
    ModelConfig,
    Prompt,
    RunMetadata,
    ToolCall,
    ToolOutput,
)
from incidentops_ai.tools.registry import ToolExecution, ToolRegistry, fingerprint

DEFAULT_MAX_ROUNDS = 3
DEFAULT_MAX_TOOL_CALLS = 8

StopReason = Literal[
    "answered", "model_failed", "empty_answer", "round_limit", "tool_budget", "loop_detected"
]


class ToolLoopResult(BaseModel):
    status: LLMStatus
    stop_reason: StopReason
    final_text: str | None = None
    error_detail: str | None = None
    retry_after_s: float | None = None  # provider's Retry-After hint, when the failing turn had one
    rounds: int  # model turns taken
    executions: list[ToolExecution] = []  # every requested call, run, rejected or skipped
    metadata: RunMetadata  # tokens and latency summed over all turns; see RunMetadata.combine


async def run_tool_loop(
    provider: LLMProvider,
    prompt: Prompt,
    registry: ToolRegistry,
    config: ModelConfig,
    *,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS,
) -> ToolLoopResult:
    transcript: list[dict] | None = None
    outputs: list = []
    executions: list[ToolExecution] = []
    seen: dict[str, str] = {}  # fingerprint -> call id of the first call that ran with it
    ran = 0
    total: RunMetadata | None = None

    def stop(status: LLMStatus, reason: StopReason, rounds: int, **fields) -> ToolLoopResult:
        return ToolLoopResult(
            status=status,
            stop_reason=reason,
            rounds=rounds,
            executions=executions,
            metadata=total,
            **fields,
        )

    for round_number in range(1, max_rounds + 1):
        turn = await provider.tool_call(
            prompt=prompt,
            tools=registry.specs,
            config=config,
            transcript=transcript,
            outputs=outputs,
        )
        total = turn.metadata if total is None else total.combine(turn.metadata)

        if turn.status is not LLMStatus.SUCCEEDED:
            return stop(
                turn.status,
                "model_failed",
                round_number,
                error_detail=turn.error_detail,
                retry_after_s=turn.retry_after_s,
            )
        if not turn.calls:
            if not turn.final_text or not turn.final_text.strip():
                # A "success" with nothing in it is a failed answer, and is recorded as one.
                return stop(
                    LLMStatus.INVALID_OUTPUT,
                    "empty_answer",
                    round_number,
                    error_detail="empty final answer",
                )
            return stop(LLMStatus.SUCCEEDED, "answered", round_number, final_text=turn.final_text)
        if round_number == max_rounds:
            executions += [_skipped(call, "round limit reached") for call in turn.calls]
            return stop(
                LLMStatus.INVALID_OUTPUT,
                "round_limit",
                max_rounds,
                error_detail=f"no final answer after {max_rounds} tool rounds",
            )

        fresh, repeats = [], {}
        for call in turn.calls:
            key = fingerprint(call)
            if key in seen:
                repeats[call.call_id] = (key, seen[key])
            else:
                seen[key] = call.call_id
                fresh.append((call, key))
        if not fresh:  # nothing new was asked for: the model is going round in circles
            executions += [
                _skipped(call, "duplicate call", *repeats[call.call_id]) for call in turn.calls
            ]
            return stop(
                LLMStatus.INVALID_OUTPUT,
                "loop_detected",
                round_number,
                error_detail="the model repeated tool calls it already had the results of",
            )
        allowed = fresh[: max(0, max_tool_calls - ran)]
        over = fresh[len(allowed) :]

        results = await registry.run_all([call for call, _ in allowed])  # concurrently
        ran += len(allowed)
        by_id = {
            execution.call_id: (execution.model_copy(update={"fingerprint": p}), output)
            for (execution, output), (_, p) in zip(results, allowed, strict=True)
        }
        outputs = []
        for call in turn.calls:  # the model gets one answer per call, in the order it asked
            if call.call_id in by_id:
                execution, output = by_id[call.call_id]
            elif call.call_id in repeats:
                execution = _skipped(call, "duplicate call", *repeats[call.call_id])
                output = _refusal(call, "duplicate call", previous_call_id=execution.duplicate_of)
            else:
                execution = _skipped(call, "tool budget exhausted", fingerprint(call))
                output = _refusal(call, "tool budget exhausted")
            executions.append(execution)
            outputs.append(output)
        if over:
            return stop(
                LLMStatus.INVALID_OUTPUT,
                "tool_budget",
                round_number,
                error_detail=f"more than {max_tool_calls} tool calls asked for",
            )
        transcript = turn.transcript

    raise AssertionError("unreachable: the last round always returns")  # pragma: no cover


def _skipped(
    call: ToolCall, reason: str, key: str | None = None, duplicate_of: str | None = None
) -> ToolExecution:
    return ToolExecution(
        call_id=call.call_id,
        name=call.name,
        executed=False,
        ok=False,
        error=reason,
        fingerprint=key,
        duplicate_of=duplicate_of,
    )


def _refusal(call: ToolCall, reason: str, **details) -> ToolOutput:
    return ToolOutput(
        call_id=call.call_id, output=json.dumps({"error": {"reason": reason, **details}})
    )
