"""RunMetadata.combine: the one place that adds up cost across calls (retry layer, tool loop)."""

from incidentops_ai.llm.provider import LLMStatus, RunMetadata

S = LLMStatus


def meta(**changes) -> RunMetadata:
    fields = {
        "provider": "openai",
        "model": "m",
        "prompt_name": "p",
        "prompt_version": "v1",
        "attempts": 1,
        "latency_ms": 0,
        "attempt_statuses": [S.SUCCEEDED],
    }
    return RunMetadata(**{**fields, **changes})


def test_combine_adds_latency_and_tokens_and_keeps_every_attempt_status():
    first = meta(
        latency_ms=100,
        input_tokens=10,
        output_tokens=5,
        attempts=2,
        attempt_statuses=[S.TIMED_OUT, S.SUCCEEDED],
    )
    second = meta(latency_ms=50, input_tokens=None, output_tokens=7)

    total = first.combine(second)

    assert total.latency_ms == 150
    assert (total.input_tokens, total.output_tokens) == (10, 12)
    assert total.attempt_statuses == [S.TIMED_OUT, S.SUCCEEDED, S.SUCCEEDED]
    assert total.attempts == 2  # the worst single call, not 3


def test_combine_names_the_latest_call():
    total = meta(model="m-1", request_id="r1").combine(meta(model="m-2", request_id="r2"))

    assert (total.model, total.request_id) == ("m-2", "r2")


def test_combine_keeps_unknown_tokens_unknown():
    total = meta().combine(meta())

    assert total.input_tokens is None and total.output_tokens is None


def test_combine_does_not_change_its_inputs():
    first, second = meta(latency_ms=1), meta(latency_ms=2)

    first.combine(second)

    assert first.latency_ms == 1 and second.latency_ms == 2
    assert first.attempt_statuses == [S.SUCCEEDED]
