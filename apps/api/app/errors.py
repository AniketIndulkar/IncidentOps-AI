"""Uniform error responses: RFC 9457 problem details (application/problem+json) plus a `code`.

Rules:
- Every error body has the same shape, so clients need one parser.
- `code` is stable and machine-readable; `title`/`detail` are for humans.
- Never echo submitted values (incident text may hold secrets/PII) or internal details
  (stack traces, DSNs, SQL) back to the client. Those go to server logs only.
"""

import logging
import math
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.exc import DBAPIError
from starlette.exceptions import HTTPException as StarletteHTTPException

from incidentops_ai.errors import (
    ConflictError,
    DomainError,
    LLMCallFailedError,
    LLMNotConfiguredError,
    NotFoundError,
    RateLimitExceededError,
    RequestInProgressError,
    RetrievalFailedError,
    RetryLaterError,
    UnauthorizedError,
)

logger = logging.getLogger(__name__)

PROBLEM_JSON = "application/problem+json"


class FieldError(BaseModel):
    loc: list[str | int]
    message: str
    type: str


class Problem(BaseModel):
    """Error body. Documented in OpenAPI so clients can generate types for it."""

    type: str = "about:blank"
    title: str
    status: int
    detail: str | None = None
    instance: str | None = None
    code: str
    errors: list[FieldError] | None = None


def problem_response(
    request: Request,
    status: int,
    code: str,
    detail: str | None = None,
    errors: list[FieldError] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = Problem(
        title=HTTPStatus(status).phrase,
        status=status,
        detail=detail,
        instance=request.url.path,
        code=code,
        errors=errors,
    )
    return JSONResponse(
        body.model_dump(exclude_none=True),
        status_code=status,
        media_type=PROBLEM_JSON,
        headers=headers,
    )


# Domain error class -> HTTP status. Most specific class wins (checked via the MRO).
_DOMAIN_STATUS: dict[type[DomainError], int] = {
    NotFoundError: 404,
    # 422, not 409: the request is well-formed but semantically invalid for this key
    # (matches the IETF Idempotency-Key draft). 409 is reserved for "retry later" conflicts.
    ConflictError: 422,
    LLMNotConfiguredError: 503,
    RetrievalFailedError: 502,  # a dependency (embedding API, reranker) failed, not the caller
    UnauthorizedError: 401,
    RateLimitExceededError: 429,
    RequestInProgressError: 409,  # the "retry later" conflict the 422 comment above leaves free
}

# Why an LLM call failed decides the HTTP status, so clients know whether retrying can help.
_LLM_STATUS = {
    "timed_out": 504,  # provider too slow; retrying later may work
    "rate_limited": 503,
    "provider_error": 502,
    "invalid_output": 502,  # the model answered, but not in a usable form
    "truncated": 502,
    "refused": 422,  # the model declined this content; retrying the same text will not help
    "rejected": 500,  # our own configuration is wrong (key, model, quota)
}

_HTTP_CODES = {404: "not_found", 405: "method_not_allowed"}

DEFAULT_RETRY_AFTER_S = 30  # for a rate limit where the provider gave no hint


async def _domain_error(request: Request, exc: DomainError) -> JSONResponse:
    status = next((_DOMAIN_STATUS[cls] for cls in type(exc).__mro__ if cls in _DOMAIN_STATUS), 400)
    headers = None
    if isinstance(exc, UnauthorizedError):
        headers = {"WWW-Authenticate": "Bearer"}
    elif isinstance(exc, RetryLaterError):
        # Whole seconds, rounded up, never 0: "retry now" is not a wait.
        headers = {"Retry-After": str(max(1, math.ceil(exc.retry_after_s)))}
    return problem_response(request, status, exc.code, exc.message, headers=headers)


async def _llm_call_failed(request: Request, exc: LLMCallFailedError) -> JSONResponse:
    headers = None
    if exc.status == "rate_limited":
        wait = DEFAULT_RETRY_AFTER_S if exc.retry_after_s is None else exc.retry_after_s
        # Whole seconds, rounded up: telling a client "0" for a 0.4 s wait invites an instant retry.
        headers = {"Retry-After": str(math.ceil(wait))}
    return problem_response(
        request, _LLM_STATUS.get(exc.status, 502), exc.code, exc.message, headers=headers
    )


async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Pydantic errors include "input" (the submitted value) and "ctx"; drop both on purpose.
    errors = [
        FieldError(loc=list(e["loc"]), message=e["msg"], type=e["type"]) for e in exc.errors()
    ]
    return problem_response(
        request, 422, "validation_error", "The request is invalid.", errors=errors
    )


async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = _HTTP_CODES.get(exc.status_code, f"http_{exc.status_code}")
    return problem_response(request, exc.status_code, code, headers=exc.headers)


async def _database_error(request: Request, exc: DBAPIError | OSError) -> JSONResponse:
    logger.exception("Database error on %s %s", request.method, request.url.path)
    return problem_response(
        request,
        503,
        "database_unavailable",
        "The incident store is temporarily unavailable. Retry shortly; resend POSTs with the "
        "same Idempotency-Key so a retry cannot create a duplicate.",
        headers={"Retry-After": "5"},
    )


async def _unhandled_error(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return problem_response(request, 500, "internal_error", "An unexpected error occurred.")


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(LLMCallFailedError, _llm_call_failed)
    app.add_exception_handler(DomainError, _domain_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    # DBAPIError: SQLAlchemy-wrapped driver errors. OSError: connection refused/reset before
    # the driver could wrap it (e.g. Postgres down when the pool opens a connection).
    app.add_exception_handler(DBAPIError, _database_error)
    app.add_exception_handler(OSError, _database_error)
    app.add_exception_handler(Exception, _unhandled_error)


ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status: {"model": Problem, "content": {PROBLEM_JSON: {}}} for status in (422, 500, 503)
}
