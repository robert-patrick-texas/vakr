"""Turn walked IF-MIB and IP-MIB columns into per-interface values (DISC-005, DISC-006).

A column walk returns ``column.index = value`` varbinds; the index is the
row key. For IF-MIB tables it is the ifIndex. For ``ipAddressTable`` it is
the address itself, encoded as ``type.length.octets`` (RFC 4001), and the
prefix length is the last sub-identifier of the ``ipAddressPrefix`` pointer
into ``ipAddressPrefixTable``. For the IPv4-only ``ipAddrTable`` the index is
the four address octets and the prefix comes from ``ipAdEntNetMask``.

Every function here is pure and treats a value of the wrong SNMP type as
absent, never as zero (blueprint 4.3).

Dependencies:
    ipaddress: Address and prefix handling.
    vakr.ports.snmp: Varbinds and walk results.

Safety:
    No I/O; malformed indexes are skipped, not guessed.

Example:
    >>> mac_text(bytes.fromhex("025e01000001"))
    '02:5e:01:00:00:01'
    >>> address_from_index((2, 16, *bytes.fromhex("20010db8000000000000000000000001")))
    IPv6Address('2001:db8::1')
"""

import ipaddress
from collections.abc import Mapping

from vakr.ports.snmp import Oid, VarBind, WalkResult

type IP = ipaddress.IPv4Address | ipaddress.IPv6Address

# InetAddressType (RFC 4001): ipv4(1), ipv6(2), ipv4z(3), ipv6z(4) with 4 zone octets.
_ADDRESS_OCTETS = {1: 4, 2: 16, 3: 4, 4: 16}
_ZONE_OCTETS = {1: 0, 2: 0, 3: 4, 4: 4}
BROADCAST = 3  # ipAddressType broadcast(3): not an interface address


def rows(walk: WalkResult | None, column: Oid) -> dict[tuple[int, ...], VarBind]:
    """Index a column walk's varbinds by row index.

    Args:
        walk: The walk of ``column``, or ``None`` if it was not attempted.
        column: The column OID the walk started from.

    Returns:
        dict[tuple[int, ...], VarBind]: Row index to varbind.

    Raises:
        None
    """
    if walk is None:
        return {}
    size = len(column)
    return {vb.oid[size:]: vb for vb in walk.varbinds if vb.oid[:size] == column}


def by_if_index(walk: WalkResult | None, column: Oid) -> dict[int, VarBind]:
    """Index an IF-MIB column walk by ifIndex.

    Args:
        walk: The walk.
        column: The column OID.

    Returns:
        dict[int, VarBind]: ifIndex to varbind; rows whose index is not one
        positive integer are skipped.

    Raises:
        None
    """
    return {
        index[0]: vb for index, vb in rows(walk, column).items() if len(index) == 1 and index[0] > 0
    }


def mac_text(octets: bytes | None) -> str | None:
    """Render ``ifPhysAddress`` as lower-case colon hex.

    Args:
        octets: The raw value, or ``None``.

    Returns:
        str | None: Such as ``02:5e:01:00:00:01``; ``None`` for no address.

    Raises:
        None
    """
    if not octets:
        return None
    return ":".join(f"{octet:02x}" for octet in octets)


def address_from_index(index: tuple[int, ...]) -> IP | None:
    """Decode an ``ipAddressTable`` row index (``type.length.octets``).

    Zoned types (``ipv4z``, ``ipv6z``) carry four zone octets after the
    address; the zone is dropped, the address kept.

    Args:
        index: The row index.

    Returns:
        IP | None: The address, or ``None`` for an unknown type or a
        malformed index.

    Raises:
        None
    """
    if len(index) < 2 or index[0] not in _ADDRESS_OCTETS:
        return None
    kind, length = index[0], index[1]
    octets = index[2:]
    if length != len(octets) or length != _ADDRESS_OCTETS[kind] + _ZONE_OCTETS[kind]:
        return None
    if not all(0 <= octet <= 255 for octet in octets):
        return None
    return ipaddress.ip_address(bytes(octets[: _ADDRESS_OCTETS[kind]]))


