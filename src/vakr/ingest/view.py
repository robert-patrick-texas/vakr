"""What a spool consumer may see of the spool: a read-only view across threads (6.2).

The reader runs in ingestion's worker threads while the writer thread
appends. It needs only a few things from the spool: where it is, how far
records are durable, which segment is still open, the byte accounting to
update when it frees segments, and a way to ask for a fresh segment after
finding damage in the open one. ``SpoolView`` names exactly that, so the
reader and cursor can be tested against a stand-in.

Dependencies:
    vakr.ingest: Usage accounting and file operations.

Safety:
    Read-only apart from ``usage`` and ``request_rotation``, both thread-safe.

Example:
    >>> SpoolView.__name__
    'SpoolView'
"""

from pathlib import Path
from typing import Protocol

from vakr.ingest.capacity import SpoolUsage
from vakr.ingest.fileops import FileOps


class SpoolView(Protocol):
    """What a consumer needs from the spool it reads.

    Attributes:
        directory: The collector's spool directory.
        usage: Byte accounting, updated when segments are freed.
        ops: File operations.
    """

    directory: Path
    usage: SpoolUsage
    ops: FileOps

    @property
    def durable_seq(self) -> int:
        """Highest sequence number covered by an fsync.

        Args:
            None

        Returns:
            int: The sequence number.

        Raises:
            None
        """
        ...

    @property
    def active_first_seq(self) -> int | None:
        """First sequence number of the segment being appended to, if any.

        Args:
            None

        Returns:
            int | None: The sequence number.

        Raises:
            None
        """
        ...

    def request_rotation(self) -> None:
        """Ask the writer to start a new segment before its next append.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        ...
