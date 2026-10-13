"""Domain errors. Each carries a stable machine-readable `code`; the API layer maps them to HTTP.

Codes are part of the public contract: clients branch on `code`, never on message text.
"""


class DomainError(Exception):
    code: str = "domain_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotFoundError(DomainError):
    code = "not_found"


class IncidentNotFoundError(NotFoundError):
    code = "incident_not_found"


class ConflictError(DomainError):
    code = "conflict"


class IdempotencyKeyReusedError(ConflictError):
    """Same Idempotency-Key was sent with a different request body."""

    code = "idempotency_key_reused"


class LLMNotConfiguredError(DomainError):
    """No API key or model is configured, so LLM features are switched off."""

    code = "llm_not_configured"


class LLMCallFailedError(DomainError):
    """An LLM operation ended without a usable result. The run is recorded; `status` says why."""

    def __init__(
        self, status: str, run_id: object, operation: str, retry_after_s: float | None = None
    ) -> None:
        super().__init__(
            f"{operation} did not complete ({status}). Run {run_id} was recorded; no partial "
            "result was stored."
        )
        self.status = status
        self.code = f"llm_{status}"
        self.run_id = run_id
        self.retry_after_s = retry_after_s
