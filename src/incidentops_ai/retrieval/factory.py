"""Builds the embedder the app uses, from Settings."""

from incidentops_ai.config import Settings
from incidentops_ai.errors import LLMNotConfiguredError
from incidentops_ai.retrieval.embeddings import OpenAIEmbedder


def build_embedder(settings: Settings) -> OpenAIEmbedder:
    """Real embedder. Tests use FakeEmbedder instead of calling this."""
    if settings.openai_api_key is None:
        raise LLMNotConfiguredError("OPENAI_API_KEY is not set.")
    return OpenAIEmbedder(
        settings.openai_api_key.get_secret_value(),
        model=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
        timeout_s=settings.llm_timeout_s,
    )
