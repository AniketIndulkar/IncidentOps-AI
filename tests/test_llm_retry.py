"""Bounded retry: which failures are retried, how often, how long we wait, and when we give up.

Time is faked: `sleep` records the delay and advances a fake clock, so tests are instant.
"""

import pytest
from pydantic import BaseModel, ConfigDict

from incidentops_ai.llm.fake_provider import Fail, FakeProvider
from incidentops_ai.llm.provider import LLMStatus, ModelConfig, Prompt
from incidentops_ai.llm.retry import MAX_DELAY_S, MAX_RETRY_AFTER_S, RetryingProvider

S = LLMStatus


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str


OK = Answer(text="fine")
PROMPT = Prompt(name="demo", version="v1", instructions="i", input="x")
CONFIG = ModelConfig(model="m", max_attempts=3, timeout_s=30, total_timeout_s=60)


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


async def run(replies, config=CONFIG, rand=lambda: 1.0, call_takes: float = 0.0):
    fake = FakeProvider(replies)
    time = FakeTime()
    if call_takes:
        original = fake.generate_structured

        async def slow(**kwargs):
            time.now += call_takes
            return await original(**kwargs)

        fake.generate_structured = slow  # type: ignore[method-assign]
    provider = RetryingProvider(fake, sleep=time.sleep, clock=time.clock, rand=rand)
    result = await provider.generate_structured(prompt=PROMPT, schema=Answer, config=config)
    return result, fake, time


async def test_success_first_time_makes_one_call_and_no_sleep():
    result, fake, time = await run([OK])

    assert result.status is S.SUCCEEDED and result.value == OK
    assert result.metadata.attempts == 1
    assert result.metadata.attempt_statuses == ["succeeded"]
    assert len(fake.calls) == 1 and time.sleeps == []


@pytest.mark.parametrize("transient", [S.TIMED_OUT, S.RATE_LIMITED, S.PROVIDER_ERROR])
async def test_transient_failures_are_retried_until_success(transient):
    result, _, time = await run([transient, transient, OK])

    assert result.status is S.SUCCEEDED and result.value == OK
    assert result.metadata.attempts == 3
    assert result.metadata.attempt_statuses == [transient, transient, "succeeded"]
    assert len(time.sleeps) == 2


@pytest.mark.parametrize("transient", [S.TIMED_OUT, S.RATE_LIMITED, S.PROVIDER_ERROR])
async def test_transient_failures_stop_at_max_attempts_with_explicit_status(transient):
    result, fake, _ = await run([transient] * 5)

    assert result.status is transient  # the real reason, not a generic error
    assert result.value is None
    assert result.metadata.attempts == 3
    assert len(fake.calls) == 3


@pytest.mark.parametrize("permanent", [S.REJECTED, S.REFUSED])
async def test_permanent_failures_are_never_retried(permanent):
    result, fake, time = await run([permanent, OK])

    assert result.status is permanent and result.value is None
    assert result.metadata.attempts == 1
    assert len(fake.calls) == 1 and time.sleeps == []


@pytest.mark.parametrize("bad_output", [S.INVALID_OUTPUT, S.TRUNCATED])
async def test_bad_output_gets_exactly_one_more_try(bad_output):
    recovered, fake, time = await run([bad_output, OK])
    assert recovered.status is S.SUCCEEDED and recovered.metadata.attempts == 2
    assert time.sleeps == []  # nothing to wait for: the provider is healthy

    gave_up, fake, _ = await run([bad_output, bad_output, OK])
    assert gave_up.status is bad_output and gave_up.value is None
    assert gave_up.metadata.attempts == 2 and len(fake.calls) == 2


async def test_truncated_retry_doubles_the_output_cap():
    config = CONFIG.model_copy(update={"max_output_tokens": 500})

    _, fake, _ = await run([S.TRUNCATED, OK], config=config)

    assert [c.config.max_output_tokens for c in fake.calls] == [500, 1000]


async def test_bad_output_retry_does_not_reset_after_transient_failures():
    result, fake, _ = await run([S.INVALID_OUTPUT, S.RATE_LIMITED, S.INVALID_OUTPUT, OK])

    assert result.status is S.INVALID_OUTPUT
    assert len(fake.calls) == 3


async def test_backoff_grows_exponentially_and_is_capped():
    config = CONFIG.model_copy(update={"max_attempts": 8, "total_timeout_s": 10_000})

    _, _, time = await run([S.PROVIDER_ERROR] * 8, config=config, rand=lambda: 1.0)

    assert time.sleeps[:4] == [0.5, 1.0, 2.0, 4.0]
    assert max(time.sleeps) == MAX_DELAY_S


async def test_jitter_spreads_delays_between_half_and_full():
    _, _, low = await run([S.PROVIDER_ERROR, S.PROVIDER_ERROR, OK], rand=lambda: 0.0)
    _, _, high = await run([S.PROVIDER_ERROR, S.PROVIDER_ERROR, OK], rand=lambda: 1.0)

    assert low.sleeps == [0.25, 0.5]
    assert high.sleeps == [0.5, 1.0]


async def test_retry_after_from_the_provider_is_honoured():
    result, _, time = await run([Fail(S.RATE_LIMITED, retry_after_s=7.0), OK])

    assert result.status is S.SUCCEEDED
    assert time.sleeps == [7.0]


async def test_very_long_retry_after_gives_up_instead_of_waiting():
    result, fake, time = await run([Fail(S.RATE_LIMITED, retry_after_s=MAX_RETRY_AFTER_S + 1), OK])

    assert result.status is S.RATE_LIMITED
    assert len(fake.calls) == 1 and time.sleeps == []


async def test_total_time_budget_stops_retries_even_with_attempts_left():
    config = CONFIG.model_copy(update={"max_attempts": 10, "total_timeout_s": 20, "timeout_s": 15})

    result, fake, time = await run([S.TIMED_OUT] * 10, config=config, call_takes=15.0)

    assert result.status is S.TIMED_OUT
    assert len(fake.calls) < 10
    assert time.now <= 20 + 15  # never starts an attempt after the budget is spent


async def test_last_attempt_timeout_is_cut_to_the_remaining_budget():
    config = CONFIG.model_copy(update={"total_timeout_s": 20, "timeout_s": 15})

    _, fake, _ = await run([S.TIMED_OUT, S.TIMED_OUT, OK], config=config, call_takes=12.0)

    assert fake.calls[0].config.timeout_s == 15
    assert fake.calls[1].config.timeout_s < 15


async def test_tokens_and_latency_are_summed_across_attempts():
    replies = [
        Fail(S.INVALID_OUTPUT, input_tokens=100, output_tokens=40, latency_ms=900),
        Fail(S.SUCCEEDED, value=OK, input_tokens=100, output_tokens=60, latency_ms=1100),
    ]

    result, _, _ = await run(replies)

    meta = result.metadata
    assert result.status is S.SUCCEEDED
    assert (meta.input_tokens, meta.output_tokens, meta.latency_ms) == (200, 100, 2000)


async def test_max_attempts_of_one_disables_retries():
    result, fake, _ = await run(
        [S.PROVIDER_ERROR, OK], config=CONFIG.model_copy(update={"max_attempts": 1})
    )

    assert result.status is S.PROVIDER_ERROR and len(fake.calls) == 1
