from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from environment variables (prefix INCIDENTOPS_) or .env."""

    model_config = SettingsConfigDict(env_prefix="INCIDENTOPS_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://localhost:5432/incidentops"
    database_echo: bool = False
