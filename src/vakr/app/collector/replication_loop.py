"""The peer's metadata pull inside the collector, every 60 s (MPOS-003, E.13, ADR-024).

On a peer the collector owns the metadata writes that replication makes,
as it owns discovery's on a primary. ``metadata_pull_loop`` pulls at start
and then every ``PULL_INTERVAL_S``, in a worker thread, never on the event
loop. The token file and the egress settings are read at each attempt, so
installing or rotating the token needs no restart. On a primary the loop
only waits for the stop. ``PeerInventory`` keeps discovery from writing
metadata on a peer: identity belongs to the primary, and the peer polls
with the interfaces it replicated (ADR-024).

Dependencies:
    vakr.replication: The client, token file, and puller.
    vakr.cli.cmd_tls: The shared outbound HTTPS settings (proxy, private CAs).

Safety:
    A failed pull is logged as node health and the loop goes on; the peer
    keeps polling with its last-known metadata (MPOS-002).

Example:
    >>> PULL_INTERVAL_S
    60.0
"""

import asyncio
import contextlib
import logging
from datetime import datetime
from typing import Any

from vakr.collectors.snmp import PollPlan, plan_from_discovery
from vakr.config.node import node_settings
from vakr.discovery import DiscoveryResult
from vakr.domain.ids import DeviceId
from vakr.replication import status
from vakr.replication.client import PartnerClient
from vakr.replication.metadata_pull import MetadataPuller, PullError
from vakr.replication.resync import Resync
from vakr.replication.token_file import TokenFileError, read_token, token_path

_LOG = logging.getLogger(__name__)
PULL_INTERVAL_S = 60.0


def _pull(service: Any) -> str:
    """Build the puller from current files and pull once (blocking).

    Args:
        service: The running collector service.

    Returns:
        str: ``applied`` or ``unchanged``.

    Raises:
        PullError: When the pull failed (recorded).
    """
    from vakr.cli.cmd_tls import build_egress_settings

    parts = service.parts
    node = node_settings(parts.config)
    record = parts.paths.state_dir / status.FILE
    try:
        token = read_token(token_path(parts.paths.secrets_dir))
        settings = build_egress_settings(parts.paths, parts.config)
    except (TokenFileError, OSError, ValueError) as exc:
        status.record(record, "failed", service.clock.now_utc(), error=str(exc))
        raise PullError(str(exc)) from None
    client = PartnerClient(node.primary_url, token, settings)
    return MetadataPuller(client, parts.uow_factory, record, service.clock,
                          _resync(service)).pull_once()  # fmt: skip


def _resync(service: Any) -> Resync | None:
    """Return the resync a demotion left behind, or ``None`` (ADR-024, E.39).

    Args:
        service: The running collector service.

    Returns:
        Resync | None: Backs up ``metadata.db`` into ``backups/`` before replacing.

    Raises:
        None
    """
    from vakr.adapters.sqlite.metadata import METADATA_FILE, metadata_migrations

    state = service.parts.paths.state_dir
    if not Resync.pending(state):
        return None
    manager = metadata_migrations(state / METADATA_FILE)
    return Resync(state, service.parts.uow_factory, lambda: manager.backup(state / "backups"),
                  service.clock.now_utc)  # fmt: skip


async def replication_loops(service: Any) -> None:
    """Run the metadata pull (a peer) and the observation pull (both roles) side by side.

    Args:
        service: The running collector service.

    Returns:
        None

    Raises:
        None
    """
    from vakr.app.collector.observation_loop import observation_pull_loop

    await asyncio.gather(metadata_pull_loop(service), observation_pull_loop(service))


async def metadata_pull_loop(service: Any) -> None:
    """Pull the primary's metadata until the collector stops; idle on a primary.

    Args:
        service: The running collector service (``parts``, ``clock``, ``loops_stop``).

    Returns:
        None

    Raises:
        None
    """
    stop: asyncio.Event = service.loops_stop
    if not node_settings(service.parts.config).is_peer:
        await stop.wait()
        return
    while not stop.is_set():
        try:
            await asyncio.to_thread(_pull, service)
        except PullError as exc:
            _LOG.warning("metadata pull from the primary failed; keeping last-known metadata: %s",
                         exc, extra={"event_id": "replication.metadata_failed"})  # fmt: skip
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), PULL_INTERVAL_S)


class PeerInventory:
    """An inventory source that never writes replicated metadata (ADR-024).

    A peer takes inventory, identities, and interface lifecycle from the
    primary, so this wrapper offers an allow-list: the reads the collector
    plans from (``devices``, ``change_counter``, ``poll_plan``), the in-memory
    policy swap of a reload (``replace_policy``), and ``record_discovery``,
    which answers the replicated plan instead of reconciling. Anything else,
    such as ``record_discovery_report`` or the Stage 1 lifecycle writer
    ``record_stage1_status`` (E.6), does not exist here, so a new write method
    of the inventory service cannot reach a peer's metadata by accident. The
    peer still records its own interface status observations: they are
    telemetry, owned by its collector.
    """

    def __init__(self, inner: Any) -> None:
        """Wrap a source.

        Args:
            inner: The real inventory source.

        Returns:
            None

        Raises:
            None
        """
        self._inner = inner

    def devices(self) -> Any:
        """Return the devices to poll (read).

        Args:
            None

        Returns:
            Any: The wrapped source's devices.

        Raises:
            StorageError: If metadata cannot be read.
        """
        return self._inner.devices()

    def change_counter(self) -> int:
        """Return the inventory change counter (read).

        Args:
            None

        Returns:
            int: The counter.

        Raises:
            StorageError: If metadata cannot be read.
        """
        return self._inner.change_counter()

    def poll_plan(self, device_id: DeviceId) -> PollPlan | None:
        """Return a device's plan from replicated metadata (read).

        Args:
            device_id: The device.

        Returns:
            PollPlan | None: The plan.

        Raises:
            NotFoundError: If inventory no longer knows the device.
        """
        return self._inner.poll_plan(device_id)

    def replace_policy(self, *args: Any, **kwargs: Any) -> None:
        """Swap the in-memory collection policy on reload; nothing is written.

        Args:
            *args: As the wrapped source's ``replace_policy``.
            **kwargs: As the wrapped source's ``replace_policy``.

        Returns:
            None

        Raises:
            AttributeError: If the wrapped source has no such method.
        """
        self._inner.replace_policy(*args, **kwargs)

    def record_discovery(self, device_id: DeviceId, result: DiscoveryResult,
                         now: datetime) -> PollPlan:  # noqa: ARG002 - contract  # fmt: skip
        """Answer the replicated plan; collect no interface the primary has not named.

        Args:
            device_id: The device.
            result: What discovery found (not stored).
            now: When it finished (unused).

        Returns:
            PollPlan: The plan from replicated metadata, or an empty one.

        Raises:
            NotFoundError: If inventory no longer knows the device.
        """
        plan = self._inner.poll_plan(device_id)
        return plan if plan is not None else plan_from_discovery(result, lambda _i: False)
