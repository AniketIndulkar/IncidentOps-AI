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
