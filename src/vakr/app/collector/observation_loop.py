"""The collector's observation pull from the other position, every 30 s (MPOS-004, E.13, ADR-024).

The collector owns the telemetry writes (E.30 #12), so it pulls: at start
and then every ``PULL_INTERVAL_S``, in a worker thread, never on the event
loop. A peer pulls from ``node.primary_url``, a primary from
``node.peer_url``; a primary without one replicates nothing. The token
file and egress settings are read at each attempt, like the metadata pull.

Pacing (E.13): a pass stores at most ``PASS_BUDGET`` observations. While
the local spool holds more than ``BACKLOG_RECORDS`` unacknowledged
records, a pass stores at most half of what local ingestion stored since
the previous pass, so backfill never takes more than half the writer from
local ingestion and stops while local ingestion makes no progress.

On a primary, a partner collector is recorded in metadata the first time
its observations are pulled, so the views can name its position; a peer
receives the primary's collector with the metadata it pulls.

Dependencies:
    vakr.replication: The client, token file, and puller.
    vakr.cli.cmd_tls: The shared outbound HTTPS settings.

Safety:
    A failed pull is node health, logged and recorded; polling and the
    device views are untouched (MPOS-006).

Example:
    >>> PULL_INTERVAL_S, PASS_BUDGET
    (30.0, 50000)
"""

import asyncio
import contextlib
import logging
from functools import partial
from typing import Any

from vakr.config.node import node_settings
from vakr.domain.catalog import Collector
from vakr.domain.enums import ManagedByKind
from vakr.domain.errors import ValidationError
from vakr.domain.ownership import ManagedBy
from vakr.ports.metadata.unit_of_work import MetadataUnitOfWorkFactory
from vakr.replication import observation_status as rec
from vakr.replication.client import PartnerClient
from vakr.replication.observation_pull import ObservationPuller, ObservationPullError
from vakr.replication.token_file import TokenFileError, read_token, token_path

_LOG = logging.getLogger(__name__)
PULL_INTERVAL_S = 30.0
PASS_BUDGET = 50_000
"""Observations one pass stores at most; a 30-minute partition at MVP scale is about 57,000."""
BACKLOG_RECORDS = 10_000
"""Unacknowledged local spool records above which backfill yields to local ingestion."""
PARTNER_OWNER = ManagedBy(ManagedByKind.API, "replication")


def register_partner(uow_factory: MetadataUnitOfWorkFactory, source: dict[str, Any]) -> None:
    """Record a partner collector in metadata, once (the primary only).

    Args:
        uow_factory: Metadata units of work.
        source: The collector as the partner described it.

    Returns:
        None

    Raises:
        ValidationError: If its name is another collector's, or a label is unacceptable.
        StorageError: If the metadata database is unavailable.
    """
    with uow_factory() as uow:
        if uow.collectors.get(source["id"]) is not None:
            return
        name = source["name"] or f"partner-{str(source['id'])[:8]}"
        if uow.collectors.get_by_name(name) is not None:
            raise ValidationError(f"the partner's collector name {name!r} is taken here")
        uow.collectors.add(Collector(source["id"], name, source["position"] or "peer",
                                     PARTNER_OWNER))  # fmt: skip
        uow.commit()


class _Pacer:
    """Turns local ingestion progress into the next pass's budget (E.13)."""

    def __init__(self) -> None:
        """Start with no progress seen.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self._seen: int | None = None

    def budget(self, service: Any) -> int:
        """Return how many observations the next pass may store.

        Args:
            service: The running collector service.

        Returns:
            int: ``PASS_BUDGET``, or half of local progress while the spool is backlogged.

        Raises:
            None
        """
        status = service.ingester.status
        backlog = max(service.parts.spool.durable_seq - status.acked_seq, 0)
        progress = 0 if self._seen is None else max(status.inserted - self._seen, 0)
        self._seen = status.inserted
        return PASS_BUDGET if backlog <= BACKLOG_RECORDS else min(progress // 2, PASS_BUDGET)


def _pull(service: Any, pacer: _Pacer) -> int:
    """Build the puller from current files and pull once (blocking).

    Args:
        service: The running collector service.
        pacer: The pacing state.

    Returns:
        int: Observations stored for the first time.

    Raises:
        ObservationPullError: When the pull failed (recorded).
    """
    from vakr.cli.cmd_tls import build_egress_settings

    parts = service.parts
    node, record = node_settings(parts.config), parts.paths.state_dir / rec.FILE
    writer = parts.telemetry.writer
    problem = None if hasattr(writer, "write_replicated") else "this telemetry backend cannot " \
        "store replicated observations"  # fmt: skip
    try:
        token = read_token(token_path(parts.paths.secrets_dir))
        client = PartnerClient(
            node.partner_url, token, build_egress_settings(parts.paths, parts.config)
        )
    except (TokenFileError, OSError, ValueError) as exc:
        problem = problem or str(exc)
    if problem is not None:
        status = rec.read_status(record) or rec.blank()
        status.last_attempt_at = rec.stamp(service.clock.now_utc())
        raise ObservationPullError(rec.record_failure(record, status, problem))
    register = None if node.is_peer else partial(register_partner, parts.uow_factory)
    puller = ObservationPuller(client, writer, record, service.clock, parts.collector.id,
                               register)  # fmt: skip
    return puller.pull_once(pacer.budget(service))


async def observation_pull_loop(service: Any) -> None:
    """Pull the partner's observations until the collector stops; idle without a partner.

    Args:
        service: The running collector service (``parts``, ``clock``, ``loops_stop``).

    Returns:
        None

    Raises:
        None
    """
    stop: asyncio.Event = service.loops_stop
    if not node_settings(service.parts.config).partner_url:
        await stop.wait()
        return
    pacer = _Pacer()
    while not stop.is_set():
        try:
            stored = await asyncio.to_thread(_pull, service, pacer)
            if stored:
                _LOG.info("stored %d observations from the other position", stored,
                          extra={"event_id": "replication.observations"})  # fmt: skip
        except (ObservationPullError, OSError) as exc:
            # OSError too: a state file that cannot be written must not end replication.
            _LOG.warning("observation pull from the other position failed: %s", exc,
                         extra={"event_id": "replication.observations_failed"})  # fmt: skip
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), PULL_INTERVAL_S)
