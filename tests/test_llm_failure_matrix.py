"""The LLM failure matrix as executable checks (docs/notes/llm-failure-matrix.md, ADR-001).

Each row injects a fault at the HTTP level and drives the whole stack:
API route -> retry layer -> real OpenAI SDK code -> fake HTTP. For every row we assert what the
client sees, how many attempts were made, what was recorded, and that no partial data leaked.
"""

import json
from dataclasses import dataclass, field

import httpx2
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.main import create_app
from incidentops_ai.llm.openai_provider import OpenAIProvider
from incidentops_ai.llm.retry import RetryingProvider

INCIDENT = {
    "title": "Checkout API returning 502s",
    "description": "Since 14:05 UTC roughly 30% of POST /checkout requests fail with 502.",
    "service": "checkout-api",
    "severity": "SEV2",
    "environment": "production",
}
GOOD_EXTRACTION = {
    "title": "Checkout API returning 502s",
    "service": {"name": "checkout-api", "stated_explicitly": True},
    "symptoms": [{"description": "30% of POST /checkout fail", "started_at": "14:05 UTC"}],
    "error_codes": [{"code": "502", "kind": "http_status"}],
    "environment": "production",
    "severity_candidate": {"level": "SEV2", "rationale": "Partial outage."},
    "entities": [],
    "missing_information": [],
}


def reply(
    text_or_parts, status: str = "completed", incomplete: dict | None = None
) -> httpx2.Response:
    parts = (
        [{"type": "output_text", "text": text_or_parts, "annotations": []}]
        if isinstance(text_or_parts, str)
        else text_or_parts
    )
    return httpx2.Response(
        200,
        headers={"x-request-id": "req_matrix"},
        json={
            "id": "resp_1",
            "object": "response",
            "created_at": 1790000000,
            "model": "test-model",
            "status": status,
            "incomplete_details": incomplete,
            "output": [
                {
                    "id": "m1",
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": parts,
                }
            ],
            "parallel_tool_calls": True,
            "tool_choice": "auto",
            "tools": [],
            "usage": {
                "input_tokens": 100,
                "input_tokens_details": {"cached_tokens": 0},
                "output_tokens": 50,
                "output_tokens_details": {"reasoning_tokens": 0},
                "total_tokens": 150,
            },
        },
    )


def http_error(status_code: int, code: str = "x", headers: dict | None = None) -> httpx2.Response:
    return httpx2.Response(
        status_code, json={"error": {"message": "nope", "code": code}}, headers=headers or {}
    )


OK = reply(json.dumps(GOOD_EXTRACTION))
NOT_JSON = reply("Sure! Here is the incident summary you asked for...")
WRONG_SHAPE = reply(
    json.dumps({**GOOD_EXTRACTION, "severity_candidate": {"level": "CRITICAL", "rationale": "x"}})
)
MISSING_FIELD = reply(json.dumps({k: v for k, v in GOOD_EXTRACTION.items() if k != "service"}))
WRONG_TYPE = reply(
    json.dumps(
        {**GOOD_EXTRACTION, "service": {"name": "checkout-api", "stated_explicitly": "true"}}
    )
)
CUT_OFF = reply(
    '{"title": "Checkout', status="incomplete", incomplete={"reason": "max_output_tokens"}
)
REFUSAL = reply([{"type": "refusal", "refusal": "I can't help with that."}])
FILTERED = reply("", status="incomplete", incomplete={"reason": "content_filter"})
RATE_LIMIT = http_error(429, "rate_limit_exceeded", {"retry-after": "3"})
RATE_LIMIT_LONG = http_error(429, "rate_limit_exceeded", {"retry-after": "120"})
NO_QUOTA = http_error(429, "insufficient_quota")
BROKEN_JSON = httpx2.Response(200, text="{not json", headers={"content-type": "application/json"})
HTML_PAGE = httpx2.Response(
    200, text="<html>Bad gateway</html>", headers={"content-type": "text/html"}
)


@dataclass(frozen=True)
class Row:
    name: str
    faults: list  # one entry per HTTP attempt; an exception class is raised instead of returned
    http_status: int
    code: str | None  # problem code, or None for success
    attempts: int
    run_status: str
    headers: dict = field(default_factory=dict)


