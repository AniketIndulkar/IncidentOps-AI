"""AIENG-51: the tool loop stops a looping model safely, with an explicit reason and a trace."""

import pytest
from test_tools import CONFIG, PROMPT, call, spy_registry

from incidentops_ai.llm.fake_provider import FakeProvider
from incidentops_ai.llm.provider import LLMStatus
from incidentops_ai.llm.tool_loop import run_tool_loop

SAME = {"service": "checkout-api", "limit": 5}


async def test_a_model_that_repeats_a_call_is_stopped_as_a_loop():
    registry, ran = spy_registry()
    provider = FakeProvider(tool_turns=[[call(SAME, call_id=f"c{i}")] for i in range(10)])

    result = await run_tool_loop(provider, PROMPT, registry, CONFIG, max_rounds=10)

    assert (result.status, result.stop_reason) == (LLMStatus.INVALID_OUTPUT, "loop_detected")
    assert result.rounds == 2 and len(provider.tool_calls) == 2
    assert len(ran) == 1  # the repeated call ran once
    first, repeat = result.executions
    assert (first.executed, first.ok) == (True, True)
    assert (repeat.executed, repeat.error, repeat.duplicate_of) == (False, "duplicate call", "c0")
    assert first.fingerprint == repeat.fingerprint
    assert "repeated" in result.error_detail


async def test_a_duplicate_is_answered_without_running_and_the_model_can_move_on():
    registry, ran = spy_registry()
    other = {"service": "payments-gateway", "limit": 5}
    provider = FakeProvider(
        tool_turns=[
            [call(SAME, call_id="c1")],
            [call(SAME, call_id="c2"), call(other, call_id="c3")],
            "Two services checked.",
        ]
    )

    result = await run_tool_loop(provider, PROMPT, registry, CONFIG)

    assert (result.status, result.stop_reason) == (LLMStatus.SUCCEEDED, "answered")
    assert len(ran) == 2
    sent_back = provider.tool_calls[2].outputs
    assert [o.call_id for o in sent_back] == ["c2", "c3"]
    assert "duplicate call" in sent_back[0].output and "c1" in sent_back[0].output


async def test_the_same_arguments_in_another_order_are_the_same_call():
    registry, ran = spy_registry()
    provider = FakeProvider(
        tool_turns=[
            [call('{"service": "checkout-api", "limit": 5}', call_id="c1")],
            [call('{"limit": 5,   "service": "checkout-api"}', call_id="c2")],
        ]
    )

    result = await run_tool_loop(provider, PROMPT, registry, CONFIG)

    assert result.stop_reason == "loop_detected" and len(ran) == 1


async def test_two_identical_calls_in_one_turn_run_once():
    registry, ran = spy_registry()
    provider = FakeProvider(
        tool_turns=[[call(SAME, call_id="c1"), call(SAME, call_id="c2")], "done"]
    )

    result = await run_tool_loop(provider, PROMPT, registry, CONFIG)

    assert result.stop_reason == "answered" and len(ran) == 1
    assert result.executions[1].duplicate_of == "c1"


async def test_the_tool_budget_stops_a_model_that_keeps_asking_for_new_calls():
    registry, ran = spy_registry()
    provider = FakeProvider(
        tool_turns=[[call({"service": f"s{i}", "limit": 1}, call_id=f"c{i}")] for i in range(10)]
    )

    result = await run_tool_loop(
        provider, PROMPT, registry, CONFIG, max_rounds=10, max_tool_calls=3
    )

    assert (result.status, result.stop_reason) == (LLMStatus.INVALID_OUTPUT, "tool_budget")
    assert len(ran) == 3 and result.rounds == 4
    assert result.executions[-1].error == "tool budget exhausted"
    assert not result.executions[-1].executed


@pytest.mark.parametrize(
    ("turns", "reason"),
    [
        (["The answer."], "answered"),
        ([""], "empty_answer"),
        ([LLMStatus.TIMED_OUT], "model_failed"),
        (
            [[call({"service": f"s{i}", "limit": 1}, call_id=f"c{i}")] for i in range(3)],
            "round_limit",
        ),
    ],
    ids=["answered", "empty", "model-failed", "round-limit"],
)
async def test_every_ending_has_an_explicit_reason(turns, reason):
    registry, _ = spy_registry()

    result = await run_tool_loop(
        FakeProvider(tool_turns=turns), PROMPT, registry, CONFIG, max_rounds=3
    )

    assert result.stop_reason == reason
