"""Tool argument validation, the read-only incidents tool, parallel execution and the tool loop."""

import asyncio
import json

import pytest
from pydantic import BaseModel, ConfigDict

from incidentops_ai.incidents.models import IncidentCreate
from incidentops_ai.incidents.repository import IncidentRepository
from incidentops_ai.llm.fake_provider import FakeProvider
from incidentops_ai.llm.provider import LLMStatus, ModelConfig, Prompt, ToolCall, ToolSpec
from incidentops_ai.llm.tool_loop import run_tool_loop
from incidentops_ai.tools.recent_incidents import make_recent_incidents_tool
from incidentops_ai.tools.registry import Tool, ToolRegistry

CONFIG = ModelConfig(model="fake-model")
PROMPT = Prompt(name="related_incidents", version="v1", instructions="i", input="checkout is down")


def call(
    arguments: dict | str, name: str = "get_recent_incidents", call_id: str = "c1"
) -> ToolCall:
    raw = arguments if isinstance(arguments, str) else json.dumps(arguments)
    return ToolCall(call_id=call_id, name=name, arguments_json=raw)


async def seed(session_factory, service: str, titles: list[str]) -> None:
    async with session_factory() as session:
        repo = IncidentRepository(session)
        for title in titles:
            await repo.create(
                IncidentCreate(
                    title=title,
                    description="secret internal details",
                    service=service,
                    severity="SEV2",
                    environment="production",
                )
            )


# --- the real read-only tool ------------------------------------------------------------------


async def test_recent_incidents_returns_newest_first_for_that_service_only(session_factory):
    await seed(session_factory, "checkout-api", ["first", "second", "third"])
    await seed(session_factory, "orders-api", ["other service"])
    registry = ToolRegistry([make_recent_incidents_tool(session_factory)])

    [(execution, output)] = await registry.run_all([call({"service": "checkout-api", "limit": 2})])

    assert execution.executed and execution.ok
    assert execution.arguments == {"service": "checkout-api", "limit": 2}
    result = json.loads(output.output)["result"]
    assert [row["title"] for row in result] == ["third", "second"]
    assert set(result[0]) == {"id", "title", "severity", "status", "environment", "created_at"}
    assert "secret internal details" not in output.output  # descriptions never leave the DB


async def test_recent_incidents_for_unknown_service_is_an_empty_list(session_factory):
    registry = ToolRegistry([make_recent_incidents_tool(session_factory)])

    [(execution, output)] = await registry.run_all([call({"service": "nope", "limit": 5})])

    assert execution.ok and json.loads(output.output) == {"result": []}


# --- validation gate: invalid calls never run ---------------------------------------------------


class SpyArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service: str
    limit: int


def spy_registry(session_factory=None):
    ran: list = []

    async def handler(args):
        ran.append(args)
        return "ok"

    real = make_recent_incidents_tool(session_factory) if session_factory else None
    spec = (
        real.spec
        if real
        else ToolSpec(name="get_recent_incidents", description="d", arguments=SpyArgs)
    )
    return ToolRegistry([Tool(spec=spec, handler=handler)]), ran


@pytest.mark.parametrize(
    "arguments",
    [
        {"service": "checkout-api", "limit": "5"},  # string instead of integer: no coercion
        {"service": "checkout-api", "limit": 0},
        {"service": "checkout-api", "limit": 11},
        {"service": "checkout-api", "limit": 2.5},
        {"service": "Checkout API; DROP TABLE incidents", "limit": 5},
        {"service": "checkout-api"},  # missing field
        {"service": "checkout-api", "limit": 5, "include_deleted": True},  # extra field
        {"service": None, "limit": 5},
        "{not json",
        "[]",
    ],
)
async def test_invalid_arguments_are_rejected_and_the_tool_never_runs(session_factory, arguments):
    registry, ran = spy_registry(session_factory)

    [(execution, output)] = await registry.run_all([call(arguments)])

    assert ran == []
    assert execution.executed is False and execution.ok is False
    assert execution.error == "invalid arguments" and execution.arguments is None
    assert json.loads(output.output)["error"]["reason"] == "invalid arguments"


async def test_rejection_names_the_field_but_never_echoes_the_value(session_factory):
    registry, _ = spy_registry(session_factory)
    bad = {"service": "password=hunter2", "limit": 5}

    [(_, output)] = await registry.run_all([call(bad)])

    assert "hunter2" not in output.output
    assert json.loads(output.output)["error"]["details"][0]["field"] == "service"


async def test_unknown_tool_is_rejected():
    registry, ran = spy_registry()

    [(execution, output)] = await registry.run_all([call({"x": 1}, name="delete_incidents")])

    assert ran == [] and execution.executed is False
    assert execution.error == "unknown tool"
    assert json.loads(output.output)["error"]["reason"] == "unknown tool"


async def test_tool_crash_is_reported_not_raised():
    async def broken(args):
        raise RuntimeError("db exploded: password=hunter2")

    spec = ToolSpec(name="get_recent_incidents", description="d", arguments=SpyArgs)
    registry = ToolRegistry([Tool(spec=spec, handler=broken)])

    [(execution, output)] = await registry.run_all([call({"service": "a", "limit": 1})])

    assert execution.executed is True and execution.ok is False
    assert json.loads(output.output) == {"error": {"reason": "tool failed"}}
    assert "hunter2" not in output.output


