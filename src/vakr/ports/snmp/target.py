"""Where to poll one device over SNMP: the agent's address, port, and credential name.

``SnmpTarget`` names the agent (address, UDP port, address family, optional
source address) and the credential *by name*. The community itself is
resolved by the adapter at session time, so a target is safe to log, queue,
and show (SEC-001).

A target validates on construction (SNMP-016, SNMP-019, blueprint 15.5): a
multicast, unspecified, or broadcast address is refused, and a source
address must be a canonical IP literal of the same family. CIDR allow and
deny policy is applied by the inventory layer, which knows the configuration.

Dependencies:
    ipaddress: Address checks.
    vakr.domain: Poll-address rules, ``AddressFamily``, ``SnmpVersion``.
    vakr.ports.snmp.settings: The bounded-integer check.

Safety:
    No field holds a secret. ``credential`` is a reference into the
    credentials file, never a community.

Example:
    >>> t = SnmpTarget("2001:db8::1", credential="network-v2c")
    >>> t.label, t.address_family.value
    ('[2001:db8::1]:161', 'ipv6')
"""

import ipaddress
from dataclasses import dataclass

from vakr.domain.addressing import require_poll_address
from vakr.domain.checks import require_name
from vakr.domain.enums import AddressFamily, SnmpVersion
from vakr.domain.errors import ValidationError
from vakr.ports.snmp.settings import require_int

DEFAULT_PORT = 161
MAX_CONTEXT_OCTETS = 32  # SnmpAdminString (SIZE (0..32)) for contextName, RFC 3411


def _require_target_ip(address: str, field: str) -> None:
    """Refuse IP literals that can never be one SNMP agent (blueprint 15.5).

    Args:
        address: Canonical address or host name.
        field: Field name for the error message.

    Returns:
        None

    Raises:
        ValidationError: If the address is multicast, unspecified, or the
            IPv4 limited broadcast address.
    """
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return  # a host name; resolution and policy checks happen later
    broadcast = ip.version == 4 and ip == ipaddress.IPv4Address("255.255.255.255")
    if ip.is_multicast or ip.is_unspecified or broadcast:
        raise ValidationError(f"{field} {address} cannot be an SNMP agent", field=field)


@dataclass(frozen=True, slots=True)
class SnmpTarget:
    """One SNMP agent to talk to, without any secret (SNMP-016).

    Attributes:
        address: Canonical IP literal or DNS name of the agent.
        credential: Name of the credential in the credentials file.
        port: UDP port of the agent.
        family: Address family to use when a name resolves to both.
        version: SNMP version; the MVP adapter supports only ``V2C``.
        source_address: Local IP address to send from, when the collector
            has several network paths (9.11).
        context: SNMPv3 context name; must be ``None`` for v2c.
    """

    address: str
    credential: str
    port: int = DEFAULT_PORT
    family: AddressFamily | None = None
    version: SnmpVersion = SnmpVersion.V2C
    source_address: str | None = None
    context: str | None = None

    def __post_init__(self) -> None:
        """Validate every field.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: On the first invalid field.
        """
        if self.family is not None and not isinstance(self.family, AddressFamily):
            raise ValidationError("family must be an AddressFamily", field="family")
        require_poll_address(self.address, self.family)
        _require_target_ip(self.address, "address")
        require_name(self.credential, "credential")
        require_int(self.port, "port", 1, 65535)
        if not isinstance(self.version, SnmpVersion):
            raise ValidationError("version must be an SnmpVersion", field="version")
        self._check_source()
        self._check_context()

    def _check_source(self) -> None:
        """Require the source address to be a canonical IP of the target's family.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If the source address is malformed, not
                canonical, unusable, or of the other family.
        """
        if self.source_address is None:
            return
        try:
            source = ipaddress.ip_address(self.source_address)
        except ValueError:
            raise ValidationError(
                "source_address must be an IP address", field="source_address"
            ) from None
        if str(source) != self.source_address or source.is_multicast:
            raise ValidationError(
                "source_address must be a canonical unicast IP", field="source_address"
            )
        if self.address_family not in (None, _family(source)):
            raise ValidationError(
                "source_address and address must be the same address family",
                field="source_address",
            )

    def _check_context(self) -> None:
        """Allow a context name only where the SNMP version has contexts.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If v2c has a context, or a v3 context is too long.
        """
        if self.context is None:
            return
        if self.version is SnmpVersion.V2C:
            raise ValidationError("context applies only to SNMPv3", field="context")
        if len(self.context.encode("utf-8")) > MAX_CONTEXT_OCTETS:
            raise ValidationError("context must be at most 32 octets", field="context")

    @property
    def address_family(self) -> AddressFamily | None:
        """Return the family implied by an IP literal, else the configured one.

        Args:
            None

        Returns:
            AddressFamily | None: ``None`` for a host name with no preference.

        Raises:
            None
        """
        try:
            return _family(ipaddress.ip_address(self.address))
        except ValueError:
            return self.family

    @property
    def label(self) -> str:
        """Return ``address:port`` for messages, with IPv6 in brackets.

        Args:
            None

        Returns:
            str: For example ``192.0.2.1:161`` or ``[2001:db8::1]:161``.

        Raises:
            None
        """
        host = f"[{self.address}]" if ":" in self.address else self.address
        return f"{host}:{self.port}"


def _family(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> AddressFamily:
    """Return the address family of an IP address.

    Args:
        ip: The address.

    Returns:
        AddressFamily: ``IPV4`` or ``IPV6``.

    Raises:
        None
    """
    return AddressFamily.IPV4 if ip.version == 4 else AddressFamily.IPV6
