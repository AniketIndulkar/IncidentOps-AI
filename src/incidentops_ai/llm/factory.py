"""Builds the provider and model config the app uses, from Settings."""

from incidentops_ai.config import Settings
from incidentops_ai.errors import LLMNotConfiguredError
from incidentops_ai.llm.openai_provider import OpenAIProvider
from incidentops_ai.llm.provider import LLMProvider, ModelConfig
from incidentops_ai.llm.retry import RetryingProvider


def build_provider(settings: Settings) -> LLMProvider:
    """Real provider with bounded retry. Tests use FakeProvider instead of calling this."""
    if settings.openai_api_key is None:
        raise LLMNotConfiguredError("OPENAI_API_KEY is not set.")
    return RetryingProvider(OpenAIProvider(settings.openai_api_key.get_secret_value()))


def build_model_config(settings: Settings) -> ModelConfig:
    if not settings.llm_model:
        raise LLMNotConfiguredError("INCIDENTOPS_LLM_MODEL is not set.")
    return ModelConfig(
        model=settings.llm_model,
        max_output_tokens=settings.llm_max_output_tokens,
        timeout_s=settings.llm_timeout_s,
        max_attempts=settings.llm_max_attempts,
        total_timeout_s=settings.llm_total_timeout_s,
    )
