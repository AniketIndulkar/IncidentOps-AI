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
    max_attempts: int = 3  # total tries including the first
    total_timeout_s: float = 60.0  # hard cap on one call across all attempts and waits
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
    # Tries the hardest single model call needed, first one included (1 = no retry). Never above
    # ModelConfig.max_attempts, however many model turns a tool conversation took.
    attempts: int
    latency_ms: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    request_id: str | None = None
    attempt_statuses: list[LLMStatus] = []  # one entry per request sent, in order

    def combine(self, later: "RunMetadata") -> "RunMetadata":
        """Totals for this call followed by `later`. The only place cost is added up, so a new
        field cannot be summed in one caller and forgotten in another. Names come from `later`
        (its model and request id are the most recent); `attempts` is the larger of the two."""
        return later.model_copy(
            update={
                "attempts": max(self.attempts, later.attempts),
                "latency_ms": self.latency_ms + later.latency_ms,
                "input_tokens": _add(self.input_tokens, later.input_tokens),
                "output_tokens": _add(self.output_tokens, later.output_tokens),
                "attempt_statuses": [*self.attempt_statuses, *later.attempt_statuses],
            }
        )


def _add(a: int | None, b: int | None) -> int | None:
    """Sum of two optional counts; None only when neither is known."""
    return None if a is None and b is None else (a or 0) + (b or 0)


class StructuredResult[T: BaseModel](BaseModel):
    status: LLMStatus
    value: T | None = None  # set only when status is SUCCEEDED
    error_detail: str | None = None  # short category text; never model or user content
    retry_after_s: float | None = None  # provider's Retry-After hint, when it sent one
    metadata: RunMetadata


class ToolSpec(BaseModel):
    """A function the model may ask us to run. `arguments` is the Pydantic model of its inputs."""

    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    arguments: type[BaseModel]


class ToolCall(BaseModel):
    """What the model asked for. Arguments are raw JSON text: not yet validated, not yet trusted."""

    call_id: str
    name: str
    arguments_json: str


class ToolOutput(BaseModel):
    call_id: str
    output: str  # JSON text sent back to the model


class ToolTurn(BaseModel):
    """One model turn in a tool conversation: either tool calls to run, or a final answer."""

    status: LLMStatus
    calls: list[ToolCall] = []
    final_text: str | None = None
    # Provider-specific conversation so far. Opaque to callers: pass it back unchanged.
    transcript: list[dict] = []
    error_detail: str | None = None
    retry_after_s: float | None = None
    metadata: RunMetadata


class LLMProvider(Protocol):
    async def generate_structured[T: BaseModel](
        self, *, prompt: Prompt, schema: type[T], config: ModelConfig
    ) -> StructuredResult[T]: ...

    async def tool_call(
        self,
        *,
        prompt: Prompt,
        tools: Sequence[ToolSpec],
        config: ModelConfig,
        transcript: list[dict] | None = None,
        outputs: Sequence[ToolOutput] = (),
    ) -> ToolTurn:
        """Ask the model to continue. First call: no transcript. Later calls: the previous
        turn's transcript plus the outputs of the tool calls it asked for."""
        ...

    async def aclose(self) -> None:
        """Release connections. Call once, when the provider will not be used again."""
        ...


__all__: Sequence[str] = (
    "LLMProvider",
    "LLMStatus",
    "ModelConfig",
    "Prompt",
    "RunMetadata",
    "StructuredResult",
    "ToolCall",
    "ToolOutput",
    "ToolSpec",
    "ToolTurn",
)
