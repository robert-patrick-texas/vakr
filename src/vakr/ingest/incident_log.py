"""The incident log of one spool directory: durable, bounded, and never silent (STORE-007).

Each incident is logged at ERROR with the stable event ID ``spool.loss``,
then appended to ``incidents.jsonl`` and fsynced. Past ``MAX_LOG_BYTES`` the
file is rotated to ``incidents.1.jsonl``, keeping one older generation, so
it cannot fill the disk. When the disk is already full the incident still
reaches the journal and is kept in memory (at most ``MAX_UNSAVED``) for
health to report. A finding that recovery sees again after a restart, such
as a damaged segment still waiting in the spool, is recorded only once.

Dependencies:
    threading: The writer thread and the ingestion worker both record.
    vakr.ingest.incidents: The incident record and the reader.
    vakr.ingest.fileops: Writes and fsyncs.

Safety:
    Incidents hold counts, sequence numbers, times, and file names, never
    observation payloads.

Example:
    >>> IncidentLog.__name__
    'IncidentLog'
"""

import json
import logging
import os
import threading
from collections import deque
from pathlib import Path

from vakr.ingest.fileops import FileOps, write_all
from vakr.ingest.incidents import (
    LOG_FILE,
    MAX_LOG_BYTES,
    OLD_LOG_FILE,
    LossIncident,
    read_incidents,
)

MAX_UNSAVED = 100
"""Incidents kept in memory when they cannot be written (disk full)."""

_LOG = logging.getLogger(__name__)


class IncidentLog:
    """The append-only incident file of one spool directory.

    Thread-safe: the writer thread and the ingester's worker thread both
    record incidents.
    """

    def __init__(self, directory: Path, ops: FileOps) -> None:
        """Bind the log to a spool directory and load the keys already recorded.

        Args:
            directory: The collector's spool directory.
            ops: File operations.

        Returns:
            None

        Raises:
            None
        """
        self._dir, self._ops = directory, ops
        self._lock = threading.Lock()
        self._unsaved: deque[LossIncident] = deque(maxlen=MAX_UNSAVED)
        self._keys = {k for i in read_incidents(directory) if (k := i.key) is not None}
        self.recorded = 0

    @property
    def unsaved(self) -> tuple[LossIncident, ...]:
        """Incidents that could not be written to disk, newest last.

        Args:
            None

        Returns:
            tuple[LossIncident, ...]: Up to ``MAX_UNSAVED`` incidents.

        Raises:
            None
        """
        return tuple(self._unsaved)

    def record(self, incident: LossIncident) -> bool:
        """Log, persist, and fsync an incident unless its key was recorded before.

        Args:
            incident: The incident.

        Returns:
            bool: ``False`` if an incident with the same key already exists.

        Raises:
            None
        """
        with self._lock:
            key = incident.key
            if key is not None and key in self._keys:
                return False
            if key is not None:
                self._keys.add(key)
            self.recorded += 1
            self._persist(incident)
        return True

    def _persist(self, incident: LossIncident) -> None:
        """Log an incident and append it to disk, keeping it in memory if that fails.

        Args:
            incident: The incident.

        Returns:
            None

        Raises:
            None
        """
        _LOG.error(
            "telemetry loss (%s): %s observation(s)%s, sequence %s-%s, classes %s: %s",
            incident.kind.value, incident.estimated, "" if incident.exact else " (estimate)",
            incident.first_seq, incident.last_seq, ",".join(incident.classes) or "unknown",
            incident.detail, extra={"event_id": "spool.loss", "incident": incident.incident_id},
        )  # fmt: skip
        try:
            self._append(json.dumps(incident.to_dict(), sort_keys=True) + "\n")
        except OSError as exc:
            self._unsaved.append(incident)
            _LOG.error("cannot save telemetry-loss incident %s: %s", incident.incident_id,
                       type(exc).__name__, extra={"event_id": "spool.loss_unsaved"})  # fmt: skip

    def _append(self, line: str) -> None:
        """Append one line durably, rotating the file when it is too large.

        Args:
            line: One JSON line.

        Returns:
            None

        Raises:
            OSError: If the file cannot be written or synced.
        """
        path = self._dir / LOG_FILE
        if path.exists() and path.stat().st_size >= MAX_LOG_BYTES:
            path.replace(self._dir / OLD_LOG_FILE)
            self._ops.fsync_dir(self._dir)
        created = not path.exists()
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o640)
        try:
            write_all(fd, line.encode(), self._ops)
            self._ops.fsync(fd)
        finally:
            os.close(fd)
        if created:
            self._ops.fsync_dir(self._dir)
