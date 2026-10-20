"""Errors the spool raises to producers: explicit refusals, never silent drops (6.4).

A collector that gets one of these knows its observations are *not*
durable. The spool has already recorded a telemetry-loss incident for them
(STORE-007), so the caller only has to stop or slow down; it must not report
the targets as down, because the trouble is the collector's (invariant 2).

Dependencies:
    vakr.domain.errors: ``StorageError``, which carries the retryable flag.

Safety:
    Messages name the spool directory, sizes, and counts, never payloads.

Example:
    >>> err = SpoolFullError("spool full")
    >>> isinstance(err, SpoolError), err.retryable
    (True, True)
"""

from vakr.domain.errors import StorageError


class SpoolError(StorageError):
    """The spool could not make observations durable."""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        """Create the error.

        Args:
            message: Operator-readable explanation.
            retryable: Whether the same append could succeed later.

        Returns:
            None

        Raises:
            None
        """
        super().__init__(message, retryable=retryable)


class SpoolFullError(SpoolError):
    """The spool reached its size limit or the filesystem is full (``ENOSPC``)."""


class SpoolWriteError(SpoolError):
    """A segment write or fsync failed for a reason other than space."""


class SpoolClosedError(SpoolError):
    """The spool is not open, or is closing; nothing more is accepted."""

    def __init__(self, message: str = "the spool is closed") -> None:
        """Create the error.

        Args:
            message: Operator-readable explanation.

        Returns:
            None

        Raises:
            None
        """
        super().__init__(message, retryable=False)


class SpoolLockedError(SpoolError):
    """Another process holds the spool directory (one producer per spool, 6.2)."""

    def __init__(self, message: str) -> None:
        """Create the error.

        Args:
            message: Operator-readable explanation naming the directory.

        Returns:
            None

        Raises:
            None
        """
        super().__init__(message, retryable=False)
