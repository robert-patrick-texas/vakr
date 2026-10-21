"""Where ``vakr status`` and ``vakr tui`` get their views: ``StatusSource`` (TUI-007, E.29 #10).

Two sources satisfy it with the same views and the same answers:

- ``vakr.client.SocketStatusSource`` asks the running ``vakr-api`` over its
  local socket (13.1's default on the monitoring node);
- ``StatusQueries`` reads the local databases in-process, when ``vakr-api``
  is not running or ``--local`` is given.

A source that cannot answer at all (no API, a refused account, an answer
that does not fit the views) raises ``SourceUnavailableError`` with a
message that already says what to run. Storage trouble stays a
``StorageError`` from either source, and a request the source refuses is a
``ValidationError``, so callers handle both paths alike.

Dependencies:
    typing: ``Protocol``.
    vakr.query: The request and view types.

Safety:
    Read-only by construction: the protocol has no method that changes anything.

Example:
    >>> from vakr.adapters.memory import memory_uow_factory
    >>> from vakr.query import StatusQueries
    >>> isinstance(StatusQueries(memory_uow_factory(), None), StatusSource)
    True
"""

from typing import Protocol, runtime_checkable

from vakr.query.detail_views import DeviceDetailView
from vakr.query.listing import DeviceListRequest
from vakr.query.views import DeviceListView

MAX_DEVICE_KEY_LENGTH = 1024
"""Longest name or UUID ``vakr status DEVICE`` asks the API for (E.29); longer ones match none."""


class SourceUnavailableError(Exception):
    """The source could not answer; the message says why and what to run, in operator words."""


@runtime_checkable
class StatusSource(Protocol):
    """The two reads behind ``vakr status`` and ``vakr tui``."""

    def device_list(self, request: DeviceListRequest | None = None) -> DeviceListView:
        """Return one page of the device list.

        Args:
            request: Search, filter, sort, and page; the defaults when ``None``.

        Returns:
            DeviceListView: The page and everything shown around it.

        Raises:
            SourceUnavailableError: If the source cannot answer.
            StorageError: If inventory cannot be read.
            ValidationError: If the source refuses the request.
        """
        ...

    def device_detail(self, key: str) -> DeviceDetailView | None:
        """Return one device's screen.

        Args:
            key: Exact display name, else device UUID.

        Returns:
            DeviceDetailView | None: ``None`` when nothing matches.

        Raises:
            SourceUnavailableError: If the source cannot answer.
            StorageError: If inventory cannot be read.
        """
        ...
