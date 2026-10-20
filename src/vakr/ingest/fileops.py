"""The OS calls the spool's durability depends on, behind one substitutable object.

Whether a record survives a power cut depends on a handful of calls: the
``write`` that puts bytes in the page cache, the ``fsync`` that forces them
to the device, and the ``fsync`` of the directory that makes a new or
renamed file's name durable. The spool makes every such call through
``FileOps``, so tests can count fsyncs (group commit, E.8) or make a write
fail with ``ENOSPC`` (disk-full backpressure) without touching a real disk.

Dependencies:
    os, pathlib: The system calls.

Safety:
    Stateless and thread-safe; the writer thread and the ingestion worker
    share one instance.

Example:
    >>> ops = FileOps()
    >>> callable(ops.fsync), callable(ops.fsync_dir)
    (True, True)
"""

import os
from pathlib import Path


class FileOps:
    """The OS calls the spool depends on for durability; stateless and thread-safe.

    Subclass in tests to observe or fail individual calls.
    """

    def write(self, fd: int, data: bytes | memoryview) -> int:
        """Write bytes to a file descriptor once.

        Args:
            fd: Open file descriptor.
            data: Bytes to write.

        Returns:
            int: Bytes written, possibly fewer than ``len(data)``.

        Raises:
            OSError: On a write error such as ``ENOSPC``.
        """
        return os.write(fd, data)

    def fsync(self, fd: int) -> None:
        """Flush a file's data and metadata to stable storage.

        Args:
            fd: Open file descriptor.

        Returns:
            None

        Raises:
            OSError: If the device reports a write error.
        """
        os.fsync(fd)

    def fsync_dir(self, path: Path) -> None:
        """Make a directory's entries (creates, renames, removals) durable.

        Args:
            path: The directory.

        Returns:
            None

        Raises:
            OSError: If the directory cannot be opened or synced.
        """
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def write_all(fd: int, data: bytes, ops: FileOps) -> None:
    """Write every byte, retrying short writes.

    Args:
        fd: Open file descriptor.
        data: Bytes to write.
        ops: File operations.

    Returns:
        None

    Raises:
        OSError: On a write error; some bytes may already be written.
    """
    view = memoryview(data)
    while view:
        view = view[ops.write(fd, view) :]
