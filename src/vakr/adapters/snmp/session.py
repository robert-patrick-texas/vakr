"""PySNMP-backed SNMP sessions and the per-device lock registry (SNMP-012).

``PysnmpSession`` implements the ``SnmpSession`` port on top of the request
engine. ``DeviceLocks`` hands every session to the same agent the same
``asyncio.Lock``, so discovery and a poll that overlap on one device still
send one PDU at a time. A lock lives only while a session to that agent is
open, so the registry is bounded by the number of open sessions.

Dependencies:
    asyncio: Locks.
    vakr.adapters.snmp: Engine, GET batching, walks, and transport.
    vakr.ports.snmp: The session protocol's types.

Safety:
    A session's ``repr`` names the agent and the credential *name* only.
    After ``close`` the transport forgets the community and every request
    fails as a collector problem.

Example:
    >>> locks = DeviceLocks()
    >>> locks.acquire(("192.0.2.1", 161)) is locks.acquire(("192.0.2.1", 161)), len(locks)
    (True, 1)
"""

import asyncio
from collections.abc import Sequence

from vakr.adapters.snmp import getbatch, walking
from vakr.adapters.snmp.engine import RequestEngine
from vakr.adapters.snmp.transport import PysnmpTransport
from vakr.ports.snmp import GetResult, Oid, SnmpStats, SnmpTarget, WalkResult

type PeerKey = tuple[str, int]


class DeviceLocks:
    """One lock per agent address and port, shared while sessions are open."""

    def __init__(self) -> None:
        """Create an empty registry.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self._locks: dict[PeerKey, tuple[asyncio.Lock, int]] = {}

    def __len__(self) -> int:
        """Return how many agents have open sessions.

        Args:
            None

        Returns:
            int: Entries in the registry.

        Raises:
            None
        """
        return len(self._locks)

    def acquire(self, key: PeerKey) -> asyncio.Lock:
        """Register one more session to ``key`` and return its lock.

        Args:
            key: The agent's resolved address and port.

        Returns:
            asyncio.Lock: The lock every session to that agent shares.

        Raises:
            None
        """
        lock, users = self._locks.get(key, (None, 0))
        lock = asyncio.Lock() if lock is None else lock
        self._locks[key] = (lock, users + 1)
        return lock

    def release(self, key: PeerKey) -> None:
        """Unregister one session to ``key``; drop the lock with the last one.

        Args:
            key: The agent's resolved address and port.

        Returns:
            None

        Raises:
            None
        """
        lock, users = self._locks.get(key, (None, 0))
        if lock is None:
            return
        if users <= 1:
            del self._locks[key]
        else:
            self._locks[key] = (lock, users - 1)


class PysnmpSession:
    """An open SNMPv2c conversation with one agent (``SnmpSession``)."""

    def __init__(
        self, target: SnmpTarget, engine: RequestEngine, transport: PysnmpTransport
    ) -> None:
        """Wrap an engine and its transport.

        Args:
            target: The agent.
            engine: The request engine bound to the transport.
            transport: The transport, so ``close`` can make it forget the
                credential.

        Returns:
            None

        Raises:
            None
        """
        self._target = target
        self._engine = engine
        self._transport = transport

    def __repr__(self) -> str:
        """Describe the session without any secret.

        Args:
            None

        Returns:
            str: Agent label and credential name.

        Raises:
            None
        """
        return f"PysnmpSession({self._target.label}, credential={self._target.credential!r})"

    @property
    def target(self) -> SnmpTarget:
        """Return the agent this session talks to.

        Args:
            None

        Returns:
            SnmpTarget: The target.

        Raises:
            None
        """
        return self._target

    @property
    def max_varbinds(self) -> int:
        """Return the current varbinds-per-request limit (SNMP-017).

        Args:
            None

        Returns:
            int: The limit after any tooBig reductions.

        Raises:
            None
        """
        return self._engine.max_varbinds

    @property
    def stats(self) -> SnmpStats:
        """Return the effort spent so far in this session.

        Args:
            None

        Returns:
            SnmpStats: Cumulative counts.

        Raises:
            None
        """
        return self._engine.stats

    async def get(self, oids: Sequence[Oid]) -> GetResult:
        """GET exact OIDs in batches (SNMP-014, SNMP-017).

        Args:
            oids: OIDs to fetch.

        Returns:
            GetResult: What arrived and what did not.

        Raises:
            ValueError: If ``oids`` is empty.
        """
        return await getbatch.get(self._engine, oids)

    async def walk(self, root: Oid) -> WalkResult:
        """Walk one subtree with GETBULK (DISC-008).

        Args:
            root: The subtree.

        Returns:
            WalkResult: The rows and why the walk stopped, if early.

        Raises:
            None
        """
        return await walking.walk(self._engine, root)

    def close(self) -> None:
        """Make the transport forget the credential; later requests fail.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self._transport.forget()
