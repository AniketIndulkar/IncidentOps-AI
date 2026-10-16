"""Runs a tool conversation: ask the model, run the calls it requests, send results, repeat.

Bounded by `max_rounds` so a model that keeps asking for tools cannot loop forever. Tool calls
are only run when their results can still be sent back: on the last round the calls are recorded
but not run, so a tool with side effects never runs for an answer nobody will read.
"""

from pydantic import BaseModel

from incidentops_ai.llm.provider import (
    LLMProvider,
    LLMStatus,
    ModelConfig,
    Prompt,
    RunMetadata,
    ToolCall,
)
from incidentops_ai.tools.registry import ToolExecution, ToolRegistry

DEFAULT_MAX_ROUNDS = 3


class ToolLoopResult(BaseModel):
    status: LLMStatus
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
) -> ToolLoopResult:
    transcript: list[dict] | None = None
    outputs: list = []
    executions: list[ToolExecution] = []
    total: RunMetadata | None = None

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
            return ToolLoopResult(
                status=turn.status,
                error_detail=turn.error_detail,
                retry_after_s=turn.retry_after_s,
                rounds=round_number,
                executions=executions,
                metadata=total,
            )
        if not turn.calls:
            if not turn.final_text or not turn.final_text.strip():
                # A "success" with nothing in it is a failed answer, and is recorded as one.
                return ToolLoopResult(
                    status=LLMStatus.INVALID_OUTPUT,
                    error_detail="empty final answer",
                    rounds=round_number,
                    executions=executions,
                    metadata=total,
                )
            return ToolLoopResult(
                status=LLMStatus.SUCCEEDED,
                final_text=turn.final_text,
                rounds=round_number,
                executions=executions,
                metadata=total,
            )
        if round_number == max_rounds:
            executions += [_skipped(call) for call in turn.calls]
            break

        results = await registry.run_all(turn.calls)  # all calls of this turn run concurrently
        executions += [execution for execution, _ in results]
        outputs = [output for _, output in results]
        transcript = turn.transcript

    return ToolLoopResult(
        status=LLMStatus.INVALID_OUTPUT,
        error_detail=f"no final answer after {max_rounds} tool rounds",
        rounds=max_rounds,
        executions=executions,
        metadata=total,
    )


def _skipped(call: ToolCall) -> ToolExecution:
    return ToolExecution(
        call_id=call.call_id, name=call.name, executed=False, ok=False, error="round limit reached"
    )
