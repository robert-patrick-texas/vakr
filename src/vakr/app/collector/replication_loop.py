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
    return MetadataPuller(client, parts.uow_factory, record, service.clock).pull_once()


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
    """An inventory source that never writes discovery results to metadata (ADR-024).

    Every other call goes to the wrapped source; ``record_discovery`` answers
    the plan from the replicated interfaces instead of reconciling.
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

    def __getattr__(self, name: str) -> Any:
        """Delegate everything else.

        Args:
            name: The attribute.

        Returns:
            Any: The wrapped source's attribute.

        Raises:
            AttributeError: If it has none; ``record_discovery_report`` is
                hidden, so manual operations never write on a peer.
        """
        if name == "record_discovery_report":
            raise AttributeError(name)
        return getattr(self._inner, name)

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
