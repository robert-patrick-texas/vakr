# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Pseudonymize SNMP walk files while keeping them valid fixtures (FIELD-005).

Walk captures become simulator fixtures, so a plain search-and-replace is
not enough: IP-MIB tables carry addresses inside the OID index
(``ipAddrTable`` ``...4.20.1.<col>.a.b.c.d`` and ``ipAddressTable``
``...4.34.1.<col>.<type>.<len>.<bytes>``) and inside OID-valued pointers
(``ipAddressPrefix``). This module parses ``snmpbulkwalk -On -Ot -Oe``
records and rewrites indexes, pointers, ``ifPhysAddress``, and ``sysName``
consistently; the free-text columns ``sysContact``, ``sysLocation``, and
``ifAlias`` keep only classification keywords (META-012). Other strings,
``sysDescr`` included, get the generic scrub.

Dependencies:
    fieldkit_scrub: Generic text scrubbing and the host-name registry.

Safety:
    Integer, counter, gauge, and time-tick values are never touched, so rate
    and uptime fixtures keep their numbers. Lines that are not walk records
    still get the generic scrub rather than passing through verbatim.

Example:
    >>> from fieldkit_pseudo import Pseudonymizer
    >>> s = Scrubber(Pseudonymizer(bytes(32), ("UPLINK",)))
    >>> line = ".1.3.6.1.2.1.31.1.1.1.18.7 = STRING: UPLINK to core1"
    >>> scrub_walk(s, line).split("STRING: ")[1].split()[0]
    'UPLINK'
