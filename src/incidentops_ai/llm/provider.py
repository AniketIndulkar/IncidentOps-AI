"""The LLM interface the rest of the app depends on. No vendor SDK is imported here.

Rule: a result is a value, not an exception. Callers check `status`; a failed call carries
`value=None` and never partly filled or guessed data.
"""

from collections.abc import Sequence
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict


class LLMStatus(StrEnum):
    SUCCEEDED = "succeeded"
    INVALID_OUTPUT = "invalid_output"  # reply did not match the schema or our validators
    TRUNCATED = "truncated"  # hit max_output_tokens
    REFUSED = "refused"  # model declined, or content filter
    TIMED_OUT = "timed_out"
    RATE_LIMITED = "rate_limited"
    PROVIDER_ERROR = "provider_error"  # 5xx or network
    REJECTED = "rejected"  # our request was wrong (400/401/403/404, no quota); never retried


class ModelConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    model: str
    max_output_tokens: int = 2000
    timeout_s: float = 30.0  # per attempt
    max_attempts: int = 3  # total tries including the first (used from AIENG-21)
    reasoning_effort: str | None = None


class Prompt(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    version: str
    instructions: str
    input: str


class RunMetadata(BaseModel):
    provider: str
    model: str  # the model that actually answered, when known
    prompt_name: str
    prompt_version: str
    attempts: int
    latency_ms: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    request_id: str | None = None


class StructuredResult[T: BaseModel](BaseModel):
    status: LLMStatus
    value: T | None = None  # set only when status is SUCCEEDED
    error_detail: str | None = None  # short category text; never model or user content
    metadata: RunMetadata


class LLMProvider(Protocol):
    async def generate_structured[T: BaseModel](
        self, *, prompt: Prompt, schema: type[T], config: ModelConfig
    ) -> StructuredResult[T]: ...


__all__: Sequence[str] = (
    "LLMProvider",
    "LLMStatus",
    "ModelConfig",
    "Prompt",
    "RunMetadata",
    "StructuredResult",
)
