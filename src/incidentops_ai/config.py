from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from environment variables (prefix INCIDENTOPS_) or .env."""

    model_config = SettingsConfigDict(env_prefix="INCIDENTOPS_", env_file=".env", extra="ignore")

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
    llm_max_attempts: int = 3
    llm_total_timeout_s: float = 60.0

    @field_validator("openai_api_key", "llm_model", mode="before")
    @classmethod
    def _blank_means_unset(cls, value):
        # docker compose passes VAR="" when the variable is not set; that is "not configured",
        # not an empty key to send to the provider.
        return None if isinstance(value, str) and not value.strip() else value
