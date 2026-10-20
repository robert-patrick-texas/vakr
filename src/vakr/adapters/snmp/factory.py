"""Open PySNMP SNMPv2c sessions: credential, address, socket, and lock (15.2).

``PysnmpSessionFactory.open`` does, in order and only for the session's
lifetime:

1. turns off library packet debugging, which would print the community;
2. refuses versions this release cannot poll (SNMPv3 is post-MVP);
3. resolves a DNS name with a bounded lookup, observed as a resolution
   failure rather than as the device being down (9.11);
4. resolves the credential *by name* in a worker thread, because the file
   provider does blocking I/O (SNMP-015);
5. checks the optional source address can be bound, so a bad one is a
   collector problem instead of an endless timeout;
6. creates the dispatcher, transport, engine, and session, sharing the
   device's lock with every other open session to it (SNMP-012).

Leaving the context closes the socket and drops the community, even when
the poll was cancelled or timed out.

Dependencies:
    asyncio, ipaddress, socket: Lookups and the source-address check.
    pysnmp: Endpoints for UDP over IPv4 and IPv6.
    vakr.credentials: The resolver and v2c credential type.
    vakr.adapters.snmp: Transport, engine, and session.

Safety:
    Every failure becomes ``SnmpSessionError`` with an operator message that
    names the credential, never its value.

Example:
    >>> PysnmpSessionFactory(resolver=None)
    PysnmpSessionFactory(open_sessions=0)
"""

import asyncio
import ipaddress
import socket
from collections.abc import AsyncGenerator
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from pysnmp.error import PySnmpError
from pysnmp.hlapi.v1arch.asyncio import Udp6TransportTarget, UdpTransportTarget

from vakr.adapters.snmp import messages
from vakr.adapters.snmp.engine import RequestEngine
from vakr.adapters.snmp.guards import (
    GuardedDispatcher,
    RedactedCommunity,
    close_dispatcher,
    quiet_library_debug,
)
from vakr.adapters.snmp.session import DeviceLocks, PeerKey, PysnmpSession
from vakr.adapters.snmp.transport import Endpoint, PysnmpTransport
from vakr.credentials import CredentialError, CredentialReference, CredentialResolver
from vakr.credentials.model import SnmpV2cCredential
from vakr.domain.enums import AddressFamily, SnmpVersion
from vakr.domain.errors import ValidationError
from vakr.ports.clock import Clock, SystemClock
from vakr.ports.snmp import SnmpErrorKind, SnmpSession, SnmpSessionError, SnmpSettings, SnmpTarget

RESOLVE_TIMEOUT_S = 5.0  # E.23: bound on one DNS lookup for an SNMP target
_FAMILIES = {AddressFamily.IPV4: socket.AF_INET, AddressFamily.IPV6: socket.AF_INET6}


