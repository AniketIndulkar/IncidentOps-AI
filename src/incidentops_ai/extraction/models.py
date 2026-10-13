"""What the LLM must return when it reads an incident report.

Schema rules for strict structured output:
- every field is required; "not stated" is expressed as null, never left out or guessed
- no extra fields (`extra="forbid"` → `additionalProperties: false`)
- value rules (non-empty text, service name format) are Pydantic validators, checked after the
  model replies, so the JSON Schema sent to the provider stays within what strict mode accepts
"""

import re
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict

from incidentops_ai.incidents.models import Environment, Severity

_SERVICE_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


def _non_empty(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("must not be empty")
    return value


def _service_name(value: str | None) -> str | None:
    if value is not None and not _SERVICE_NAME.fullmatch(value):
        raise ValueError("must be lowercase kebab-case, e.g. 'checkout-api'")
    return value


NonEmpty = Annotated[str, AfterValidator(_non_empty)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ServiceRef(_Strict):
    # Same naming rule as IncidentCreate.service, so extracted services match stored ones.
    name: Annotated[str | None, AfterValidator(_service_name)]
    stated_explicitly: bool  # False when the name was inferred from context


class Symptom(_Strict):
    description: NonEmpty
    started_at: str | None  # as written in the report ("14:05 UTC", "this morning"); not parsed


class ErrorCode(_Strict):
    code: NonEmpty
    kind: Literal["http_status", "exception", "error_code", "exit_code", "other"]


class SeverityCandidate(_Strict):
    level: Severity  # a suggestion for a human to confirm, not a decision
    rationale: NonEmpty


class Entity(_Strict):
    type: Literal[
        "host", "endpoint", "database", "queue", "region", "deployment", "team", "customer", "other"
    ]
    value: NonEmpty


class MissingInformation(_Strict):
    field: NonEmpty  # short snake_case label, e.g. "recent_changes"
    question: NonEmpty  # what to ask the reporter


class IncidentExtraction(_Strict):
    title: NonEmpty
    service: ServiceRef
    symptoms: list[Symptom]
    error_codes: list[ErrorCode]
    environment: Environment | None
    severity_candidate: SeverityCandidate
    entities: list[Entity]
    missing_information: list[MissingInformation]
