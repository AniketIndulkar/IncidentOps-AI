"""OpenAI implementation of LLMProvider. The only module that imports `openai`.

One attempt per call for now: SDK retries are switched off so that retrying is explicit and
countable in our own layer (AIENG-21). Every vendor exception is translated to an LLMStatus.
"""

import time

import httpx2
import openai
from pydantic import BaseModel, ValidationError

from incidentops_ai.llm.provider import (
    LLMStatus,
    ModelConfig,
    Prompt,
    RunMetadata,
    StructuredResult,
)

_REJECTED = (
    openai.BadRequestError,
    openai.AuthenticationError,
    openai.PermissionDeniedError,
    openai.NotFoundError,
    openai.UnprocessableEntityError,
)


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

        def result(
            status: LLMStatus, *, value: T | None = None, detail: str | None = None, response=None
        ) -> StructuredResult[T]:
            usage = getattr(response, "usage", None)
            return StructuredResult[schema](
                status=status,
                value=value,
                error_detail=detail,
                metadata=RunMetadata(
                    provider="openai",
                    model=getattr(response, "model", None) or config.model,
                    prompt_name=prompt.name,
                    prompt_version=prompt.version,
                    attempts=1,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    input_tokens=getattr(usage, "input_tokens", None),
                    output_tokens=getattr(usage, "output_tokens", None),
                    request_id=getattr(response, "_request_id", None),
                ),
            )

        extra = (
            {"reasoning": {"effort": config.reasoning_effort}} if config.reasoning_effort else {}
        )
        try:
            # responses.create, not responses.parse: parse() validates the reply before we can
            # look at response.status, so a reply cut off at the token cap would surface as a
            # validation error with no usage or request id. We validate ourselves, below.
            response = await self._client.responses.create(
                model=config.model,
                instructions=prompt.instructions,
                input=prompt.input,
                text={"format": _strict_format(schema)},
                max_output_tokens=config.max_output_tokens,
                store=False,  # incident text may be sensitive; don't keep it provider-side
                timeout=config.timeout_s,
                **extra,
            )
        # Order matters: APITimeoutError is a subclass of APIConnectionError.
        except openai.APITimeoutError:
            return result(LLMStatus.TIMED_OUT, detail="request timed out")
        except openai.APIConnectionError:
            return result(LLMStatus.PROVIDER_ERROR, detail="connection error")
        except openai.RateLimitError as exc:
            if getattr(exc, "code", None) == "insufficient_quota":
                return result(LLMStatus.REJECTED, detail="quota exhausted")
            return result(LLMStatus.RATE_LIMITED, detail="rate limited")
        except _REJECTED as exc:
            return result(LLMStatus.REJECTED, detail=f"request rejected (HTTP {exc.status_code})")
        except openai.APIStatusError as exc:
            status = LLMStatus.PROVIDER_ERROR if exc.status_code >= 500 else LLMStatus.REJECTED
            return result(status, detail=f"HTTP {exc.status_code}")

        if response.status == "incomplete":
            reason = getattr(response.incomplete_details, "reason", None)
            if reason == "max_output_tokens":
                return result(
                    LLMStatus.TRUNCATED, detail="output hit the token cap", response=response
                )
            if reason == "content_filter":
                return result(LLMStatus.REFUSED, detail="content filter", response=response)
            return result(LLMStatus.INVALID_OUTPUT, detail="incomplete response", response=response)

        if _refused(response):
            return result(LLMStatus.REFUSED, detail="model refused", response=response)

        try:
            value = schema.model_validate_json(response.output_text)
        except ValidationError:
            # Detail is deliberately generic: the validation message would echo model output,
            # which may contain incident text.
            return result(
                LLMStatus.INVALID_OUTPUT, detail="reply did not match the schema", response=response
            )
        return result(LLMStatus.SUCCEEDED, value=value, response=response)


def _strict_format(schema: type[BaseModel]) -> dict:
    """JSON Schema response format with strict mode on.

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
    return {"type": "json_schema", "name": schema.__name__, "schema": json_schema, "strict": True}


def _refused(response) -> bool:
    return any(
        getattr(part, "type", None) == "refusal"
        for item in response.output
        if getattr(item, "type", None) == "message"
        for part in item.content
    )
