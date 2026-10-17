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


class RetrievalFailedError(DomainError):
    """The evidence for an analysis could not be fetched (embedding or reranking failed)."""

    code = "retrieval_failed"


class AttachmentRejectedError(DomainError):
    """An uploaded file was refused. The subclass decides the HTTP status; `code` says why."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class UnsupportedMediaError(AttachmentRejectedError):
    """Not a PNG, JPEG or PDF, judged by the bytes."""


class AttachmentTooLargeError(AttachmentRejectedError):
    """Over the size or pixel limit."""


class InvalidAttachmentError(AttachmentRejectedError):
    """Unreadable, or too many pages."""


class ExtractionUnavailableError(DomainError):
    """The extractor a file needs is not installed or failed (for example OCR without its extra)."""

    code = "extraction_unavailable"


class WorkflowRunNotFoundError(NotFoundError):
    code = "workflow_run_not_found"


class WorkflowNotResumableError(DomainError):
    """Only a run that failed part way can be resumed."""

    code = "workflow_not_resumable"


class WorkflowNotAwaitingDecisionError(DomainError):
    """A decision was sent for a run that is not paused at the approval gate (AIENG-49)."""

    code = "workflow_not_awaiting_decision"


class MemoryNotFoundError(NotFoundError):
    code = "memory_not_found"


class MemoryAlreadySupersededError(DomainError):
    """A note has one successor: correct the current note, not an old one (AIENG-50)."""

    code = "memory_already_superseded"


class WorkflowFailedError(DomainError):
    """A workflow run stopped part way. Its checkpoints are kept; it can be resumed by id."""

    code = "workflow_failed"

    def __init__(self, run_id: object) -> None:
        super().__init__(
            f"The workflow run {run_id} stopped part way. Its progress was saved; resume it with "
            f"POST .../triage/{run_id}/resume."
        )
        self.run_id = run_id


class UnauthorizedError(DomainError):
    """The caller did not present the token this endpoint requires."""

    code = "unauthorized"


class RetryLaterError(DomainError):
    """Refused for now; the same request can succeed after `retry_after_s` seconds."""

    def __init__(self, message: str, retry_after_s: float) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


class RateLimitExceededError(RetryLaterError):
    code = "rate_limit_exceeded"


class RequestInProgressError(RetryLaterError):
    """Another request with the same Idempotency-Key is still running."""

    code = "request_in_progress"


class WorkflowAbandonedError(WorkflowFailedError):
    """A run failed again after its last allowed resume (AIENG-51). It is not resumed further."""

    code = "workflow_abandoned"

    def __init__(self, run_id: object) -> None:
        DomainError.__init__(
            self,
            f"The workflow run {run_id} failed again after its last allowed resume and was "
            "abandoned. Start a new run.",
        )
        self.run_id = run_id