MATRIX = [
    # --- schema errors: one extra try, then fail; never repair or coerce
    Row(
        "reply is not JSON, twice",
        [NOT_JSON, NOT_JSON],
        502,
        "llm_invalid_output",
        2,
        "invalid_output",
    ),
    Row(
        "reply has an invalid enum value, twice",
        [WRONG_SHAPE, WRONG_SHAPE],
        502,
        "llm_invalid_output",
        2,
        "invalid_output",
    ),
    Row(
        "reply is missing a required field, twice",
        [MISSING_FIELD, MISSING_FIELD],
        502,
        "llm_invalid_output",
        2,
        "invalid_output",
    ),
    Row(
        "reply has a wrong type (string for a boolean), twice",
        [WRONG_TYPE, WRONG_TYPE],
        502,
        "llm_invalid_output",
        2,
        "invalid_output",
    ),
    Row("bad reply once, then a good one", [NOT_JSON, OK], 200, None, 2, "succeeded"),
    Row(
        "reply cut off at the token cap, twice",
        [CUT_OFF, CUT_OFF],
        502,
        "llm_truncated",
        2,
        "truncated",
    ),
    Row("cut off once, then complete", [CUT_OFF, OK], 200, None, 2, "succeeded"),
    # --- rate limits: retry as told, bounded
    Row("rate limited, then ok", [RATE_LIMIT, OK], 200, None, 2, "succeeded"),
    Row(
        "rate limited on every attempt",
        [RATE_LIMIT] * 3,
        503,
        "llm_rate_limited",
        3,
        "rate_limited",
        {"retry-after": "3"},
    ),
    Row(
        "told to wait longer than we accept",
        [RATE_LIMIT_LONG],
        503,
        "llm_rate_limited",
        1,
        "rate_limited",
        {"retry-after": "120"},
    ),
    # --- timeouts: retry, bounded
    Row("timeout, then ok", [httpx2.ReadTimeout, OK], 200, None, 2, "succeeded"),
    Row("timeout on every attempt", [httpx2.ReadTimeout] * 3, 504, "llm_timed_out", 3, "timed_out"),
    # --- provider outage: retry, bounded; no fallback model (ADR-001)
    Row("500 then ok", [http_error(500), OK], 200, None, 2, "succeeded"),
    Row(
        "503 on every attempt",
        [http_error(503)] * 3,
        502,
        "llm_provider_error",
        3,
        "provider_error",
    ),
    Row(
        "network unreachable on every attempt",
        [httpx2.ConnectError] * 3,
        502,
        "llm_provider_error",
        3,
        "provider_error",
    ),
    Row(
        "proxy HTML page with status 200",
        [HTML_PAGE] * 3,
        502,
        "llm_provider_error",
        3,
        "provider_error",
    ),
    Row(
        "broken JSON body with status 200",
        [BROKEN_JSON] * 3,
        502,
        "llm_provider_error",
        3,
        "provider_error",
    ),
    # --- rejection: a retry cannot help, so exactly one attempt
    Row("quota exhausted", [NO_QUOTA], 500, "llm_rejected", 1, "rejected"),
    Row("invalid API key", [http_error(401)], 500, "llm_rejected", 1, "rejected"),
    Row("unknown model", [http_error(404)], 500, "llm_rejected", 1, "rejected"),
    Row("malformed request", [http_error(400)], 500, "llm_rejected", 1, "rejected"),
    Row("model refuses", [REFUSAL], 422, "llm_refused", 1, "refused"),
    Row("content filter", [FILTERED], 422, "llm_refused", 1, "refused"),
]


