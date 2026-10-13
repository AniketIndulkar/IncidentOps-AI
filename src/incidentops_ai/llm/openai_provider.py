"""OpenAI implementation of LLMProvider. The only module that imports `openai`.

One attempt per call: SDK retries are switched off so that retrying is explicit and countable
in our own layer (see retry.RetryingProvider). Every vendor exception is translated to an
LLMStatus; nothing here raises for a provider failure.
"""

import json
import time
from collections.abc import Sequence
from dataclasses import dataclass

import httpx2
import openai
from openai.types.responses import Response
from pydantic import BaseModel, ValidationError

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

_REJECTED = (
    openai.BadRequestError,
    openai.AuthenticationError,
    openai.PermissionDeniedError,
    openai.NotFoundError,
    openai.UnprocessableEntityError,
)


@dataclass(frozen=True)
class _Outcome:
    """Result of one HTTP attempt. `status` is None when the response is usable. A failure
    may still carry the response (truncated, refused), so its token usage is not lost."""

    response: Response | None = None
    status: LLMStatus | None = None
    detail: str | None = None
    retry_after_s: float | None = None


class OpenAIProvider:
    def __init__(
        self,
        api_key: str,
        *,
        project: str | None = None,
        http_client: httpx2.AsyncClient | None = None,
    ) -> None:
        self._client = openai.AsyncOpenAI(
            api_key=api_key, project=project, max_retries=0, http_client=http_client
        )

    async def generate_structured[T: BaseModel](
        self, *, prompt: Prompt, schema: type[T], config: ModelConfig
    ) -> StructuredResult[T]:
        started = time.perf_counter()
        # responses.create, not responses.parse: parse() validates the reply before we can
        # look at response.status, so a reply cut off at the token cap would surface as a
        # validation error with no usage or request id. We validate ourselves, below.
        outcome = await self._request(
            config,
            instructions=prompt.instructions,
            input=prompt.input,
            text={"format": _strict_format(schema)},
        )
        response = outcome.response

        def result(status: LLMStatus, *, value: T | None = None, detail: str | None = None):
            return StructuredResult[schema](
                status=status,
                value=value,
                error_detail=detail,
                retry_after_s=outcome.retry_after_s,
                metadata=_metadata(prompt, config, started, status, response),
            )

        if outcome.status is not None:
            return result(outcome.status, detail=outcome.detail)
        try:
            # strict=True: no silent conversions ("true" -> True, 42 -> "42"). A reply with the
            # wrong type is a failed reply, not something to quietly repair.
            value = schema.model_validate_json(response.output_text, strict=True)
        except ValidationError:
            # Detail is deliberately generic: the validation message would echo model output,
            # which may contain incident text.
            return result(LLMStatus.INVALID_OUTPUT, detail="reply did not match the schema")
        return result(LLMStatus.SUCCEEDED, value=value)

    async def tool_call(
        self,
        *,
        prompt: Prompt,
        tools: Sequence[ToolSpec],
        config: ModelConfig,
        transcript: list[dict] | None = None,
        outputs: Sequence[ToolOutput] = (),
    ) -> ToolTurn:
        started = time.perf_counter()
        items = list(transcript) if transcript else [{"role": "user", "content": prompt.input}]
        items += [
            {"type": "function_call_output", "call_id": o.call_id, "output": o.output}
            for o in outputs
        ]
        outcome = await self._request(
            config,
            instructions=prompt.instructions,
            input=items,
            tools=[
                {
                    "type": "function",
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": _strict_schema(tool.arguments),
                    "strict": True,
                }
                for tool in tools
            ],
            parallel_tool_calls=True,
            # We do not store conversations provider-side, so the model's reasoning must travel
            # with the transcript for the next turn to continue from it.
            include=["reasoning.encrypted_content"],
        )
        response = outcome.response
        if outcome.status is not None:
            return ToolTurn(
                status=outcome.status,
                transcript=items,
                error_detail=outcome.detail,
                retry_after_s=outcome.retry_after_s,
                metadata=_metadata(prompt, config, started, outcome.status, response),
            )

        calls = [
            ToolCall(call_id=item.call_id, name=item.name, arguments_json=item.arguments)
            for item in response.output
            if item.type == "function_call"
        ]
        return ToolTurn(
            status=LLMStatus.SUCCEEDED,
            calls=calls,
            final_text=None if calls else response.output_text,
            transcript=items + [_as_input_item(item) for item in response.output],
            metadata=_metadata(prompt, config, started, LLMStatus.SUCCEEDED, response),
        )

    async def _request(self, config: ModelConfig, **params) -> _Outcome:
        """One HTTP attempt. Returns a completed, non-refused response or a failure status."""
        if config.reasoning_effort:
            params["reasoning"] = {"effort": config.reasoning_effort}
        try:
            response = await self._client.responses.create(
                model=config.model,
                max_output_tokens=config.max_output_tokens,
                store=False,  # incident text may be sensitive; don't keep it provider-side
                timeout=config.timeout_s,
                **params,
            )
        # Order matters: APITimeoutError is a subclass of APIConnectionError.
        except openai.APITimeoutError:
            return _Outcome(status=LLMStatus.TIMED_OUT, detail="request timed out")
        except openai.APIConnectionError:
            return _Outcome(status=LLMStatus.PROVIDER_ERROR, detail="connection error")
        except openai.RateLimitError as exc:
            if getattr(exc, "code", None) == "insufficient_quota":
                return _Outcome(status=LLMStatus.REJECTED, detail="quota exhausted")
            return _Outcome(
                status=LLMStatus.RATE_LIMITED,
                detail="rate limited",
                retry_after_s=_retry_after(exc),
            )
        except _REJECTED as exc:
            return _Outcome(
                status=LLMStatus.REJECTED, detail=f"request rejected (HTTP {exc.status_code})"
            )
        except openai.APIStatusError as exc:
            status = LLMStatus.PROVIDER_ERROR if exc.status_code >= 500 else LLMStatus.REJECTED
            return _Outcome(status=status, detail=f"HTTP {exc.status_code}")
        except (openai.OpenAIError, json.JSONDecodeError):
            # Includes a 200 whose body is not valid JSON: the SDK raises a bare JSONDecodeError.
            return _Outcome(status=LLMStatus.PROVIDER_ERROR, detail="unreadable provider response")

        if not isinstance(response, Response):
            # A 200 with a non-JSON body (e.g. a proxy's HTML error page) comes back from the SDK
            # as a plain string instead of raising.
            return _Outcome(status=LLMStatus.PROVIDER_ERROR, detail="unreadable provider response")
        if response.status == "incomplete":
            reason = getattr(response.incomplete_details, "reason", None)
            if reason == "max_output_tokens":
                return _Outcome(response, LLMStatus.TRUNCATED, "output hit the token cap")
            if reason == "content_filter":
                return _Outcome(response, LLMStatus.REFUSED, "content filter")
            return _Outcome(response, LLMStatus.INVALID_OUTPUT, "incomplete response")
        if _refused(response):
            return _Outcome(response, LLMStatus.REFUSED, "model refused")
        return _Outcome(response=response)


