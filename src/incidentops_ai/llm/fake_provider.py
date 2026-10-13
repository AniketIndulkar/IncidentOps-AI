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


class FakeProvider:
    """Returns `replies` in order: a model instance means success, an LLMStatus means that failure."""

    def __init__(self, replies: list[BaseModel | LLMStatus]) -> None:
        self._replies = list(replies)
        self.calls: list[RecordedCall] = []

    async def generate_structured[T: BaseModel](
        self, *, prompt: Prompt, schema: type[T], config: ModelConfig
    ) -> StructuredResult[T]:
        self.calls.append(RecordedCall(prompt, schema, config))
        reply = self._replies.pop(0)
        metadata = RunMetadata(
            provider="fake",
            model=config.model,
            prompt_name=prompt.name,
            prompt_version=prompt.version,
            attempts=1,
            latency_ms=0,
        )
        if isinstance(reply, LLMStatus):
            return StructuredResult[schema](
                status=reply, error_detail="scripted", metadata=metadata
            )
        return StructuredResult[schema](status=LLMStatus.SUCCEEDED, value=reply, metadata=metadata)
