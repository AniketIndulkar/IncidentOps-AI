"""Runs a tool conversation: ask the model, run the calls it requests, send results, repeat.

Bounded by `max_rounds` so a model that keeps asking for tools cannot loop forever.
"""

from pydantic import BaseModel

from incidentops_ai.llm.provider import LLMProvider, LLMStatus, ModelConfig, Prompt, RunMetadata
from incidentops_ai.tools.registry import ToolExecution, ToolRegistry

DEFAULT_MAX_ROUNDS = 3


class ToolLoopResult(BaseModel):
    status: LLMStatus
    final_text: str | None = None
    error_detail: str | None = None
    rounds: int  # model turns taken
    executions: list[ToolExecution] = []  # every requested call, run or rejected, in order
    metadata: RunMetadata  # tokens, latency and attempts summed over all turns


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
        total = _combine(total, turn.metadata)

        if turn.status is not LLMStatus.SUCCEEDED:
            return ToolLoopResult(
                status=turn.status,
                error_detail=turn.error_detail,
                rounds=round_number,
                executions=executions,
                metadata=total,
            )
        if not turn.calls:
            return ToolLoopResult(
                status=LLMStatus.SUCCEEDED,
                final_text=turn.final_text,
                rounds=round_number,
                executions=executions,
                metadata=total,
            )

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


def _combine(total: RunMetadata | None, turn: RunMetadata) -> RunMetadata:
    if total is None:
        return turn

    def add(a: int | None, b: int | None) -> int | None:
        return None if a is None and b is None else (a or 0) + (b or 0)

    return turn.model_copy(
        update={
            "attempts": total.attempts + turn.attempts,
            "attempt_statuses": total.attempt_statuses + turn.attempt_statuses,
            "latency_ms": total.latency_ms + turn.latency_ms,
            "input_tokens": add(total.input_tokens, turn.input_tokens),
            "output_tokens": add(total.output_tokens, turn.output_tokens),
        }
    )
