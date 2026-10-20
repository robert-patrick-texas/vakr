"""The SNMP session protocol that discovery, collectors, and the scheduler call.

A session is one short conversation with one agent: the collector opens it
just before a poll or discovery, makes a few GET and walk requests, and
closes it. The adapter behind it resolves the credential at open time and
drops it at close (blueprint 15.2), and keeps at most one request
outstanding per device at any moment (SNMP-012), however many sessions
target the same device.

Requests never raise for SNMP failures. They return explicit results that
keep whatever arrived (SNMP-014). Only opening a session can fail outright,
with ``SnmpSessionError``, for example when the credential is missing.

Dependencies:
    contextlib, typing: The async context manager protocol.
    vakr.ports.snmp: Targets, settings, values, and results.

Safety:
    Implementations must never log, raise, or ``repr`` a community or other
    secret, and must keep all blocking work off the event loop (SNMP-015).

Example:
    >>> async def uptime(factory: SnmpSessionFactory, target: SnmpTarget) -> int | None:
    ...     async with factory.open(target, SnmpSettings()) as session:
    ...         result = await session.get([(1, 3, 6, 1, 2, 1, 1, 3, 0)])
    ...     return result.varbinds[0].integer() if result.varbinds else None
"""

from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from typing import Protocol

from vakr.ports.snmp.results import GetResult, SnmpStats, WalkResult
from vakr.ports.snmp.settings import SnmpSettings
from vakr.ports.snmp.target import SnmpTarget
from vakr.ports.snmp.values import Oid


class SnmpSession(Protocol):
    """An open conversation with one SNMP agent."""

    @property
    def target(self) -> SnmpTarget:
        """Return the agent this session talks to.

        Args:
            None

        Returns:
            SnmpTarget: The target given at open time.

        Raises:
            None
        """
        ...

    @property
    def max_varbinds(self) -> int:
        """Return the current varbinds-per-request limit.

        It starts at ``SnmpSettings.max_varbinds`` and halves after tooBig
        (SNMP-017). Callers may remember it per device for the next session.

        Args:
            None

        Returns:
            int: The current limit.

        Raises:
            None
        """
        ...

    @property
    def stats(self) -> SnmpStats:
        """Return the effort spent so far in this session.

        Args:
            None

        Returns:
            SnmpStats: Cumulative counts since the session opened.

        Raises:
            None
        """
        ...

    async def get(self, oids: Sequence[Oid]) -> GetResult:
        """GET exact OIDs, in as few requests as the size limit allows.

        Args:
            oids: OIDs to fetch; duplicates are fetched once.

        Returns:
            GetResult: Every varbind that arrived, the OIDs that did not,
            and the first error.

        Raises:
            ValueError: If ``oids`` is empty.
        """
        ...

    async def walk(self, root: Oid) -> WalkResult:
        """Walk one subtree with GETBULK.

        Args:
            root: The subtree, for example the ``ifDescr`` column.

        Returns:
            WalkResult: The varbinds inside the subtree, and why the walk
            stopped if it stopped early.

        Raises:
            None
        """
        ...


class SnmpSessionFactory(Protocol):
    """Opens sessions; the adapter owns credentials and sockets."""

    def open(
        self, target: SnmpTarget, settings: SnmpSettings
    ) -> AbstractAsyncContextManager[SnmpSession]:
        """Return a context manager that opens a session and always closes it.

        Args:
            target: The agent to talk to.
            settings: The request budget.

        Returns:
            AbstractAsyncContextManager[SnmpSession]: Enter it to get the
            session; leaving it releases the socket and the credential, even
            on cancellation.

        Raises:
            SnmpSessionError: On entry, when the credential cannot be
                resolved, the version is unsupported, the name does not
                resolve, or the socket cannot be opened.
        """
        ...
