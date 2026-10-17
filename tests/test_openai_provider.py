"""OpenAIProvider against a mocked HTTP layer: real SDK code runs, no network, no API key."""

import json

import httpx2
import pytest
from pydantic import BaseModel, ConfigDict

from incidentops_ai.llm.openai_provider import OpenAIProvider, _strict_schema
from incidentops_ai.llm.provider import LLMStatus, ModelConfig, Prompt


class Ticket(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service: str
    urgent: bool


PROMPT = Prompt(name="demo", version="v1", instructions="Extract a ticket.", input="pay is down!")
CONFIG = ModelConfig(model="test-model", max_output_tokens=500)


def response_body(content: list[dict], status: str = "completed", incomplete: dict | None = None):
    return {
        "id": "resp_123",
        "object": "response",
        "created_at": 1790000000,
        "model": "test-model-2026-01-01",
        "status": status,
        "incomplete_details": incomplete,
        "output": [
            {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": content,
            }
        ],
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        "usage": {
            "input_tokens": 42,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens": 7,
            "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": 49,
        },
    }


def text_part(text: str) -> dict:
    return {"type": "output_text", "text": text, "annotations": []}


def provider_with(handler) -> tuple[OpenAIProvider, list[httpx2.Request]]:
    seen: list[httpx2.Request] = []

    def record(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return handler(request)

    http_client = httpx2.AsyncClient(transport=httpx2.MockTransport(record))
    return OpenAIProvider(api_key="test-key", http_client=http_client), seen


def ok(body: dict, status_code: int = 200) -> httpx2.Response:
    return httpx2.Response(status_code, json=body, headers={"x-request-id": "req_abc"})


def test_json_schema_is_built_and_checked_once_per_model():
    assert _strict_schema(Ticket) is _strict_schema(Ticket)


async def test_closing_the_provider_closes_its_connection_pool():
    http_client = httpx2.AsyncClient(transport=httpx2.MockTransport(lambda _: ok({})))
    provider = OpenAIProvider(api_key="test-key", http_client=http_client)

    await provider.aclose()

    assert http_client.is_closed


async def test_success_returns_parsed_model_and_metadata():
    provider, seen = provider_with(
        lambda _: ok(response_body([text_part('{"service": "payments", "urgent": true}')]))
    )

    result = await provider.generate_structured(prompt=PROMPT, schema=Ticket, config=CONFIG)

    assert result.status is LLMStatus.SUCCEEDED
    assert result.value == Ticket(service="payments", urgent=True)
    meta = result.metadata
    assert (meta.provider, meta.prompt_name, meta.prompt_version) == ("openai", "demo", "v1")
    assert meta.model == "test-model-2026-01-01"  # the model that actually answered
    assert (meta.input_tokens, meta.output_tokens, meta.attempts) == (42, 7, 1)
    assert meta.request_id == "req_abc"
    assert meta.latency_ms >= 0
    assert len(seen) == 1


async def test_request_uses_strict_json_schema_and_our_limits():
    provider, seen = provider_with(
        lambda _: ok(response_body([text_part('{"service": "a", "urgent": false}')]))
    )

    await provider.generate_structured(prompt=PROMPT, schema=Ticket, config=CONFIG)

    sent = json.loads(seen[0].content)
    assert seen[0].url.path.endswith("/responses")
    assert sent["model"] == "test-model"
    assert sent["instructions"] == "Extract a ticket."
    assert sent["input"] == "pay is down!"
    assert sent["max_output_tokens"] == 500
    assert sent["store"] is False
    fmt = sent["text"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["strict"] is True
    assert fmt["schema"]["additionalProperties"] is False
    assert set(fmt["schema"]["required"]) == {"service", "urgent"}


async def test_refusal_is_reported_as_refused_without_value():
    provider, _ = provider_with(
        lambda _: ok(response_body([{"type": "refusal", "refusal": "I can't help with that."}]))
    )

    result = await provider.generate_structured(prompt=PROMPT, schema=Ticket, config=CONFIG)

    assert result.status is LLMStatus.REFUSED
    assert result.value is None


async def test_output_cut_off_at_token_cap_is_truncated():
    provider, _ = provider_with(
        lambda _: ok(
            response_body(
                [text_part('{"service": "pay')],
                status="incomplete",
                incomplete={"reason": "max_output_tokens"},
            )
        )
    )

    result = await provider.generate_structured(prompt=PROMPT, schema=Ticket, config=CONFIG)

    assert result.status is LLMStatus.TRUNCATED
    assert result.value is None


async def test_reply_not_matching_schema_is_invalid_output():
    provider, _ = provider_with(
        lambda _: ok(response_body([text_part('{"service": "payments", "urgent": "maybe"}')]))
    )

    result = await provider.generate_structured(prompt=PROMPT, schema=Ticket, config=CONFIG)

    assert result.status is LLMStatus.INVALID_OUTPUT
    assert result.value is None
    assert "maybe" not in (result.error_detail or "")  # never echo model/user content


async def test_http_errors_map_to_statuses_and_sdk_does_not_retry():
    cases = {
        429: LLMStatus.RATE_LIMITED,
        500: LLMStatus.PROVIDER_ERROR,
        503: LLMStatus.PROVIDER_ERROR,
        400: LLMStatus.REJECTED,
        401: LLMStatus.REJECTED,
        404: LLMStatus.REJECTED,
    }
    for status_code, expected in cases.items():
        provider, seen = provider_with(
            lambda _, code=status_code: ok({"error": {"message": "nope", "type": "x"}}, code)
        )

        result = await provider.generate_structured(prompt=PROMPT, schema=Ticket, config=CONFIG)

        assert result.status is expected, status_code
        assert result.value is None
        assert len(seen) == 1, "SDK retries must be off; retries are ours (AIENG-21)"


async def test_timeout_and_network_failure_do_not_raise():
    def timeout(request):
        raise httpx2.ReadTimeout("slow", request=request)

    def refused(request):
        raise httpx2.ConnectError("no route", request=request)

    for handler, expected in [
        (timeout, LLMStatus.TIMED_OUT),
        (refused, LLMStatus.PROVIDER_ERROR),
    ]:
        provider, _ = provider_with(handler)

        result = await provider.generate_structured(prompt=PROMPT, schema=Ticket, config=CONFIG)

        assert result.status is expected
        assert result.value is None


# --- AIENG-21: injected faults through the real SDK + our retry layer -------------------------

from incidentops_ai.llm.retry import RetryingProvider

GOOD = response_body([text_part('{"service": "payments", "urgent": true}')])


def error(status_code: int, code: str = "x", headers: dict | None = None) -> httpx2.Response:
    return httpx2.Response(
        status_code, json={"error": {"message": "nope", "code": code}}, headers=headers or {}
    )


def scripted(*responses):
    """Each call gets the next scripted response (or raises it, if it is an exception class)."""
    queue = list(responses)

    def handler(request: httpx2.Request) -> httpx2.Response:
        item = queue.pop(0)
        if isinstance(item, type) and issubclass(item, Exception):
            raise item("injected", request=request)
        return item

    return handler


async def retrying(handler, config=CONFIG):
    provider, seen = provider_with(handler)
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    result = await RetryingProvider(provider, sleep=sleep).generate_structured(
        prompt=PROMPT, schema=Ticket, config=config
    )
    return result, seen, sleeps


async def test_html_error_page_with_status_200_is_a_provider_error_not_a_crash():
    page = httpx2.Response(
        200, text="<html>Bad gateway</html>", headers={"content-type": "text/html"}
    )
    provider, _ = provider_with(lambda _: page)

    result = await provider.generate_structured(prompt=PROMPT, schema=Ticket, config=CONFIG)

    assert result.status is LLMStatus.PROVIDER_ERROR and result.value is None


async def test_broken_json_body_is_a_provider_error_not_an_exception():
    broken = httpx2.Response(200, text="{not json", headers={"content-type": "application/json"})
    provider, _ = provider_with(lambda _: broken)

    result = await provider.generate_structured(prompt=PROMPT, schema=Ticket, config=CONFIG)

    assert result.status is LLMStatus.PROVIDER_ERROR and result.value is None


async def test_model_reply_that_is_not_json_is_invalid_output():
    provider, _ = provider_with(lambda _: ok(response_body([text_part("Sure! Here you go: ...")])))

    result = await provider.generate_structured(prompt=PROMPT, schema=Ticket, config=CONFIG)

    assert result.status is LLMStatus.INVALID_OUTPUT and result.value is None


async def test_wrong_types_are_rejected_not_coerced():
    # "true"/1/"yes" must not be silently turned into a boolean, nor numbers into strings.
    for reply in ['{"service": "pay", "urgent": "true"}', '{"service": 42, "urgent": true}']:
        provider, _ = provider_with(lambda _, r=reply: ok(response_body([text_part(r)])))

        result = await provider.generate_structured(prompt=PROMPT, schema=Ticket, config=CONFIG)

        assert result.status is LLMStatus.INVALID_OUTPUT, reply
        assert result.value is None


async def test_rate_limit_carries_retry_after_and_quota_does_not_retry():
    provider, _ = provider_with(lambda _: error(429, "rate_limit_exceeded", {"retry-after": "7"}))
    limited = await provider.generate_structured(prompt=PROMPT, schema=Ticket, config=CONFIG)
    assert limited.status is LLMStatus.RATE_LIMITED and limited.retry_after_s == 7.0

    result, seen, _ = await retrying(scripted(error(429, "insufficient_quota"), ok(GOOD)))
    assert result.status is LLMStatus.REJECTED
    assert len(seen) == 1


@pytest.mark.parametrize("value", ["nan", "inf", "-5", "soon", ""])
async def test_retry_after_that_is_not_a_usable_number_of_seconds_is_ignored(value):
    provider, _ = provider_with(lambda _: error(429, "rate_limit_exceeded", {"retry-after": value}))

    limited = await provider.generate_structured(prompt=PROMPT, schema=Ticket, config=CONFIG)

    assert limited.status is LLMStatus.RATE_LIMITED and limited.retry_after_s is None


async def test_429_twice_then_success_recovers_and_waits_as_told():
    result, seen, sleeps = await retrying(
        scripted(
            error(429, "rate_limit_exceeded", {"retry-after": "2"}),
            error(429, "rate_limit_exceeded", {"retry-after": "3"}),
            ok(GOOD),
        )
    )

    assert result.status is LLMStatus.SUCCEEDED
    assert result.value == Ticket(service="payments", urgent=True)
    assert result.metadata.attempts == 3 and len(seen) == 3
    assert sleeps == [2.0, 3.0]


async def test_timeout_then_500_then_success_recovers():
    result, _, _ = await retrying(scripted(httpx2.ReadTimeout, error(500), ok(GOOD)))

    assert result.status is LLMStatus.SUCCEEDED
    assert result.metadata.attempt_statuses == ["timed_out", "provider_error", "succeeded"]


async def test_persistent_outage_ends_as_provider_error_after_three_attempts():
    result, seen, _ = await retrying(scripted(error(503), error(503), error(503), ok(GOOD)))

    assert result.status is LLMStatus.PROVIDER_ERROR and result.value is None
    assert len(seen) == 3


async def test_bad_request_and_auth_errors_are_sent_once():
    for status_code in (400, 401, 403, 404):
        result, seen, sleeps = await retrying(scripted(error(status_code), ok(GOOD)))

        assert result.status is LLMStatus.REJECTED, status_code
        assert len(seen) == 1 and sleeps == []


async def test_malformed_reply_once_recovers_twice_fails():
    bad = ok(response_body([text_part('{"service": "pay", "urgent": "maybe"}')]))

    recovered, seen, _ = await retrying(scripted(bad, ok(GOOD)))
    assert recovered.status is LLMStatus.SUCCEEDED and len(seen) == 2

    failed, seen, _ = await retrying(scripted(bad, bad, ok(GOOD)))
    assert failed.status is LLMStatus.INVALID_OUTPUT and failed.value is None
    assert len(seen) == 2


async def test_refusal_is_not_retried():
    refusal = ok(response_body([{"type": "refusal", "refusal": "no"}]))

    result, seen, _ = await retrying(scripted(refusal, ok(GOOD)))

    assert result.status is LLMStatus.REFUSED and len(seen) == 1


# --- AIENG-22: tool calls through the real SDK ------------------------------------------------

from incidentops_ai.llm.provider import ToolOutput, ToolSpec


class LookupArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service: str
    limit: int


LOOKUP = ToolSpec(
    name="get_recent_incidents", description="Recent incidents.", arguments=LookupArgs
)


def tool_response(output: list[dict]) -> dict:
    body = response_body([])
    body["output"] = output
    return body


def function_call(call_id: str, service: str) -> dict:
    return {
        "type": "function_call",
        "id": f"fc_{call_id}",
        "call_id": call_id,
        "name": "get_recent_incidents",
        "arguments": json.dumps({"service": service, "limit": 5}),
        "status": "completed",
    }


REASONING = {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "gAAA-opaque"}


async def test_tool_turn_parses_parallel_calls_and_sends_strict_tool_schema():
    provider, seen = provider_with(
        lambda _: ok(
            tool_response(
                [REASONING, function_call("c1", "checkout-api"), function_call("c2", "pay")]
            )
        )
    )

    turn = await provider.tool_call(prompt=PROMPT, tools=[LOOKUP], config=CONFIG)

    assert turn.status is LLMStatus.SUCCEEDED and turn.final_text is None
    assert [(c.call_id, c.name) for c in turn.calls] == [
        ("c1", "get_recent_incidents"),
        ("c2", "get_recent_incidents"),
    ]
    assert json.loads(turn.calls[0].arguments_json) == {"service": "checkout-api", "limit": 5}
    assert turn.metadata.prompt_version == "v1" and turn.metadata.input_tokens == 42

    sent = json.loads(seen[0].content)
    assert sent["parallel_tool_calls"] is True
    assert sent["store"] is False
    assert sent["include"] == ["reasoning.encrypted_content"]
    assert sent["input"] == [{"role": "user", "content": "pay is down!"}]
    [tool] = sent["tools"]
    assert (tool["type"], tool["name"], tool["strict"]) == (
        "function",
        "get_recent_incidents",
        True,
    )
    assert tool["parameters"]["additionalProperties"] is False
    assert set(tool["parameters"]["required"]) == {"service", "limit"}


async def test_second_turn_sends_reasoning_calls_and_outputs_back_then_gets_the_answer():
    provider, seen = provider_with(
        scripted(
            ok(tool_response([REASONING, function_call("c1", "checkout-api")])),
            ok(response_body([text_part("One related incident: abc.")])),
        )
    )

    first = await provider.tool_call(prompt=PROMPT, tools=[LOOKUP], config=CONFIG)
    final = await provider.tool_call(
        prompt=PROMPT,
        tools=[LOOKUP],
        config=CONFIG,
        transcript=first.transcript,
        outputs=[ToolOutput(call_id="c1", output='{"result": []}')],
    )

    assert final.status is LLMStatus.SUCCEEDED
    assert final.calls == [] and final.final_text == "One related incident: abc."
    sent = json.loads(seen[1].content)["input"]
    assert [item.get("type", item.get("role")) for item in sent] == [
        "user",
        "reasoning",
        "function_call",
        "function_call_output",
    ]
    assert sent[1]["encrypted_content"] == "gAAA-opaque"
    assert sent[2]["call_id"] == "c1"
    assert sent[3] == {"type": "function_call_output", "call_id": "c1", "output": '{"result": []}'}


async def test_tool_turn_failures_use_the_same_statuses_and_retry_rules():
    provider, _ = provider_with(lambda _: error(429, "rate_limit_exceeded", {"retry-after": "4"}))
    limited = await provider.tool_call(prompt=PROMPT, tools=[LOOKUP], config=CONFIG)
    assert limited.status is LLMStatus.RATE_LIMITED and limited.retry_after_s == 4.0
    assert limited.calls == []

    inner, seen = provider_with(
        scripted(error(503), ok(tool_response([function_call("c1", "checkout-api")])))
    )

    async def no_sleep(_):
        return None

    turn = await RetryingProvider(inner, sleep=no_sleep).tool_call(
        prompt=PROMPT, tools=[LOOKUP], config=CONFIG
    )
    assert turn.status is LLMStatus.SUCCEEDED and len(turn.calls) == 1
    assert turn.metadata.attempts == 2 and len(seen) == 2


async def test_images_are_sent_as_input_image_parts_next_to_the_text():
    from incidentops_ai.llm.provider import ImageInput

    provider, seen = provider_with(
        lambda _: ok(response_body([text_part('{"service": "a", "urgent": false}')]))
    )
    prompt = PROMPT.model_copy(
        update={
            "images": (ImageInput(media_type="image/png", data=b"\x89PNG-bytes", detail="low"),)
        }
    )

    await provider.generate_structured(prompt=prompt, schema=Ticket, config=CONFIG)

    sent = json.loads(seen[0].content)
    [message] = sent["input"]
    assert message["role"] == "user"
    assert message["content"][0] == {"type": "input_text", "text": "pay is down!"}
    image = message["content"][1]
    assert image["type"] == "input_image" and image["detail"] == "low"
    assert image["image_url"] == "data:image/png;base64,iVBORy1ieXRlcw=="
