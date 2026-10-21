"""Exception handlers that turn every failure into the error envelope (API-007, API-T03).

``install_handlers`` registers one handler per kind of failure:

- ``ApiError`` (raised by handlers and dependencies): its own status and code;
- FastAPI request validation: 422 ``invalid_request`` with the field path
  and rule of each problem, never the input value;
- routing errors (no such endpoint, wrong method): their status;
- domain errors: ``ValidationError`` 422, ``NotFoundError`` 404,
  ``OwnershipError`` and ``ConflictError`` 409;
- ``StorageError`` 503 ``storage_unavailable`` with a fixed message (the
  driver's words never reach a client), ``Retry-After`` when retryable;
- ``AuthBusyError`` 503 ``busy`` with ``Retry-After``.

Anything else is answered 500 by the middleware (``vakr.api.middleware``).

Dependencies:
    fastapi, starlette: Exception types.
    vakr.api.errors: The envelope.
    vakr.domain.errors, vakr.auth: The exceptions mapped here.

Safety:
    A 5xx is logged by exception type and request ID only.

Example:
    >>> _domain_answer(StorageError("database is locked", retryable=True))[:2]
    (503, 'storage_unavailable')
"""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from vakr.api.errors import (
    MAX_FIELDS,
    ApiError,
    FieldProblem,
    envelope_response,
    request_id_of,
)
from vakr.auth import AuthBusyError
from vakr.domain.errors import (
    ConflictError,
    DuplicateError,
    NotFoundError,
    OwnershipError,
    StorageError,
    ValidationError,
)

_LOG = logging.getLogger(__name__)
_LOCATIONS = frozenset({"query", "path", "header", "cookie"})
_HTTP_CODES = {
    400: "bad_request", 401: "unauthenticated", 403: "forbidden", 404: "not_found",
    405: "method_not_allowed", 406: "not_acceptable", 409: "conflict",
    413: "body_too_large", 415: "unsupported_media_type", 422: "invalid_request",
    429: "too_many_requests", 500: "internal_error", 503: "unavailable",
}  # fmt: skip


def _validation_fields(exc: RequestValidationError) -> list[FieldProblem]:
    """Turn FastAPI validation errors into field problems without input values.

    Args:
        exc: The validation error.

    Returns:
        list[FieldProblem]: Field path and rule message per problem.

    Raises:
        None
    """
    problems = []
    for error in exc.errors()[:MAX_FIELDS]:
        path = ".".join(str(part) for part in error.get("loc", ()) if part not in _LOCATIONS)
        problems.append(FieldProblem(path or "request", str(error.get("msg", "invalid"))))
    return problems


_DOMAIN: tuple[tuple[type[Exception], int, str], ...] = (
    (ValidationError, 422, "invalid_request"),
    (NotFoundError, 404, "not_found"),
    (OwnershipError, 409, "managed_by_file"),
    (ConflictError, 409, "conflict"),
    (DuplicateError, 409, "conflict"),
)
"""Domain exceptions whose own message is safe to show, with their status and code."""


def _domain_answer(exc: Exception) -> tuple[int, str, str, list[FieldProblem], dict[str, str]]:
    """Map a domain or auth exception to status, code, message, fields, and headers.

    Args:
        exc: The exception.

    Returns:
        tuple: Status, code, message, field problems, extra headers.

    Raises:
        None
    """
    if isinstance(exc, AuthBusyError):
        busy = "the server is busy checking passwords; retry shortly"
        return 503, "busy", busy, [], {"Retry-After": "5"}
    if isinstance(exc, StorageError):  # its message may name internals; a fixed one is shown
        retry = {"Retry-After": "5"} if exc.retryable else {}
        return 503, "storage_unavailable", "the metadata database is unavailable", [], retry
    for kind, status, code in _DOMAIN:
        if isinstance(exc, kind):
            field = getattr(exc, "field", None)
            fields = [FieldProblem(field, str(exc))] if field else []
            return status, code, str(exc), fields, {}
    return 500, "internal_error", "the request failed; the log names its request ID", [], {}


def install_handlers(app: FastAPI) -> None:
    """Register the envelope for every error the app can raise.

    Args:
        app: The application.

    Returns:
        None

    Raises:
        None
    """

    async def api_error(request: Request, exc: Exception) -> JSONResponse:
        """Answer an ``ApiError``.

        Args:
            request: The request.
            exc: The error.

        Returns:
            JSONResponse: The envelope.

        Raises:
            None
        """
        if not isinstance(exc, ApiError):  # registered for ApiError only
            return await domain(request, exc)
        return envelope_response(exc.status, exc.code, exc.message, request_id_of(request),
                                 fields=exc.fields, headers=exc.headers)  # fmt: skip

    async def invalid(request: Request, exc: Exception) -> JSONResponse:
        """Answer a request that failed FastAPI validation.

        Args:
            request: The request.
            exc: The error.

        Returns:
            JSONResponse: 422 with field paths only.

        Raises:
            None
        """
        fields = _validation_fields(exc) if isinstance(exc, RequestValidationError) else []
        return envelope_response(422, "invalid_request", "the request is not valid",
                                 request_id_of(request), fields=fields)  # fmt: skip

    async def http_error(request: Request, exc: Exception) -> JSONResponse:
        """Answer a routing error (no such route, wrong method).

        Args:
            request: The request.
            exc: The error.

        Returns:
            JSONResponse: The envelope with the same status.

        Raises:
            None
        """
        status = exc.status_code if isinstance(exc, StarletteHTTPException) else 500
        code = _HTTP_CODES.get(status, "error")
        message = "no such endpoint" if status == 404 else code.replace("_", " ")
        return envelope_response(status, code, message, request_id_of(request),
                                 headers=getattr(exc, "headers", None))  # fmt: skip

    async def domain(request: Request, exc: Exception) -> JSONResponse:
        """Answer a domain, storage, or auth exception.

        Args:
            request: The request.
            exc: The error.

        Returns:
            JSONResponse: The mapped envelope.

        Raises:
            None
        """
        status, code, message, fields, headers = _domain_answer(exc)
        if status >= 500:
            _LOG.warning("request %s failed: %s", request_id_of(request), type(exc).__name__,
                         extra={"event_id": "api.request_failed"})  # fmt: skip
        return envelope_response(status, code, message, request_id_of(request), fields=fields,
                                 headers=headers)  # fmt: skip

    app.add_exception_handler(ApiError, api_error)
    app.add_exception_handler(RequestValidationError, invalid)
    app.add_exception_handler(StarletteHTTPException, http_error)
    for kind in (ValidationError, NotFoundError, OwnershipError, ConflictError, DuplicateError,
                 AuthBusyError, StorageError):  # fmt: skip
        app.add_exception_handler(kind, domain)