def prefix_from_pointer(pointer: Oid | None, ip: IP) -> int | None:
    """Read the prefix length from an ``ipAddressPrefix`` pointer.

    The pointer names a row of ``ipAddressPrefixTable`` whose last index
    component is the prefix length. ``zeroDotZero`` means unknown.

    Args:
        pointer: The pointer value, or ``None``.
        ip: The address, to bound the length.

    Returns:
        int | None: The prefix length, or ``None`` when unknown.

    Raises:
        None
    """
    if pointer is None or pointer == (0, 0) or len(pointer) < 2:
        return None
    length = pointer[-1]
    return length if 0 <= length <= ip.max_prefixlen else None


def mask_length(octets: bytes | None) -> int | None:
    """Convert an ``ipAdEntNetMask`` to a prefix length.

    Args:
        octets: The four mask octets.

    Returns:
        int | None: The prefix length, or ``None`` for a non-contiguous or
        malformed mask.

    Raises:
        None
    """
    if octets is None or len(octets) != 4:
        return None
    try:
        return ipaddress.IPv4Network(f"0.0.0.0/{ipaddress.IPv4Address(octets)}").prefixlen
    except ValueError:
        return None


def interface_address(ip: IP, prefix: int | None) -> str:
    """Write an address in the canonical ``address/prefix`` form.

    An unknown prefix is written as a host prefix (/32 or /128) and the
    caller records ``address_prefix`` as a missing field.

    Args:
        ip: The address.
        prefix: The prefix length, or ``None`` when unknown.

    Returns:
        str: Such as ``192.0.2.1/31``.

    Raises:
        None
    """
    length = ip.max_prefixlen if prefix is None else prefix
    return str(ipaddress.ip_interface(f"{ip}/{length}"))


def modern_addresses(
    walks: Mapping[str, WalkResult], columns: Mapping[str, Oid]
) -> tuple[dict[int, list[str]], set[int], bool]:
    """Collect addresses from ``ipAddressTable`` (RFC 4293).

    Args:
        walks: Walks by column name.
        columns: ``IP_ADDRESS_COLUMNS``.

    Returns:
        tuple[dict[int, list[str]], set[int], bool]: Addresses by ifIndex,
        the ifIndexes with an unknown prefix, and whether any IPv4 address
        was seen.

    Raises:
        None
    """
    found: dict[int, list[str]] = {}
    unknown_prefix: set[int] = set()
    types = rows(walks.get("ipAddressType"), columns["ipAddressType"])
    prefixes = rows(walks.get("ipAddressPrefix"), columns["ipAddressPrefix"])
    saw_ipv4 = False
    for index, vb in rows(walks.get("ipAddressIfIndex"), columns["ipAddressIfIndex"]).items():
        ip, if_index = address_from_index(index), vb.integer()
        kind = types.get(index)
        if ip is None or not if_index or (kind is not None and kind.integer() == BROADCAST):
            continue
        pointer = prefixes.get(index)
        prefix = prefix_from_pointer(None if pointer is None else pointer.object_id(), ip)
        if prefix is None:
            unknown_prefix.add(if_index)
        found.setdefault(if_index, []).append(interface_address(ip, prefix))
        saw_ipv4 |= ip.version == 4
    return found, unknown_prefix, saw_ipv4


def legacy_addresses(
    walks: Mapping[str, WalkResult], columns: Mapping[str, Oid]
) -> tuple[dict[int, list[str]], set[int]]:
    """Collect IPv4 addresses from the deprecated ``ipAddrTable``.

    Args:
        walks: Walks by column name.
        columns: ``IP_ADDR_COLUMNS``.

    Returns:
        tuple[dict[int, list[str]], set[int]]: Addresses by ifIndex, and the
        ifIndexes with an unknown prefix.

    Raises:
        None
    """
    found: dict[int, list[str]] = {}
    unknown_prefix: set[int] = set()
    masks = rows(walks.get("ipAdEntNetMask"), columns["ipAdEntNetMask"])
    for index, vb in rows(walks.get("ipAdEntIfIndex"), columns["ipAdEntIfIndex"]).items():
        if_index = vb.integer()
        if len(index) != 4 or not all(0 <= o <= 255 for o in index) or not if_index:
            continue
        ip = ipaddress.IPv4Address(bytes(index))
        mask = masks.get(index)
        prefix = mask_length(None if mask is None else mask.octets())
        if prefix is None:
            unknown_prefix.add(if_index)
        found.setdefault(if_index, []).append(interface_address(ip, prefix))
    return found, unknown_prefix
