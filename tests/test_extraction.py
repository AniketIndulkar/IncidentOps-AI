"""Extraction schemas, prompt loading, sample data and the extraction service (offline)."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from incidentops_ai.extraction.models import IncidentExtraction
from incidentops_ai.extraction.service import EXTRACTION_PROMPT, extract_incident
from incidentops_ai.incidents.models import Environment, Severity
from incidentops_ai.llm.fake_provider import FakeProvider
from incidentops_ai.llm.prompts import PromptNotFoundError, load_prompt
from incidentops_ai.llm.provider import LLMStatus, ModelConfig

SAMPLES = Path(__file__).parent.parent / "evals" / "extraction" / "samples.jsonl"
CONFIG = ModelConfig(model="fake-model")

VALID_EXTRACTION = {
    "title": "Checkout API returning 502s",
    "service": {"name": "checkout-api", "stated_explicitly": True},
    "symptoms": [{"description": "30% of POST /checkout fail with 502", "started_at": "14:05 UTC"}],
    "error_codes": [{"code": "502", "kind": "http_status"}],
    "environment": "production",
    "severity_candidate": {"level": "SEV2", "rationale": "Partial outage of a revenue path."},
    "entities": [{"type": "endpoint", "value": "POST /checkout"}],
    "missing_information": [
        {"field": "recent_changes", "question": "Was anything deployed before 14:05 UTC?"}
    ],
}


def test_valid_extraction_parses_into_typed_objects():
    extraction = IncidentExtraction.model_validate(VALID_EXTRACTION)

    assert extraction.service.name == "checkout-api"
    assert extraction.environment is Environment.PRODUCTION
    assert extraction.severity_candidate.level is Severity.SEV2
    assert extraction.error_codes[0].kind == "http_status"
    assert extraction.missing_information[0].field == "recent_changes"


def test_unknown_values_are_null_not_guessed():
    # The schema must let the model say "not stated" instead of inventing a value.
    data = {
        **VALID_EXTRACTION,
        "service": {"name": None, "stated_explicitly": False},
        "environment": None,
        "symptoms": [{"description": "site is slow", "started_at": None}],
    }

    extraction = IncidentExtraction.model_validate(data)

    assert extraction.service.name is None
    assert extraction.environment is None


@pytest.mark.parametrize(
    "broken",
    [
        {"severity_candidate": {"level": "CRITICAL", "rationale": "x"}},
        {"environment": "prod-eu"},
        {"error_codes": [{"code": "502", "kind": "made_up_kind"}]},
        {"service": {"name": "Checkout API!", "stated_explicitly": True}},
        {"title": ""},
        {"unexpected_field": 1},
    ],
)
def test_invalid_extraction_is_rejected(broken):
    with pytest.raises(ValidationError):
        IncidentExtraction.model_validate({**VALID_EXTRACTION, **broken})


def test_every_field_is_required_for_strict_structured_output():
    # Strict structured output needs all properties listed as required; optional = nullable.
    schema = IncidentExtraction.model_json_schema()

    assert set(schema["required"]) == set(schema["properties"])
    for definition in schema["$defs"].values():
        if definition.get("type") == "object":
            assert set(definition["required"]) == set(definition["properties"])
            assert definition["additionalProperties"] is False


def test_prompt_v1_loads_with_name_and_version():
    prompt = load_prompt("extract_incident", "v1", input_text="db is down")

    assert prompt.name == "extract_incident"
    assert prompt.version == "v1"
    assert prompt.input == "db is down"
    assert "missing_information" in prompt.instructions


def test_unknown_prompt_version_fails_clearly():
    with pytest.raises(PromptNotFoundError):
        load_prompt("extract_incident", "v999", input_text="x")


def test_sample_file_has_ten_usable_incidents():
    rows = [json.loads(line) for line in SAMPLES.read_text().splitlines() if line.strip()]

    assert len(rows) == 10
    assert len({row["id"] for row in rows}) == 10
    for row in rows:
        assert row["text"].strip()
        assert "expected_service" in row


async def test_extract_incident_returns_typed_value_and_run_metadata():
    provider = FakeProvider(replies=[IncidentExtraction.model_validate(VALID_EXTRACTION)])

    result = await extract_incident(provider, "Checkout is throwing 502s since 14:05", CONFIG)

    assert result.status is LLMStatus.SUCCEEDED
    assert isinstance(result.value, IncidentExtraction)
    assert result.value.service.name == "checkout-api"
    assert result.metadata.prompt_name == EXTRACTION_PROMPT
    assert result.metadata.prompt_version == "v1"
    assert result.metadata.model == "fake-model"
    assert provider.calls[0].prompt.input == "Checkout is throwing 502s since 14:05"
    assert provider.calls[0].schema is IncidentExtraction


async def test_failed_extraction_has_status_and_no_value():
    provider = FakeProvider(replies=[LLMStatus.TIMED_OUT])

    result = await extract_incident(provider, "anything", CONFIG)

    assert result.status is LLMStatus.TIMED_OUT
    assert result.value is None