def _metadata(
    prompt: Prompt,
    config: ModelConfig,
    started: float,
    status: LLMStatus,
    response: Response | None,
) -> RunMetadata:
    usage = getattr(response, "usage", None)
    return RunMetadata(
        provider="openai",
        model=getattr(response, "model", None) or config.model,
        prompt_name=prompt.name,
        prompt_version=prompt.version,
        attempts=1,
        latency_ms=int((time.perf_counter() - started) * 1000),
        input_tokens=getattr(usage, "input_tokens", None),
        output_tokens=getattr(usage, "output_tokens", None),
        request_id=getattr(response, "_request_id", None),
        attempt_statuses=[status],
    )


def _strict_schema(schema: type[BaseModel]) -> dict:
    """JSON Schema for strict mode.

    Strict mode needs every object to list all its properties as required and to forbid extras.
    We check that here so a badly declared schema fails loudly in tests, not as an HTTP 400.
    """
    json_schema = schema.model_json_schema()
    for definition in [json_schema, *json_schema.get("$defs", {}).values()]:
        if definition.get("type") != "object":
            continue
        properties = set(definition.get("properties", {}))
        if set(definition.get("required", [])) != properties:
            raise TypeError(f"{schema.__name__}: every field must be required (use `X | None`)")
        if definition.get("additionalProperties") is not False:
            raise TypeError(f"{schema.__name__}: models must set extra='forbid'")
    return json_schema


def _strict_format(schema: type[BaseModel]) -> dict:
    return {
        "type": "json_schema",
        "name": schema.__name__,
        "schema": _strict_schema(schema),
        "strict": True,
    }


def _as_input_item(item) -> dict:
    """An output item in the shape the API accepts back as input on the next turn."""
    if item.type == "function_call":
        return {
            "type": "function_call",
            "call_id": item.call_id,
            "name": item.name,
            "arguments": item.arguments,
        }
    if item.type == "reasoning":
        return {
            "type": "reasoning",
            "id": item.id,
            "summary": [part.model_dump() for part in item.summary],
            "encrypted_content": item.encrypted_content,
        }
    if item.type == "message":
        text = "".join(part.text for part in item.content if part.type == "output_text")
        return {"role": "assistant", "content": text}
    return item.model_dump(exclude_none=True)


def _retry_after(exc: openai.APIStatusError) -> float | None:
    """Retry-After in seconds, when the provider sent a plain number."""
    try:
        return float(exc.response.headers["retry-after"])
    except (KeyError, ValueError):
        return None


def _refused(response: Response) -> bool:
    return any(
        getattr(part, "type", None) == "refusal"
        for item in response.output
        if getattr(item, "type", None) == "message"
        for part in item.content
    )
