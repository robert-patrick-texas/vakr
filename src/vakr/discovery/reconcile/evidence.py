"""Identity evidence: comparable names, trustworthy MACs, and unique addresses (9.8, IFID-008).

Reconciliation compares what the agent reports now with what vakr stored
before. This module turns both into comparable evidence without touching
the raw values (9.8 step 2):

- a *name key* is ``ifName`` when the row has one, else ``ifDescr``,
  trimmed of whitespace and NUL padding and compared case-insensitively;
- a MAC is *strong* only when it is a six-octet unicast address that is not
  all zeros or all ones and appears exactly once among the rows and at most
  once among the stored interfaces. IOS XE gives SVIs, subinterfaces, and
  port-channel members the same base MAC, so a shared MAC proves nothing
  (IFID-008);
- an address is *unique* when exactly one row and at most one stored
  interface carry it.

Dependencies:
    vakr.discovery.model: Discovered rows.
    vakr.domain.interfaces: Stored interfaces.

Safety:
    Pure. Evidence text is cleaned of control characters before it is stored.

Example:
    >>> strong_mac("00:00:5e:00:53:01"), strong_mac("01:00:5e:00:00:01"), strong_mac(None)
    (True, False, False)
"""

import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from vakr.discovery.model import DiscoveredInterface
from vakr.domain.interfaces import Interface

_PADDING = " \t\r\n\x00"
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")
_SHOWN = 64  # long enough for any IOS XE interface name


@dataclass(frozen=True, slots=True, order=True)
class NameKey:
    """A comparable interface name.

    Attributes:
        field: ``ifName`` or ``ifDescr``: which column the key came from.
        value: Trimmed, case-folded text.
    """

    field: str
    value: str


def normalize(text: str | None) -> str:
    """Trim protocol padding and fold case, keeping the raw value elsewhere.

    Args:
        text: A DisplayString value, or ``None``.

    Returns:
        str: Comparable text; empty when unknown.

    Raises:
        None
    """
    return "" if text is None else text.strip(_PADDING).casefold()


def row_key(row: DiscoveredInterface) -> NameKey | None:
    """Return a row's name key: ``ifName``, else ``ifDescr`` when ``ifName`` is absent.

    Args:
        row: The discovered row.

    Returns:
        NameKey | None: The key, or ``None`` when the row has no name at all.

    Raises:
        None
    """
    if name := normalize(row.if_name):
        return NameKey("ifName", name)
    if descr := normalize(row.if_descr):
        return NameKey("ifDescr", descr)
    return None


def stored_key(interface: Interface, field: str) -> NameKey | None:
    """Return a stored interface's key in the same column as a row's key.

    Args:
        interface: The stored interface.
        field: ``ifName`` or ``ifDescr``.

    Returns:
        NameKey | None: The key, or ``None`` when that column is empty.

    Raises:
        None
    """
    value = normalize(interface.if_name if field == "ifName" else interface.if_descr)
    return NameKey(field, value) if value else None


def same_name(row: DiscoveredInterface, interface: Interface) -> bool:
    """Whether a row and a stored interface carry the same name.

    Args:
        row: The discovered row.
        interface: The stored interface.

    Returns:
        bool: ``True`` when both keys exist and are equal.

    Raises:
        None
    """
    key = row_key(row)
    return key is not None and key == stored_key(interface, key.field)


def strong_mac(mac: str | None) -> bool:
    """Whether a MAC could identify one interface: six-octet, unicast, not all 0 or 1 bits.

    Args:
        mac: Lower-case colon hex, or ``None``.

    Returns:
        bool: ``True`` for a usable unicast EUI-48.

    Raises:
        None
    """
    if mac is None:
        return False
    octets = mac.split(":")
    if len(octets) != 6:
        return False
    first = int(octets[0], 16)
    return not first & 1 and mac not in {"00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff"}


def unique_macs(rows: Sequence[DiscoveredInterface], stored: Sequence[Interface]) -> frozenset[str]:
    """Return the MACs strong enough to count as evidence on this device (IFID-008).

    Args:
        rows: Every discovered row.
        stored: Every stored interface of the device.

    Returns:
        frozenset[str]: Strong MACs seen once in the rows and at most once stored.

    Raises:
        None
    """
    in_rows = Counter(r.mac for r in rows if strong_mac(r.mac))
    in_store = Counter(i.mac for i in stored if strong_mac(i.mac))
    return frozenset(m for m, n in in_rows.items() if n == 1 and in_store[m] <= 1 and m)


def unique_addresses(
    rows: Sequence[DiscoveredInterface], stored: Sequence[Interface]
) -> frozenset[str]:
    """Return host addresses carried by one row and at most one stored interface.

    Args:
        rows: Every discovered row.
        stored: Every stored interface of the device.

    Returns:
        frozenset[str]: Addresses without prefix, such as ``192.0.2.1``.

    Raises:
        None
    """
    in_rows = Counter(_hosts(r.addresses for r in rows))
    in_store = Counter(_hosts(i.addresses for i in stored))
    return frozenset(a for a, n in in_rows.items() if n == 1 and in_store[a] <= 1)


def hosts(addresses: Iterable[str]) -> frozenset[str]:
    """Strip prefixes from interface addresses.

    Args:
        addresses: Such as ``192.0.2.1/31``.

    Returns:
        frozenset[str]: Such as ``192.0.2.1``.

    Raises:
        None
    """
    return frozenset(a.split("/", 1)[0] for a in addresses)


def _hosts(groups: Iterable[Iterable[str]]) -> list[str]:
    """List each interface's distinct host addresses, keeping repeats across interfaces.

    Args:
        groups: Address lists, one per interface.

    Returns:
        list[str]: Host addresses.

    Raises:
        None
    """
    return [host for group in groups for host in hosts(group)]


def shown(text: str | None) -> str:
    """Render a device-supplied name safely inside evidence text.

    Args:
        text: The raw value.

    Returns:
        str: Quoted, control characters replaced, at most 64 characters.

    Raises:
        None
    """
    clean = _CONTROL.sub("?", "" if text is None else text)
    return repr(clean[:_SHOWN])
