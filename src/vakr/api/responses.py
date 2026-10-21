"""JSON answers built from view models, and the paged wrappers (API-002, API-005, TIME-004).

Handlers return a view through ``view_response``: the view is converted
with ``vakr.query.to_jsonable`` (UUIDs as text, times as UTC ``Z``,
missing values as ``null``, never ``0``) and sent as JSON. FastAPI still
documents the declared response model in OpenAPI; it does not re-validate
the answer, which keeps large graph answers cheap.

Dependencies:
    fastapi.responses: ``JSONResponse``.
    vakr.query.jsonable: View conversion.

Safety:
    Views and API shapes carry no secrets, so nothing is filtered here.

Example:
    >>> from vakr.api.schemas import VersionInfo
    >>> view_response(VersionInfo("1.2.3")).status_code
    200
"""

import dataclasses
from collections.abc import Callable
from typing import Any

from fastapi.responses import JSONResponse

from vakr.api.paging import PageInfo, offset_cursor
from vakr.query import to_jsonable


def view_response(view: Any, status: int = 200) -> JSONResponse:  # any view or API shape
    """Answer with a view as JSON.

    Args:
        view: A dataclass view or shape.
        status: HTTP status.

    Returns:
        JSONResponse: The answer.

    Raises:
        TypeError: If the view holds an unsupported type (a programming error).
    """
    return JSONResponse(to_jsonable(view), status_code=status)


def next_offset(offset: int, limit: int, total: int) -> int | None:
    """Return where the next page starts, or ``None`` after the last one.

    Args:
        offset: This page's first row.
        limit: Page size.
        total: Rows across all pages.

    Returns:
        int | None: The next page's first row.

    Raises:
        None
    """
    following = offset + limit
    return following if following < total else None


def paged[T](
    shape: Callable[..., T], view: Any, limit: int, total: int, following: int | None
) -> T:
    """Wrap a list view into its paged shape, adding ``page``.

    Args:
        shape: ``DeviceListPage`` or ``InterfaceListPage``.
        view: The matching list view.
        limit: Page size.
        total: Rows across all pages.
        following: Next page's first row, or ``None``.

    Returns:
        T: The view's fields plus ``page``.

    Raises:
        TypeError: If ``shape`` does not extend the view's class.
    """
    values = {f.name: getattr(view, f.name) for f in dataclasses.fields(view)}
    return shape(**values, page=PageInfo(limit, total, offset_cursor(following)))
