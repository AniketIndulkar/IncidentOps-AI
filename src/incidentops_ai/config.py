from pathlib import Path
from typing import Literal

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
    # W04: dense + BM25 fused by reciprocal rank beat dense alone by the rule fixed in advance; the
    # reranker did not earn its cost (evals/retrieval/runs/2026-10-16-hybrid-findings.md). It needs
    # the optional "rerank" extra: uv sync --extra rerank.
    retrieval_stack: Literal["dense", "hybrid", "hybrid-rerank", "bm25"] = "hybrid"
    # W12: "simulated" answers with the latencies and token counts of real runs, for load tests.
    llm_provider: Literal["openai", "simulated"] = "openai"
    # W12: a run still "running" this long after its last update belonged to a process that died.
    stale_run_after_s: float = 900
    # W13: how often the API looks for such runs (also once at startup).
    stale_run_sweep_s: float = 60
    # W12: connection pools, configurable. Doubling them made no measurable difference under load
    # (the bottleneck was CPU on one event loop), so the defaults stay where they were.
    database_pool_size: int = Field(default=5, ge=1)
    database_max_overflow: int = Field(default=10, ge=0)
    checkpoint_pool_size: int = Field(default=5, ge=1)
    simulated_latency_scale: float = 1.0

    # Knowledge graph (W05). With a URI and a password the API reads service context from Neo4j.
    # graph_retrieval: "off" (default; the W05 comparison found no gain), "anchor" (the incident's
    # service's own documents, fused in) or "graph" (documents the graph links to that service).
    neo4j_uri: str | None = None
    neo4j_user: str = "neo4j"
    neo4j_password: SecretStr | None = None
    graph_retrieval: Literal["off", "anchor", "graph"] = "off"

    # Ticket system (W08). With both set, the API starts the ticket MCP server and an approved
    # workflow run opens its ticket there. The secret signs approvals; the model never sees it.
    tickets_db: Path | None = None
    approval_secret: SecretStr | None = None
    # Jira-style ticket API (W09). When set, it is the ticket system (it wins over the MCP server).
    jira_url: str | None = None
    jira_token: SecretStr | None = None
    jira_project: str = "OPS"
    # The ops database for text-to-SQL (W09): a login granted only incidentops_ops_reader.
    # ops_database_url is the default tenant's; ops_database_urls (JSON) maps every tenant to its
    # own reader login (W10), whose rows row-level security admits.
    ops_database_url: str | None = None
    ops_database_urls: dict[str, str] = {}
    # The legacy CMDB (W09, SOAP): the system of record for who owns a service.
    cmdb_url: str | None = None
    cmdb_api_key: SecretStr | None = None
    # W11 gateway: per-task model routes, e.g. {"diagnosis": ["gpt-6-luna", "gpt-6-mini"]}; a task
    # without a route uses llm_model. Optional calls-per-minute cap per model.
    llm_routes: dict[str, list[str]] = {}
    # W11 tracing: a JSONL file of spans, and/or Langfuse (host and both keys).
    trace_file: Path | None = None
    langfuse_host: str | None = None
    langfuse_public_key: str | None = None
    langfuse_secret_key: SecretStr | None = None
    llm_per_model_per_minute: int | None = None
    llm_max_attempts: int = 3
    llm_total_timeout_s: float = 60.0

    # Who may use the LLM endpoints, which spend money on every call. Without a token they stay
    # off (503) even when an OpenAI key is set: a key alone must never open them to everyone.
    api_token: SecretStr | None = None
    # W10: bearer JWTs from the identity provider. Exactly one key: an HS256 secret or an RS256
    # public key (PEM). Tokens must name this issuer and the audience "incidentops".
    jwt_secret: SecretStr | None = None
    jwt_public_key: str | None = None
    jwt_issuer: str = "incidentops-dev"
    # Calls per minute across all callers, per process (see app.rate_limit).
    llm_rate_limit_per_minute: int = Field(default=10, ge=1)

    @field_validator(
        "openai_api_key",
        "llm_model",
        "api_token",
        "neo4j_uri",
        "neo4j_password",
        "tickets_db",
        "approval_secret",
        "jira_url",
        "jira_token",
        "ops_database_url",
        "cmdb_url",
        "cmdb_api_key",
        "jwt_secret",
        "jwt_public_key",
        mode="before",
    )
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