@pytest.mark.parametrize("row", MATRIX, ids=lambda row: row.name)
async def test_failure_matrix(row: Row, settings, session_factory):
    queue = list(row.faults)
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        fault = queue.pop(0)
        if isinstance(fault, type) and issubclass(fault, Exception):
            raise fault("injected", request=request)
        return fault

    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    provider = RetryingProvider(
        OpenAIProvider(
            "test-key", http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
        ),
        sleep=sleep,
    )
    app = create_app(settings, llm_provider=provider)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client,
    ):
        incident = (await client.post("/incidents", json=INCIDENT)).json()
        resp = await client.post(f"/incidents/{incident['id']}/extraction")

    # What the client sees
    assert resp.status_code == row.http_status
    body = resp.json()
    if row.code is None:
        assert body["extraction"]["service"]["name"] == "checkout-api"
    else:
        assert resp.headers["content-type"] == "application/problem+json"
        assert body["code"] == row.code
        assert "extraction" not in body  # never partial data
        assert "hunter2" not in resp.text and "Traceback" not in resp.text
    for name, value in row.headers.items():
        assert resp.headers[name] == value

    # How hard we tried: bounded, and exactly as the matrix says
    assert len(seen) == row.attempts
    assert all(delay <= 30 for delay in sleeps)

    # What was recorded
    async with session_factory() as session:
        run = (await session.execute(text("SELECT * FROM llm_runs"))).one()._mapping
    assert run["status"] == row.run_status
    assert run["attempts"] == row.attempts
    assert len(run["attempt_statuses"]) == row.attempts
    assert (run["prompt_name"], run["prompt_version"]) == ("extract_incident", "v1")
    assert (run["output"] is not None) == (row.code is None)
    if row.code is not None:
        assert str(run["id"]) in body["detail"]


# --- low confidence: a valid reply that a human should still confirm ----------------------------

LOW_CONFIDENCE = [
    (
        "no service identified",
        {"service": {"name": None, "stated_explicitly": False}},
        ["no_service_identified"],
    ),
    (
        "service inferred, not stated",
        {"service": {"name": "checkout-api", "stated_explicitly": False}},
        ["service_inferred_not_stated"],
    ),
    ("environment not stated", {"environment": None}, ["environment_not_stated"]),
    ("no symptoms found", {"symptoms": []}, ["no_symptoms_found"]),
    (
        "many open questions",
        {"missing_information": [{"field": f"q{i}", "question": "?"} for i in range(4)]},
        ["many_open_questions"],
    ),
    (
        "SEV1 suggested",
        {"severity_candidate": {"level": "SEV1", "rationale": "Full outage."}},
        ["high_severity_needs_confirmation"],
    ),
    (
        "vague report: several reasons at once",
        {
            "service": {"name": None, "stated_explicitly": False},
            "environment": None,
            "missing_information": [{"field": f"q{i}", "question": "?"} for i in range(5)],
        },
        ["no_service_identified", "environment_not_stated", "many_open_questions"],
    ),
]


@pytest.mark.parametrize(
    ("name", "changes", "reasons"), LOW_CONFIDENCE, ids=[c[0] for c in LOW_CONFIDENCE]
)
async def test_low_confidence_results_succeed_but_are_flagged_for_review(
    name, changes, reasons, settings, session_factory
):
    extraction = {**GOOD_EXTRACTION, **changes}
    provider = RetryingProvider(
        OpenAIProvider(
            "test-key",
            http_client=httpx2.AsyncClient(
                transport=httpx2.MockTransport(lambda _: reply(json.dumps(extraction)))
            ),
        )
    )
    app = create_app(settings, llm_provider=provider)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client,
    ):
        incident = (await client.post("/incidents", json=INCIDENT)).json()
        resp = await client.post(f"/incidents/{incident['id']}/extraction")

    # Not a failure: the data is valid and returned. It is marked, with reasons, for a human.
    assert resp.status_code == 200
    assert resp.json()["review"] == {"required": True, "reasons": reasons}
    async with session_factory() as session:
        run = (await session.execute(text("SELECT * FROM llm_runs"))).one()._mapping
    assert run["status"] == "succeeded"
    assert run["output"]["review"] == {"required": True, "reasons": reasons}


async def test_complete_unambiguous_result_needs_no_review(settings):
    provider = RetryingProvider(
        OpenAIProvider(
            "k", http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(lambda _: OK))
        )
    )
    app = create_app(settings, llm_provider=provider)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client,
    ):
        incident = (await client.post("/incidents", json=INCIDENT)).json()
        resp = await client.post(f"/incidents/{incident['id']}/extraction")

    assert resp.json()["review"] == {"required": False, "reasons": []}
