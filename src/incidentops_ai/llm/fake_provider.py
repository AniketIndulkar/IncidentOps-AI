"""Scripted provider for tests and CI: no network, no API key, no cost."""

from dataclasses import dataclass

from pydantic import BaseModel

from incidentops_ai.llm.provider import (
    LLMStatus,
    ModelConfig,
    Prompt,
    RunMetadata,
    StructuredResult,
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


class FakeProvider:
    """Returns `replies` in order: a model instance means success, an LLMStatus means that
    failure, a `Fail` gives full control."""

    def __init__(self, replies: list[BaseModel | LLMStatus | Fail]) -> None:
        self._replies = list(replies)
        self.calls: list[RecordedCall] = []

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