class PysnmpSessionFactory:
    """Opens SNMPv2c sessions with PySNMP (``SnmpSessionFactory``)."""

    def __init__(
        self,
        resolver: CredentialResolver | None,
        clock: Clock | None = None,
        resolve_timeout_s: float = RESOLVE_TIMEOUT_S,
    ) -> None:
        """Create a factory.

        Args:
            resolver: Resolves credential names; called once per session.
            clock: Monotonic time for effort accounting; the system clock
                when ``None``.
            resolve_timeout_s: Bound on a DNS lookup.

        Returns:
            None

        Raises:
            None
        """
        self._resolver = resolver
        self._clock = SystemClock() if clock is None else clock
        self._resolve_timeout_s = resolve_timeout_s
        self._locks = DeviceLocks()

    def __repr__(self) -> str:
        """Describe the factory without its resolver.

        Args:
            None

        Returns:
            str: The number of agents with open sessions.

        Raises:
            None
        """
        return f"PysnmpSessionFactory(open_sessions={len(self._locks)})"

    def open(
        self, target: SnmpTarget, settings: SnmpSettings
    ) -> AbstractAsyncContextManager[SnmpSession]:
        """Return a context manager that opens a session and always closes it.

        Args:
            target: The agent.
            settings: The request budget.

        Returns:
            AbstractAsyncContextManager[SnmpSession]: The session context.

        Raises:
            SnmpSessionError: On entry, if the session cannot be opened.
        """
        return self._session(target, settings)

    @asynccontextmanager
    async def _session(
        self, target: SnmpTarget, settings: SnmpSettings
    ) -> AsyncGenerator[SnmpSession]:
        """Open, yield, and close one session.

        Args:
            target: The agent.
            settings: The request budget.

        Returns:
            AsyncGenerator[SnmpSession]: Yields the open session once.

        Raises:
            SnmpSessionError: If the session cannot be opened.
        """
        quiet_library_debug()
        if target.version is not SnmpVersion.V2C:
            what = "SNMPv3 polling arrives after the MVP; use an SNMPv2c credential for now"
            raise SnmpSessionError(
                messages.collector(SnmpErrorKind.UNSUPPORTED, target.label, what)
            )
        peer = await self._resolve(target)
        _check_source(target)
        auth = await self._community(target)
        lock = self._locks.acquire(peer)
        dispatcher: GuardedDispatcher | None = None
        session: PysnmpSession | None = None
        try:
            endpoint = await _endpoint(peer, target, settings)
            dispatcher = GuardedDispatcher(peer, target.label)
            transport = PysnmpTransport(dispatcher, auth, endpoint, target.label)
            del auth  # the transport holds the only reference until close
            engine = RequestEngine(transport, settings, lock, self._clock, target.label)
            session = PysnmpSession(target, engine, transport)
            yield session
        finally:
            # Close the socket even when the poll timed out or was cancelled,
            # so a network-wide outage cannot leak file descriptors.
            if session is not None:
                session.close()
            if dispatcher is not None:
                close_dispatcher(dispatcher)
            self._locks.release(peer)

    async def _resolve(self, target: SnmpTarget) -> PeerKey:
        """Resolve the target to one IP address, with a bounded DNS lookup (9.11).

        Args:
            target: The agent.

        Returns:
            PeerKey: The IP address and port to send to.

        Raises:
            SnmpSessionError: ``RESOLUTION`` if the lookup fails, times out,
                or yields an address that cannot be an agent.
        """
        try:
            return (str(ipaddress.ip_address(target.address)), target.port)
        except ValueError:
            pass  # a DNS name
        family = socket.AF_UNSPEC if target.family is None else _FAMILIES[target.family]
        what: str | None = None
        try:
            async with asyncio.timeout(self._resolve_timeout_s):
                infos = await asyncio.get_running_loop().getaddrinfo(
                    target.address, target.port, family=family, type=socket.SOCK_DGRAM
                )
            address = str(infos[0][4][0])
            SnmpTarget(address, target.credential, target.port)  # same guardrails as literals
        except TimeoutError:
            what = f"DNS lookup of {target.address} took longer than {self._resolve_timeout_s:g} s"
        except OSError, IndexError:
            what = f"{target.address} did not resolve to an address"
        except ValidationError:
            what = f"{target.address} resolved to an address that cannot be an SNMP agent"
        if what is not None:
            raise SnmpSessionError(messages.collector(SnmpErrorKind.RESOLUTION, target.label, what))
        return (address, target.port)

    async def _community(self, target: SnmpTarget) -> RedactedCommunity:
        """Resolve the credential by name, off the event loop (15.2, SNMP-015).

        Args:
            target: The agent, naming its credential.

        Returns:
            RedactedCommunity: The community, wrapped for PySNMP.

        Raises:
            SnmpSessionError: ``CREDENTIAL`` if it cannot be resolved, or
                ``UNSUPPORTED`` if it is not an SNMPv2c credential.
        """
        what: str | None = None
        credential = None
        resolver = self._resolver
        if resolver is None:
            what = "no credential provider is configured"
            raise SnmpSessionError(messages.collector(SnmpErrorKind.CREDENTIAL, target.label, what))
        try:
            ref = CredentialReference(target.credential)
            credential = await asyncio.to_thread(resolver.resolve, ref)
        except CredentialError as exc:
            # Credential errors never contain values; they name files and keys.
            what = f"credential {target.credential!r} is not usable: {exc}"
        if what is not None:
            raise SnmpSessionError(messages.collector(SnmpErrorKind.CREDENTIAL, target.label, what))
        if not isinstance(credential, SnmpV2cCredential):
            what = f"credential {target.credential!r} is SNMPv3, which this release cannot poll yet"
            raise SnmpSessionError(
                messages.collector(SnmpErrorKind.UNSUPPORTED, target.label, what)
            )
        return RedactedCommunity(credential.community.reveal(), mpModel=1)


def _check_source(target: SnmpTarget) -> None:
    """Fail early when the source address cannot be bound on this host.

    Args:
        target: The agent, with its optional source address.

    Returns:
        None

    Raises:
        SnmpSessionError: ``TRANSPORT`` if binding the address fails.
    """
    if target.source_address is None:
        return
    family = socket.AF_INET6 if ":" in target.source_address else socket.AF_INET
    what: str | None = None
    with socket.socket(family, socket.SOCK_DGRAM) as probe:
        try:
            probe.bind((target.source_address, 0))  # local only: no packet is sent
        except OSError as exc:
            what = f"cannot send from {target.source_address} ({exc.strerror or 'bind failed'})"
    if what is not None:
        raise SnmpSessionError(messages.collector(SnmpErrorKind.TRANSPORT, target.label, what))


async def _endpoint(peer: PeerKey, target: SnmpTarget, settings: SnmpSettings) -> Endpoint:
    """Create PySNMP's endpoint for the agent, with retries off.

    Args:
        peer: Resolved address and port.
        target: The agent, for the source address.
        settings: The timeout.

    Returns:
        Endpoint: A UDP/IPv4 or UDP/IPv6 endpoint.

    Raises:
        SnmpSessionError: ``TRANSPORT`` if PySNMP cannot create it.
    """
    kind = Udp6TransportTarget if ":" in peer[0] else UdpTransportTarget
    what: str | None = None
    try:
        endpoint = await kind.create(peer, timeout=settings.timeout_s, retries=0)
    except PySnmpError, OSError:
        what = "PySNMP could not create a UDP endpoint for the agent"
    if what is not None:
        raise SnmpSessionError(messages.collector(SnmpErrorKind.TRANSPORT, target.label, what))
    if target.source_address is not None:
        endpoint.set_local_address((target.source_address, 0))
    return endpoint
