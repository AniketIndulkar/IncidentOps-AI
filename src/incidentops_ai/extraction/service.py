from incidentops_ai.extraction.models import IncidentExtraction
from incidentops_ai.llm.prompts import load_prompt
from incidentops_ai.llm.provider import LLMProvider, ModelConfig, StructuredResult

EXTRACTION_PROMPT = "extract_incident"
EXTRACTION_PROMPT_VERSION = "v1"


async def extract_incident(
    provider: LLMProvider, text: str, config: ModelConfig
) -> StructuredResult[IncidentExtraction]:
    """Turn a free-text incident report into typed data. Never raises for LLM failures:
    check `result.status`."""
    prompt = load_prompt(EXTRACTION_PROMPT, EXTRACTION_PROMPT_VERSION, input_text=text)
    return await provider.generate_structured(
        prompt=prompt, schema=IncidentExtraction, config=config
    )
