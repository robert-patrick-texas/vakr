"""A telemetry backend view that can only read: the API never writes telemetry (6.2).

In the Compact service mode the collector is the only writer of telemetry
(6.2). ``vakr-api`` reads the same SQLite file in WAL mode beside it, so it
hands the query services this view instead of the adapter itself: the read
ports pass through, and the batch writer, rollup writes, and retention
purges raise ``PermissionError``. A programming mistake therefore fails
loudly in the API instead of competing with the collector for the write
lock or changing history behind its back.

Dependencies:
    vakr.ports.telemetry: The backend protocol it implements.

Safety:
    Holds no state of its own; every read goes to the wrapped backend.

Example:
    >>> ReadOnlyTelemetry.__name__
    'ReadOnlyTelemetry'
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from vakr.ports.telemetry import TelemetryBackend

WRITE_REFUSED = "vakr-api reads telemetry; only the collector writes it (blueprint 6.2)"


class _ReadOnlyRollups:
    """Rollup reads only."""

    def __init__(self, inner: Any) -> None:  # a RollupRepository
        """Wrap the rollup repository.

        Args:
            inner: The backend's rollup repository.

        Returns:
            None

        Raises:
            None
        """
        self._inner = inner

    def write(self, _buckets: Sequence[Any]) -> int:
        """Refuse a rollup write.

        Args:
            _buckets: Ignored.

        Returns:
            int: Never.

        Raises:
            PermissionError: Always.
        """
        raise PermissionError(WRITE_REFUSED)

    def read(self, *args: Any, **kwargs: Any) -> Any:
        """Read buckets from the wrapped repository.

        Args:
            *args: As ``RollupRepository.read``.
            **kwargs: As ``RollupRepository.read``.

        Returns:
            Any: The buckets.

        Raises:
            Exception: Whatever the repository raises.
        """
        return self._inner.read(*args, **kwargs)

    def coverage_end(self) -> datetime | None:
        """Return where rollup coverage ends.

        Args:
            None

        Returns:
            datetime | None: The latest bucket end.

        Raises:
            Exception: Whatever the repository raises.
        """
        return self._inner.coverage_end()


class _ReadOnlyRetention:
    """Retention policy and horizon only; never a purge."""

    def __init__(self, inner: Any) -> None:  # a RetentionManager
        """Wrap the retention manager.

        Args:
            inner: The backend's retention manager.

        Returns:
            None

        Raises:
            None
        """
        self._inner = inner

    @property
    def policy(self) -> Any:
        """Return the retention policy.

        Args:
            None

        Returns:
            Any: The policy.

        Raises:
            Exception: Whatever the manager raises.
        """
        return self._inner.policy

    def native_horizon(self) -> datetime | None:
        """Return the oldest native time still kept.

        Args:
            None

        Returns:
            datetime | None: The horizon.

        Raises:
            Exception: Whatever the manager raises.
        """
        return self._inner.native_horizon()

    def apply(self, _now: datetime) -> Any:
        """Refuse a retention pass.

        Args:
            _now: Ignored.

        Returns:
            Any: Never.

        Raises:
            PermissionError: Always.
        """
        raise PermissionError(WRITE_REFUSED)


class ReadOnlyTelemetry:
    """Every read port of a telemetry backend; no writer."""

    def __init__(self, inner: TelemetryBackend) -> None:
        """Wrap a backend view.

        Args:
            inner: The adapter's backend view.

        Returns:
            None

        Raises:
            None
        """
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        """Pass the read ports through: capabilities, query, current, rollup source, health.

        Args:
            name: Attribute name.

        Returns:
            Any: The wrapped attribute, read-only where it could write.

        Raises:
            PermissionError: For the batch writer and ``maintain``.
            AttributeError: For anything the backend does not have.
        """
        if name in ("writer", "maintain"):
            raise PermissionError(WRITE_REFUSED)
        value = getattr(self._inner, name)
        if name == "rollups":
            return _ReadOnlyRollups(value)
        if name == "retention":
            return _ReadOnlyRetention(value)
        return value
