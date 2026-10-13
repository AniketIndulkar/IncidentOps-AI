"""Scripted provider for tests and CI: no network, no API key, no cost."""

from dataclasses import dataclass

from pydantic import BaseModel

from incidentops_ai.llm.provider import (
    LLMStatus,
    ModelConfig,
    Prompt,
    RunMetadata,
    StructuredResult,
    ToolCall,
    ToolOutput,
    ToolSpec,
    ToolTurn,
)


@dataclass(frozen=True)
class RecordedCall:
    prompt: Prompt
    schema: type[BaseModel]
    config: ModelConfig


@dataclass(frozen=True)
class Fail:
    """A fully scripted reply, for when a test needs more than a bare status."""

    status: LLMStatus
    retry_after_s: float | None = None
    value: BaseModel | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int = 0


@dataclass(frozen=True)
class RecordedToolTurn:
    prompt: Prompt
    tools: tuple[ToolSpec, ...]
    config: ModelConfig
    outputs: tuple[ToolOutput, ...]


class FakeProvider:
    """Returns `replies` in order: a model instance means success, an LLMStatus means that
    failure, a `Fail` gives full control.

    `tool_turns` scripts tool conversations the same way: a list of ToolCall means "the model
    asks for these calls", a str means "the model gives this final answer", an LLMStatus means
    that failure.
    """

    def __init__(
        self,
        replies: list[BaseModel | LLMStatus | Fail] | None = None,
        *,
        tool_turns: list[list[ToolCall] | str | LLMStatus] | None = None,
    ) -> None:
        self._replies = list(replies or [])
        self._tool_turns = list(tool_turns or [])
        self.calls: list[RecordedCall] = []
        self.tool_calls: list[RecordedToolTurn] = []

    async def generate_structured[T: BaseModel](
        self, *, prompt: Prompt, schema: type[T], config: ModelConfig
    ) -> StructuredResult[T]:
        self.calls.append(RecordedCall(prompt, schema, config))
        reply = self._replies.pop(0)
        if isinstance(reply, LLMStatus):
            reply = Fail(reply)
        elif not isinstance(reply, Fail):
            reply = Fail(LLMStatus.SUCCEEDED, value=reply)
        return StructuredResult[schema](
            status=reply.status,
            value=reply.value,
            error_detail=None if reply.status is LLMStatus.SUCCEEDED else "scripted",
            retry_after_s=reply.retry_after_s,
            metadata=RunMetadata(
                provider="fake",
                model=config.model,
                prompt_name=prompt.name,
                prompt_version=prompt.version,
                attempts=1,
                latency_ms=reply.latency_ms,
                input_tokens=reply.input_tokens,
                output_tokens=reply.output_tokens,
                attempt_statuses=[reply.status],
            ),
        )

    async def tool_call(
        self,
        *,
        prompt: Prompt,
        tools,
        config: ModelConfig,
        transcript: list[dict] | None = None,
        outputs=(),
    ) -> ToolTurn:
        self.tool_calls.append(RecordedToolTurn(prompt, tuple(tools), config, tuple(outputs)))
        scripted = self._tool_turns.pop(0)
        history = list(transcript or []) + [o.model_dump() for o in outputs]
        status = scripted if isinstance(scripted, LLMStatus) else LLMStatus.SUCCEEDED
        return ToolTurn(
            status=status,
            calls=scripted if isinstance(scripted, list) else [],
            final_text=scripted if isinstance(scripted, str) else None,
            transcript=[*history, {"turn": len(self.tool_calls)}],
            error_detail="scripted" if isinstance(scripted, LLMStatus) else None,
            metadata=RunMetadata(
                provider="fake",
                model=config.model,
                prompt_name=prompt.name,
                prompt_version=prompt.version,
                attempts=1,
                latency_ms=0,
                input_tokens=10,
                output_tokens=5,
                attempt_statuses=[status],
            ),
        )
