"""Durable spool and idempotent ingestion: observations survive until stored (6.1, ADR-008).

Between polling and the telemetry database sits a per-collector spool of
append-only segment files (``/var/lib/vakr/spool/<collector UUID>/``). A
collector awaits ``Spool.append``; it returns once the observations are
fsynced (group commit, E.8), so a power cut costs at most about one second
of telemetry, and recovery records even that as a telemetry-loss incident
(STORE-007). An ``Ingester`` reads committed records in order, writes them in
batches through ``ObservationWriter.write_batch``, and acknowledges whole
batches by advancing a durable consumer offset; replay after any crash is
idempotent (STORE-009). If the database is down, polling continues while
the spool has room (STORE-014); a full spool pauses normal polling instead
of losing data (6.4).

Module map: ``spool`` (append API), ``writer_thread``/``segment_writer``/
``commit`` (group commit), ``recovery`` (startup), ``reader``/``cursor``
(consumption), ``ingester``/``ingest_steps``/``batching`` (database writes),
``status``/``inspect`` (health), ``framing``/``segments``/``scan`` (format).

Dependencies:
    vakr.domain.envelope: The record payload, unchanged (ARCH-002).
    vakr.ports.telemetry: The only way observations reach storage.

Safety:
    No SQL, no driver types, and no blocking call on the event loop.

Example:
    >>> from vakr.ingest import Ingester, Spool, SpoolSettings
    >>> Spool.__name__, Ingester.__name__, SpoolSettings.__name__
    ('Spool', 'Ingester', 'SpoolSettings')
"""

from vakr.ingest.capacity import CapacityState
from vakr.ingest.errors import (
    SpoolClosedError,
    SpoolError,
    SpoolFullError,
    SpoolLockedError,
    SpoolWriteError,
)
from vakr.ingest.incidents import LossIncident, LossKind, read_incidents
from vakr.ingest.ingester import Ingester
from vakr.ingest.recovery import Recovery
from vakr.ingest.requests import AppendReceipt
from vakr.ingest.settings import SpoolSettings
from vakr.ingest.spool import Spool
from vakr.ingest.status import SpoolHealth, collector_state

__all__ = [
    "AppendReceipt",
    "CapacityState",
    "Ingester",
    "LossIncident",
    "LossKind",
    "Recovery",
    "Spool",
    "SpoolClosedError",
    "SpoolError",
    "SpoolFullError",
    "SpoolHealth",
    "SpoolLockedError",
    "SpoolSettings",
    "SpoolWriteError",
    "collector_state",
    "read_incidents",
]