"""

import re
from collections.abc import Callable, Iterator

from fieldkit_scrub import MAC_RE, Scrubber

SYS_NAME = ".1.3.6.1.2.1.1.5.0"
FREE_TEXT_EXACT = (".1.3.6.1.2.1.1.4.0", ".1.3.6.1.2.1.1.6.0")  # sysContact, sysLocation
IF_ALIAS = ".1.3.6.1.2.1.31.1.1.1.18."
IF_PHYS = ".1.3.6.1.2.1.2.2.1.6."
IP_ADDR_ENTRY = ".1.3.6.1.2.1.4.20.1."
IP_ADDRESS_ENTRY = ".1.3.6.1.2.1.4.34.1."
IP_PREFIX_ENTRY = ".1.3.6.1.2.1.4.32.1."
# InetAddressType -> (address bytes, zone bytes), RFC 4001.
_INET_TYPES = {"1": (4, 0), "2": (16, 0), "3": (4, 4), "4": (16, 4)}
_RECORD_RE = re.compile(r"^(\.[0-9.]+) = (?:([A-Za-z0-9-]+): ?)?(.*)$")


def _records(text: str) -> Iterator[tuple[str, str, str]]:
    """Split walk output into ``(oid, type, value)`` records.

    Values that span lines (Cisco ``sysDescr``) keep their newlines; lines
    before the first record come back with an empty OID.

    Args:
        text: ``snmpbulkwalk -On -Ot -Oe`` standard output.

    Returns:
        Iterator[tuple[str, str, str]]: One tuple per record.

    Raises:
        None
    """
    current: list[str] | None = None
    for line in text.splitlines():
        match = _RECORD_RE.match(line)
        if match:
            if current is not None:
                yield current[0], current[1], current[2]
            current = [match[1], match[2] or "", match[3]]
        elif current is not None:
            current[2] += "\n" + line
        else:
            yield "", "", line
    if current is not None:
        yield current[0], current[1], current[2]


def _map_octets(s: Scrubber, octets: list[str], plen: int | None = None) -> list[str]:
    """Pseudonymize an address written as decimal OID sub-identifiers.

    Args:
        s: The run's scrubber.
        octets: Four (IPv4) or sixteen (IPv6) decimal strings.
        plen: Prefix length; when given, host bits are cleared so a prefix
            pointer still names a network address.

    Returns:
        list[str]: Mapped sub-identifiers, or the input if it is malformed.

    Raises:
        None
    """
    if len(octets) not in (4, 16) or not all(o.isdigit() and int(o) < 256 for o in octets):
        return octets
    width = len(octets) * 8
    value = int.from_bytes(bytes(int(o) for o in octets))
    mapped = s.p.ipv4_int(value) if width == 32 else s.p.ipv6_int(value)
    fixed = 15 if width == 32 else 32  # never clear bits of the pseudonym range itself
    if plen is not None and fixed <= plen <= width:
        mapped &= ~((1 << (width - plen)) - 1)
    return [str(b) for b in mapped.to_bytes(width // 8)]


def _map_inet(s: Scrubber, parts: list[str], plen: int | None = None) -> list[str]:
    """Pseudonymize an ``InetAddressType.length.bytes[.zone]`` index fragment.

    Args:
        s: The run's scrubber.
        parts: Sub-identifiers starting with the address type.
        plen: Optional prefix length passed to ``_map_octets``.

    Returns:
        list[str]: Fragment with the address bytes replaced; trailing
        sub-identifiers (zone, prefix length) are preserved.

    Raises:
        None
    """
    if len(parts) < 2 or parts[0] not in _INET_TYPES:
        return parts
    size, zone = _INET_TYPES[parts[0]]
    if parts[1] != str(size + zone) or len(parts) < 2 + size:
        return parts
    return [*parts[:2], *_map_octets(s, parts[2 : 2 + size], plen), *parts[2 + size :]]


def scrub_oid(s: Scrubber, oid: str) -> str:
    """Rewrite addresses embedded in IP-MIB OID indexes and prefix pointers.

    Args:
        s: The run's scrubber.
        oid: Numeric OID with a leading dot.

    Returns:
        str: OID with index addresses pseudonymized; other OIDs unchanged.

    Raises:
        None
    """
    for table in (IP_ADDR_ENTRY, IP_ADDRESS_ENTRY, IP_PREFIX_ENTRY):
        if not oid.startswith(table):
            continue
        parts = oid[len(table) :].split(".")
        if table == IP_ADDR_ENTRY:
            rest = [parts[0], *_map_octets(s, parts[1:])]
        elif table == IP_ADDRESS_ENTRY:
            rest = [parts[0], *_map_inet(s, parts[1:])]
        else:  # ipAddressPrefixEntry: column.ifIndex.type.len.bytes.prefixLength
            plen = int(parts[-1]) if parts[-1].isdigit() else None
            rest = [*parts[:2], *_map_inet(s, parts[2:], plen)]
        return table + ".".join(rest)
    return oid


def _as_text(vtype: str, value: str, transform: Callable[[str], str]) -> tuple[str, str]:
    """Apply ``transform`` to a string value, keeping quotes and decoding hex.

    Args:
        vtype: ``STRING``, ``Hex-STRING``, or ``""``.
        value: Raw value text from the walk.
        transform: Text rewrite to apply.

    Returns:
        tuple[str, str]: New type and value; hex input becomes ``STRING``.

    Raises:
        None
    """
    if vtype == "Hex-STRING":
        try:
            raw = bytes.fromhex(value)
        except ValueError:
            return vtype, transform(value)
        return "STRING", transform(raw.decode("utf-8", errors="replace"))
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return vtype, '"' + transform(value[1:-1]) + '"'
    return vtype, transform(value)


def _mac_value(s: Scrubber, vtype: str, value: str) -> tuple[str, str]:
    """Pseudonymize an ``ifPhysAddress`` value in STRING or Hex-STRING form.

    Args:
        s: The run's scrubber.
        vtype: Value type from the walk.
        value: ``52:54:0:e7:2b:bd``, ``52 54 00 E7 2B BD``, or (without
            MIBs) six raw printable characters.

    Returns:
        tuple[str, str]: Type and mapped value in the same notation; raw
        six-character values come back as ``Hex-STRING``.

    Raises:
        None
    """
    raw: bytes | None = None
    if vtype == "Hex-STRING":
        try:
            raw = bytes.fromhex(value)
        except ValueError:
            return vtype, value
    elif not MAC_RE.search(value) and len(value.strip('"')) == 6:
        raw = value.strip('"').encode("latin-1", errors="replace")
    if raw is None:
        return vtype, MAC_RE.sub(lambda m: s.mac_text(m.group(0)), value)
    trail = " " if value.endswith(" ") else ""
    return "Hex-STRING", " ".join(f"{b:02X}" for b in s.p.mac(raw)) + trail


def _scrub_string(s: Scrubber, oid: str, vtype: str, value: str) -> tuple[str, str]:
    """Pseudonymize a string-typed value according to the column it belongs to.

    Args:
        s: The run's scrubber.
        oid: Original numeric OID of the record.
        vtype: ``STRING``, ``Hex-STRING``, or ``""``.
        value: Value text.

    Returns:
        tuple[str, str]: New type and value.

    Raises:
        None
    """
    if oid == SYS_NAME:
        return _as_text(vtype, value, s.p.hostname)
    if oid in FREE_TEXT_EXACT or oid.startswith(IF_ALIAS):
        return _as_text(vtype, value, s.p.free_text)
    if oid.startswith(IF_PHYS):
        return _mac_value(s, vtype, value)
    return _as_text(vtype, value, s.text)


def _scrub_value(s: Scrubber, oid: str, vtype: str, value: str) -> tuple[str, str]:
    """Pseudonymize one record's value according to its OID and type.

    Args:
        s: The run's scrubber.
        oid: Original numeric OID of the record.
        vtype: Value type (``STRING``, ``IpAddress``, ``OID``, ...).
        value: Value text.

    Returns:
        tuple[str, str]: New type and value.

    Raises:
        None
    """
    if vtype == "IpAddress":
        return vtype, s.p.ip(value.strip())
    if vtype == "OID":
        return vtype, scrub_oid(s, value)
    if vtype in ("STRING", "Hex-STRING", ""):
        return _scrub_string(s, oid, vtype, value)
    return vtype, value  # numbers and time ticks carry no identity


def scrub_walk(s: Scrubber, text: str) -> str:
    """Pseudonymize a whole walk file while keeping it a valid fixture.

    Args:
        s: The run's scrubber.
        text: ``snmpbulkwalk -On -Ot -Oe`` output.

    Returns:
        str: Rewritten walk output, one record per original record.

    Raises:
        None
    """
    records = list(_records(text))
    # Learn sysName first: sysDescr precedes it in walk order and often repeats it.
    for oid, _, value in records:
        if oid == SYS_NAME:
            s.add_host(value.strip().strip('"'))
    lines = []
    for oid, vtype, value in records:
        if not oid:
            lines.append(s.text(value))
            continue
        new_type, new_value = _scrub_value(s, oid, vtype, value)
        prefix = f"{new_type}: " if new_type else ""
        lines.append(f"{scrub_oid(s, oid)} = {prefix}{new_value}")
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")
