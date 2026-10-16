from dataclasses import dataclass

from incidentops_ai.extraction.models import IncidentExtraction
from incidentops_ai.incidents.models import Environment, Severity
from incidentops_ai.llm.prompts import load_prompt
from incidentops_ai.llm.provider import LLMProvider, ModelConfig, StructuredResult

EXTRACTION_PROMPT = "extract_incident"
EXTRACTION_PROMPT_VERSION = "v2"


@dataclass(frozen=True)
class RecordedFields:
    """What the reporter already filled in on the incident. Every value is an enum or a validated
    service name, so it is safe to put in the prompt as-is; free text never goes in here."""

    service: str
    severity: Severity
    environment: Environment


def _model_input(text: str, recorded: RecordedFields | None) -> str:
    if recorded is None:
        return text
    return (
        "Recorded on the incident by the reporter:\n"
        f"- service: {recorded.service}\n"
        f"- severity: {recorded.severity}\n"
        f"- environment: {recorded.environment}\n"
        "\n"
        f"Report:\n{text}"
    )


async def extract_incident(
    provider: LLMProvider,
    text: str,
    config: ModelConfig,
    *,
    recorded: RecordedFields | None = None,
) -> StructuredResult[IncidentExtraction]:
    """Turn a free-text incident report into typed data. Never raises for LLM failures:
    check `result.status`. `recorded`: fields already stored on the incident, so the model does
    not report them as "not stated"."""
    prompt = load_prompt(
        EXTRACTION_PROMPT, EXTRACTION_PROMPT_VERSION, input_text=_model_input(text, recorded)
    )
    return await provider.generate_structured(
        prompt=prompt, schema=IncidentExtraction, config=config
    )
