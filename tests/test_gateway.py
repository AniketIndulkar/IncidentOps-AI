"""AIENG-66 / H47: every model call goes through one gateway: per-task routes, fallback only on
failures another model could fix, a circuit breaker, and a per-model rate limit."""

import pytest
from pydantic import BaseModel

from incidentops_ai.llm.fake_provider import FakeProvider
from incidentops_ai.llm.gateway import Gateway, task_of
from incidentops_ai.llm.provider import LLMStatus, ModelConfig, Prompt

CONFIG = ModelConfig(model="configured-model")


class Answer(BaseModel):
    text: str


def prompt(name: str = "diagnose_incident") -> Prompt:
    return Prompt(name=name, version="v1", instructions="i", input="x")


class ByModel:
    """Answers per model: a list of scripted outcomes per model name, consumed in order."""

    def __init__(self, script: dict[str, list]) -> None:
        self._script = {model: list(outcomes) for model, outcomes in script.items()}
        self.called: list[str] = []

    async def generate_structured(self, *, prompt, schema, config):
        self.called.append(config.model)
        outcome = self._script[config.model].pop(0)
        reply = Answer(text=f"from {config.model}") if outcome == "ok" else outcome
        return await FakeProvider([reply]).generate_structured(
            prompt=prompt, schema=schema, config=config
        )

    async def tool_call(self, *, prompt, tools, config, transcript=None, outputs=()):
        self.called.append(config.model)
        outcome = self._script[config.model].pop(0)
        turn = f"answer from {config.model}" if outcome == "ok" else outcome
        return await FakeProvider(tool_turns=[turn]).tool_call(
            prompt=prompt, tools=tools, config=config
        )

    async def aclose(self):
        return None


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


ROUTES = {"diagnosis": ["primary", "secondary"]}


async def ask(gateway, name="diagnose_incident"):
    return await gateway.generate_structured(prompt=prompt(name), schema=Answer, config=CONFIG)


def test_tasks_come_from_prompt_names():
    assert task_of("diagnose_incident") == "diagnosis"
    assert task_of("extract_incident") == "extraction"
    assert task_of("transcribe_image") == "transcription"
    assert task_of("something_new") == "something_new"


async def test_a_task_uses_its_route_and_others_the_configured_model():
    inner = ByModel({"primary": ["ok"], "configured-model": ["ok"]})
    gateway = Gateway(inner, ROUTES)

    routed = await ask(gateway)
    default = await ask(gateway, "extract_incident")

    assert inner.called == ["primary", "configured-model"]
    assert (routed.metadata.model, default.metadata.model) == ("primary", "configured-model")


@pytest.mark.parametrize(
    "failure", [LLMStatus.TIMED_OUT, LLMStatus.RATE_LIMITED, LLMStatus.PROVIDER_ERROR]
)
async def test_a_retryable_failure_falls_back(failure):
    inner = ByModel({"primary": [failure], "secondary": ["ok"]})

    result = await ask(Gateway(inner, ROUTES))

    assert result.status is LLMStatus.SUCCEEDED and result.value.text == "from secondary"
    assert result.metadata.model == "secondary" and result.metadata.fallback_from == ["primary"]


@pytest.mark.parametrize(
    "failure",
    [LLMStatus.INVALID_OUTPUT, LLMStatus.TRUNCATED, LLMStatus.REFUSED, LLMStatus.REJECTED],
)
async def test_failures_another_model_would_only_hide_do_not_fall_back(failure):
    inner = ByModel({"primary": [failure], "secondary": ["ok"]})

    result = await ask(Gateway(inner, ROUTES))

    assert result.status is failure and inner.called == ["primary"]


async def test_when_every_model_fails_the_last_failure_is_returned():
    inner = ByModel({"primary": [LLMStatus.TIMED_OUT], "secondary": [LLMStatus.PROVIDER_ERROR]})

    result = await ask(Gateway(inner, ROUTES))

    assert result.status is LLMStatus.PROVIDER_ERROR
    assert result.metadata.fallback_from == ["primary"]


async def test_the_breaker_skips_a_failing_model_then_tries_it_again():
    clock = Clock()
    inner = ByModel({"primary": [LLMStatus.TIMED_OUT] * 3 + ["ok"], "secondary": ["ok"] * 4})
    gateway = Gateway(inner, ROUTES, breaker_threshold=3, breaker_cooldown_s=30, clock=clock)

    for _ in range(3):
        await ask(gateway)
    skipped = await ask(gateway)  # breaker open: primary is not called at all
    clock.now = 31
    recovered = await ask(gateway)  # half-open: one trial call

    assert inner.called == ["primary", "secondary"] * 3 + ["secondary", "primary"]
    assert skipped.metadata.model == "secondary"
    assert recovered.metadata.model == "primary" and gateway.open_models() == []


async def test_a_model_over_its_rate_limit_hands_over_to_the_next():
    clock = Clock()
    inner = ByModel({"primary": ["ok"] * 2, "secondary": ["ok"]})
    gateway = Gateway(inner, ROUTES, per_model_per_minute=2, clock=clock)

    models = [(await ask(gateway)).metadata.model for _ in range(3)]

    assert models == ["primary", "primary", "secondary"]


async def test_tool_conversations_are_routed_too():
    inner = ByModel({"primary": [LLMStatus.TIMED_OUT], "secondary": ["ok"]})
    gateway = Gateway(inner, {"related_incidents": ["primary", "secondary"]})

    turn = await gateway.tool_call(prompt=prompt("related_incidents"), tools=[], config=CONFIG)

    assert turn.final_text == "answer from secondary"
