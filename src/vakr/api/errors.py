"""The stable error envelope of API v1 (API-007, API-T03).

Every error answer has the same JSON shape::

    {"code": "not_found", "message": "no device 0190...", "request_id": "5f2c...",
     "fields": [{"field": "limit", "message": "must be at most 500"}]}

``code`` is a stable machine word, ``message`` an operator sentence,
``request_id`` the value of the ``X-Request-ID`` response header (it also
appears in the log line of a failure), and ``fields`` names the inputs that
were refused. Telemetry trouble is not an error: views report it inside a
200 answer (``available: false``, UI-014, UI-015), because storage trouble
must never read as a device or link problem (invariant 2).

The handlers that map exceptions to it are in ``vakr.api.error_handlers``.

Dependencies:
    fastapi.responses: JSON responses.

Safety:
    Bodies never carry a traceback, an exception message from a driver, or
    an input value: request-validation problems keep only the field path and
    the rule, so a password that broke a length rule is never echoed back.

Example:
    >>> ApiError(404, "not_found", "no such device").status
    404
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from vakr.api.keys import REQUEST_ID_KEY

type Responses = dict[int | str, dict[str, Any]]
"""Extra OpenAPI answers of a route, as FastAPI's ``responses=`` takes them."""
MAX_FIELDS = 20
"""Field problems listed per answer."""


@dataclass(frozen=True, slots=True)
class FieldProblem:
    """One refused input.

    Attributes:
        field: Input name, such as ``limit`` or ``body.password``.
        message: What rule it broke.
    """

    field: str
    message: str


@dataclass(frozen=True, slots=True)
class ErrorEnvelope:
    """The body of every error answer (API-007).

    Attributes:
        code: Stable machine word, such as ``not_found``.
        message: Operator sentence without secrets.
        request_id: Correlates the answer with the log.
        fields: Refused inputs, if any.
    """

    code: str
    message: str
    request_id: str
    fields: tuple[FieldProblem, ...] = ()


class ApiError(Exception):
    """An answer other than success, raised by handlers and dependencies.

    Attributes:
        status: HTTP status.
        code: Stable machine word.
        message: Operator sentence.
        fields: Refused inputs.
        headers: Extra response headers, such as ``Retry-After``.
    """

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        fields: Sequence[FieldProblem] = (),
        headers: Mapping[str, str] | None = None,
    ) -> None:
        """Create the error.

        Args:
            status: HTTP status.
            code: Stable machine word.
            message: Operator sentence without secrets.
            fields: Refused inputs.
            headers: Extra response headers.

        Returns:
            None

        Raises:
            None
        """
        super().__init__(message)
        self.status, self.code, self.message = status, code, message
        self.fields = tuple(fields)[:MAX_FIELDS]
        self.headers = dict(headers or {})


def envelope_response(
    status: int,
    code: str,
    message: str,
    request_id: str,
    *,
    fields: Sequence[FieldProblem] = (),
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """Build an error answer.

    Args:
        status: HTTP status.
        code: Stable machine word.
        message: Operator sentence.
        request_id: The request's ID.
        fields: Refused inputs.
        headers: Extra headers.

    Returns:
        JSONResponse: The envelope.

    Raises:
        None
    """
    body = {
        "code": code,
        "message": message,
        "request_id": request_id,
        "fields": [{"field": f.field, "message": f.message} for f in fields[:MAX_FIELDS]],
    }
    return JSONResponse(body, status_code=status, headers=dict(headers or {}))


def request_id_of(request: Request) -> str:
    """Return the ID the middleware gave this request.

    Args:
        request: The request.

    Returns:
        str: The ID, or ``"-"`` outside the middleware (tests of single handlers).

    Raises:
        None
    """
    return str(request.scope.get(REQUEST_ID_KEY, "-"))
