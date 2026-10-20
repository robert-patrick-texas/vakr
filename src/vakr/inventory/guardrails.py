"""Network target guardrails: which addresses vakr may poll (15.5, SEC-015, NETSEC-001).

Polling sends packets wherever inventory says, so inventory is a
network-capable input. Every IP literal is checked before it is stored:

- multicast, unspecified (``0.0.0.0``, ``::``), and the limited broadcast
  address are never targets;
- addresses in ``security.denied_target_addresses`` are refused; the
  default denies the cloud metadata services (``169.254.169.254``,
  ``fd00:ec2::254``, ``100.100.100.200``), which answer anyone on the host;
- when ``security.allowed_target_cidrs`` is not empty, every target must be
  inside one of its networks.

An IPv4-mapped IPv6 address is checked as the IPv4 address it carries. A
DNS name is checked when the collector resolves it, not here, because its
address can change (9.11). ``target_problem`` is pure so the collector can
apply the same rule to resolved addresses.

Dependencies:
    ipaddress: Address and network parsing.

Safety:
    Pure. Messages name the address and the setting that refused it.

Example:
    >>> policy = TargetPolicy.parse([], list(DEFAULT_DENIED))
    >>> target_problem("169.254.169.254", policy), target_problem("192.0.2.1", policy)
    ('is denied by security.denied_target_addresses (169.254.169.254/32)', None)
"""

import ipaddress
from collections.abc import Sequence
from dataclasses import dataclass

type Network = ipaddress.IPv4Network | ipaddress.IPv6Network
type Address = ipaddress.IPv4Address | ipaddress.IPv6Address

ALLOWED_KEY = "security.allowed_target_cidrs"
DENIED_KEY = "security.denied_target_addresses"
DEFAULT_DENIED = ("169.254.169.254/32", "fd00:ec2::254/128", "100.100.100.200/32")
"""Known cloud metadata-service addresses, denied by default (15.5, E.26)."""

_BROADCAST = ipaddress.IPv4Address("255.255.255.255")


@dataclass(frozen=True, slots=True)
class TargetPolicy:
    """The configured allow and deny lists.

    Attributes:
        allowed: Networks targets must fall in; empty allows every unicast address.
        denied: Networks no target may fall in.
    """

    allowed: tuple[Network, ...] = ()
    denied: tuple[Network, ...] = ()

    @classmethod
    def parse(cls, allowed: Sequence[str], denied: Sequence[str]) -> TargetPolicy:
        """Build the policy from configuration values.

        Args:
            allowed: ``security.allowed_target_cidrs`` entries.
            denied: ``security.denied_target_addresses`` entries (addresses or CIDRs).

        Returns:
            TargetPolicy: The policy.

        Raises:
            ValueError: Naming every entry that is not an address or network.
        """
        problems: list[str] = []
        policy = cls(_networks(allowed, ALLOWED_KEY, problems),
                     _networks(denied, DENIED_KEY, problems))  # fmt: skip
        if problems:
            raise ValueError("; ".join(problems))
        return policy


def _networks(values: Sequence[str], key: str, problems: list[str]) -> tuple[Network, ...]:
    """Parse address or CIDR strings; host bits must be zero.

    Args:
        values: The entries.
        key: Option name, for messages.
        problems: Problems are appended here.

    Returns:
        tuple[Network, ...]: The valid networks.

    Raises:
        None
    """
    found = []
    for index, value in enumerate(values):
        try:
            found.append(ipaddress.ip_network(str(value).strip()))
        except ValueError as exc:
            problems.append(f"{key}[{index}]: {value!r} is not an address or CIDR ({exc})")
    return tuple(found)


def _effective(ip: Address) -> Address:
    """Unwrap an IPv4-mapped IPv6 address, so ``::ffff:a.b.c.d`` is checked as ``a.b.c.d``.

    Args:
        ip: The address.

    Returns:
        Address: The address to check.

    Raises:
        None
    """
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def target_problem(address: str, policy: TargetPolicy) -> str | None:
    """Explain why an address may not be polled, or return ``None`` when it may.

    Args:
        address: A poll address: an IP literal or a DNS name.
        policy: The allow and deny lists.

    Returns:
        str | None: Why the target is refused; ``None`` for allowed
        addresses and for DNS names, which are checked once resolved.

    Raises:
        None
    """
    try:
        ip = _effective(ipaddress.ip_address(address))
    except ValueError:
        return None
    kind = next((label for test, label in _NEVER if test(ip)), None)
    if kind is not None:
        return f"is {kind}, never a poll target"
    for network in policy.denied:
        if ip.version == network.version and ip in network:
            return f"is denied by {DENIED_KEY} ({network})"
    if policy.allowed and not any(ip.version == n.version and ip in n for n in policy.allowed):
        return f"is outside every network in {ALLOWED_KEY}"
    return None


_NEVER = (
    (lambda ip: ip.is_multicast, "a multicast address"),
    (lambda ip: ip.is_unspecified, "an unspecified address"),
    (lambda ip: ip == _BROADCAST, "the broadcast address"),
)
"""Addresses that are never unicast targets, whatever the configuration says."""
