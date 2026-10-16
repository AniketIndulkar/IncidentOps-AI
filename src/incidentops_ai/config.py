from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_API_TOKEN_LENGTH = 16


class Settings(BaseSettings):
    """Runtime configuration, read from environment variables (prefix INCIDENTOPS_) or .env."""

    # hide_input_in_errors: a rejected value (a too-short token, a database URL with its password)
    # must not be printed back in the startup error.
    model_config = SettingsConfigDict(
        env_prefix="INCIDENTOPS_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )

    database_url: str = "postgresql+asyncpg://localhost:5432/incidentops"
    database_echo: bool = False

    # Standard OpenAI variable name (no INCIDENTOPS_ prefix). SecretStr keeps it out of logs
    # and reprs; call .get_secret_value() only where the client is built.
    openai_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("OPENAI_API_KEY")
    )
    # No default model on purpose: the choice is explicit and recorded with every run.
    llm_model: str | None = None
    llm_max_output_tokens: int = 2000
    llm_timeout_s: float = 30.0

    # Retrieval (W03). Vectors from different models or sizes cannot be compared, so both are
    # stored with the index; changing either means re-embedding the corpus.
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = Field(default=1536, ge=1)
    llm_max_attempts: int = 3
    llm_total_timeout_s: float = 60.0

    # Who may use the LLM endpoints, which spend money on every call. Without a token they stay
    # off (503) even when an OpenAI key is set: a key alone must never open them to everyone.
    api_token: SecretStr | None = None
    # Calls per minute across all callers, per process (see app.rate_limit).
    llm_rate_limit_per_minute: int = Field(default=10, ge=1)

    @field_validator("openai_api_key", "llm_model", "api_token", mode="before")
    @classmethod
    def _blank_means_unset(cls, value):
        # docker compose passes VAR="" when the variable is not set; that is "not configured",
        # not an empty key to send to the provider.
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("api_token")
    @classmethod
    def _token_must_resist_guessing(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and len(value.get_secret_value()) < MIN_API_TOKEN_LENGTH:
            raise ValueError(
                f"must be at least {MIN_API_TOKEN_LENGTH} characters (generate: openssl rand -hex 24)"
            )
        return value