# --- parallel execution -------------------------------------------------------------------------


async def test_calls_in_one_turn_run_concurrently_and_keep_their_order():
    active = 0
    peak = 0

    async def slow(args):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.05)
        active -= 1
        return args.service

    spec = ToolSpec(name="get_recent_incidents", description="d", arguments=SpyArgs)
    registry = ToolRegistry([Tool(spec=spec, handler=slow)])
    calls = [call({"service": name, "limit": 1}, call_id=name) for name in ("a", "b", "c")]

    loop = asyncio.get_running_loop()
    started = loop.time()
    results = await registry.run_all(calls)
    elapsed = loop.time() - started

    assert peak == 3  # all three were in flight at once
    assert elapsed < 0.12  # ~0.05 s in parallel, not ~0.15 s one after another
    assert [e.call_id for e, _ in results] == ["a", "b", "c"]
    assert [json.loads(o.output)["result"] for _, o in results] == ["a", "b", "c"]


async def test_one_bad_call_does_not_stop_the_good_ones(session_factory):
    await seed(session_factory, "checkout-api", ["one"])
    registry = ToolRegistry([make_recent_incidents_tool(session_factory)])

    results = await registry.run_all(
        [
            call({"service": "checkout-api", "limit": 5}, call_id="good"),
            call({"service": "checkout-api", "limit": 500}, call_id="bad"),
        ]
    )

    assert [(e.call_id, e.executed, e.ok) for e, _ in results] == [
        ("good", True, True),
        ("bad", False, False),
    ]


# --- the loop -----------------------------------------------------------------------------------


async def test_loop_runs_parallel_calls_then_returns_the_final_answer(session_factory):
    await seed(session_factory, "checkout-api", ["Checkout 502s last week"])
    registry = ToolRegistry([make_recent_incidents_tool(session_factory)])
    provider = FakeProvider(
        tool_turns=[
            [
                call({"service": "checkout-api", "limit": 5}, call_id="c1"),
                call({"service": "payments-gateway", "limit": 5}, call_id="c2"),
            ],
            "Related: Checkout 502s last week.",
        ]
    )

    result = await run_tool_loop(provider, PROMPT, registry, CONFIG)

    assert result.status is LLMStatus.SUCCEEDED
    assert result.final_text == "Related: Checkout 502s last week."
    assert result.rounds == 2
    assert [(e.call_id, e.ok) for e in result.executions] == [("c1", True), ("c2", True)]
    # Second turn received both results, in order, in one batch.
    sent_back = provider.tool_calls[1].outputs
    assert [o.call_id for o in sent_back] == ["c1", "c2"]
    assert "Checkout 502s last week" in sent_back[0].output
    # Usage is summed over both model turns.
    assert (result.metadata.input_tokens, result.metadata.output_tokens) == (20, 10)
    assert result.metadata.prompt_version == "v1"


async def test_loop_feeds_rejections_back_so_the_model_can_correct_itself(session_factory):
    registry, ran = spy_registry(session_factory)
    provider = FakeProvider(
        tool_turns=[
            [call({"service": "checkout-api", "limit": 999}, call_id="bad")],
            [call({"service": "checkout-api", "limit": 5}, call_id="fixed")],
            "done",
        ]
    )

    result = await run_tool_loop(provider, PROMPT, registry, CONFIG)

    assert result.status is LLMStatus.SUCCEEDED and result.rounds == 3
    assert [(e.call_id, e.executed) for e in result.executions] == [("bad", False), ("fixed", True)]
    assert len(ran) == 1  # only the corrected call ran
    assert "invalid arguments" in provider.tool_calls[1].outputs[0].output


async def test_loop_without_tool_calls_answers_in_one_round():
    registry, ran = spy_registry()
    provider = FakeProvider(tool_turns=["No service named, nothing to look up."])

    result = await run_tool_loop(provider, PROMPT, registry, CONFIG)

    assert result.status is LLMStatus.SUCCEEDED and result.rounds == 1
    assert result.executions == [] and ran == []


async def test_loop_stops_at_the_round_limit():
    registry, ran = spy_registry()
    again = [call({"service": "a", "limit": 1})]
    provider = FakeProvider(tool_turns=[again, again, again, again])

    result = await run_tool_loop(provider, PROMPT, registry, CONFIG, max_rounds=3)

    assert result.status is LLMStatus.INVALID_OUTPUT
    assert result.final_text is None and result.rounds == 3
    assert len(provider.tool_calls) == 3 and len(ran) == 3


async def test_loop_reports_provider_failure_with_what_already_ran():
    registry, _ = spy_registry()
    provider = FakeProvider(tool_turns=[[call({"service": "a", "limit": 1})], LLMStatus.TIMED_OUT])

    result = await run_tool_loop(provider, PROMPT, registry, CONFIG)

    assert result.status is LLMStatus.TIMED_OUT and result.final_text is None
    assert len(result.executions) == 1
