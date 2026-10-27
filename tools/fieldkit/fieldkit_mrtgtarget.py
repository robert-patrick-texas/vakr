# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
r"""Parse one MRTG ``Target[]`` value into what a comparison needs (FIELD-002 ``mrtg``).

MRTG names the data it graphs in a compact syntax:
``port:community@router[:port[:timeout[:retries[:backoff[:version]]]]]``.
The interface part is an ``ifIndex`` (``2``), a lookup by name (``#Gi0/1``),
description (``\GigabitEthernet0/1``), address (``/192.0.2.1``), Ethernet
address (``!00-1a-2b-3c-4d-5e``), or type (``%6``), optionally reversed
with a leading ``-`` (in and out swapped), or two explicit OIDs joined by
``&``. Targets may also be arithmetic over several specs or a script in
backticks. A comparison with vakr only makes sense for one interface's
octet counters, so this module says which targets qualify and with which
counter width (SNMPv2c and v3 make MRTG read the 64-bit ``ifHC*`` counters).

Dependencies:
    re, dataclasses: Standard library only.

Safety:
    The community (the SNMPv3 user for version 3) is returned only in
    ``communities``, which is hidden from ``repr``, so the caller can add it
    to the bundle's redaction list; nothing else here keeps it. A script
    target's command line is never returned at all.

Example:
    >>> spec = parse_target(r"#Gi0/0/1:pub\@lic@rtr1.example.net:::::2")
    >>> spec.kind, spec.key, spec.host, spec.counter_bits, sorted(spec.communities)
    ('ifname', 'Gi0/0/1', 'rtr1.example.net', 64, ['pub@lic'])
"""

import re
from dataclasses import dataclass, field

LOOKUPS = {"#": "ifname", "\\": "ifdescr", "/": "ip", "!": "mac", "%": "type"}
"""Interface lookup prefixes and the kind each one selects."""
COMPARABLE = frozenset({"ifindex", "ifname", "ifdescr", "ip", "mac"})
_OCTETS = {
    "ifinoctets": ("in", 32), "ifoutoctets": ("out", 32),
    "ifhcinoctets": ("in", 64), "ifhcoutoctets": ("out", 64),
    "1.3.6.1.2.1.2.2.1.10": ("in", 32), "1.3.6.1.2.1.2.2.1.16": ("out", 32),
    "1.3.6.1.2.1.31.1.1.1.6": ("in", 64), "1.3.6.1.2.1.31.1.1.1.10": ("out", 64),
}  # fmt: skip
_ESCAPE = re.compile(r"\\(.)")
_UNESCAPED_SPACE = re.compile(r"(?<!\\)\s+")
_OPERATOR = re.compile(r"^[-+*/()]+$|^\d+(\.\d+)?$")


@dataclass(frozen=True, slots=True)
class TargetSpec:
    """What one ``Target[]`` value graphs.

    Attributes:
        kind: ``ifindex``, ``ifname``, ``ifdescr``, ``ip``, ``mac``, ``type``,
            ``oid``, ``expression``, ``script``, or ``invalid``.
        key: The interface key (the ``ifIndex`` digits, name, address, ...).
        reversed: True when MRTG's in is the interface's out.
        host: Device host name or address, without brackets.
        port: SNMP port, or ``""`` for the default.
        version: SNMP version field, ``""`` meaning 1.
        counter_bits: 64 or 32 for interface octets, 0 otherwise.
        oids: For ``oid`` targets, the two OIDs without their lookups.
        hosts: Every host the value names (expressions name several).
        communities: Every community or SNMPv3 user; hidden from ``repr``.
    """

    kind: str
    key: str = ""
    reversed: bool = False
    host: str = ""
    port: str = ""
    version: str = ""
    counter_bits: int = 0
    oids: tuple[str, ...] = ()
    hosts: tuple[str, ...] = ()
    communities: frozenset[str] = field(default=frozenset(), repr=False)

    @property
    def comparable(self) -> bool:
        """Report whether this target is one interface's octet counters.

        Args:
            None

        Returns:
            bool: True for the interface kinds MRTG resolves to one ``ifIndex``.

        Raises:
            None
        """
        return self.kind in COMPARABLE and bool(self.host) and self.counter_bits > 0


def unescape(text: str) -> str:
    r"""Remove MRTG's backslash escapes (``\@``, ``\ ``, ``\:``).

    Args:
        text: Escaped text.

    Returns:
        str: Plain text.

    Raises:
        None
    """
    return _ESCAPE.sub(r"\1", text)


def _unescaped(text: str, char: str, *, last: bool = False) -> int:
    """Find the first (or last) occurrence of ``char`` not escaped by a backslash.

    Args:
        text: Text to search.
        char: One character.
        last: Search from the right.

    Returns:
        int: Index, or -1.

    Raises:
        None
    """
    hits = [m.start() for m in re.finditer(re.escape(char), text)
            if m.start() == 0 or text[m.start() - 1] != "\\"]  # fmt: skip
    return (hits[-1] if last else hits[0]) if hits else -1


def _host_part(text: str) -> tuple[str, str, str]:
    """Split ``router[:port[:timeout[:retries[:backoff[:version]]]]][|name]``.

    Args:
        text: Everything after the ``@``.

    Returns:
        tuple[str, str, str]: Host (no brackets), port, and version field.

    Raises:
        None
    """
    text = text.split("|", 1)[0].strip()
    if text.startswith("["):
        host, _, rest = text[1:].partition("]")
        fields = rest.split(":")[1:]
    else:
        host, *fields = text.split(":")
    port = fields[0] if fields else ""
    version = fields[4] if len(fields) >= 5 else ""
    return host.strip(), port.strip(), version.strip()


def _split_oid(oid: str) -> tuple[str, str, str]:
    """Split one OID into its base, the separator, and the instance or lookup.

    Args:
        oid: Such as ``ifHCInOctets.2``, ``1.3.6.1.2.1.2.2.1.10.2``, or
            ``ifInOctets#Gi0/1``.

    Returns:
        tuple[str, str, str]: Base, separator (``.`` or a lookup prefix), and
        the rest; the separator is ``""`` when the OID is not understood.

    Raises:
        None
    """
    found = [i for i in (_unescaped(oid, c) for c in LOOKUPS) if i > 0]
    if found:
        at = min(found)
        return oid[:at], oid[at], unescape(oid[at + 1 :])
    for base in sorted(_OCTETS, key=len, reverse=True):
        if oid.lower().startswith(base + ".") and oid[len(base) + 1 :].isdigit():
            return oid[: len(base)], ".", oid[len(base) + 1 :]
    return oid, "", ""


def _oid_pair(text: str) -> tuple[str, str, int, bool, tuple[str, ...]]:
    """Recognise ``ifInOctets.2&ifOutOctets.2`` and its numeric and lookup forms.

    Args:
        text: The two OIDs joined by ``&``.

    Returns:
        tuple: Kind, key, counter width, reversed, and the bare OIDs. The kind
        is ``oid`` when the pair is not one interface's in and out octets.

    Raises:
        None
    """
    parts = [_split_oid(oid) for oid in text.split("&", 1)]
    bare = tuple(p[0] for p in parts)
    meaning = [_OCTETS.get(p[0].lower()) for p in parts]
    first, second = meaning if len(meaning) == 2 else (None, None)
    if first is None or second is None or parts[0][1:] != parts[1][1:] or not parts[0][1]:
        return "oid", "", 0, False, bare
    if {first[0], second[0]} != {"in", "out"} or first[1] != second[1]:
        return "oid", "", 0, False, bare
    sep, ref = parts[0][1], parts[0][2]
    kind = "ifindex" if sep == "." else LOOKUPS[sep]
    return kind, ref, first[1], first[0] == "out", bare


def _single(text: str) -> TargetSpec:
    """Parse one ``interface:community@host`` spec.

    Args:
        text: One spec, possibly with escapes.

    Returns:
        TargetSpec: The parsed spec; ``invalid`` when it does not parse.

    Raises:
        None
    """
    at = _unescaped(text, "@", last=True)
    colon = _unescaped(text[:at], ":") if at > 0 else -1
    if colon <= 0:
        return TargetSpec("invalid")
    left, community = text[:colon], unescape(text[colon + 1 : at])
    host, port, version = _host_part(text[at + 1 :])
    reversed_ = left.startswith("-")
    left = left.removeprefix("-")
    bits = 64 if version in ("2", "3") else 32
    if left.isdigit():
        kind, key, oids = "ifindex", left, ()
    elif left[:1] in LOOKUPS:
        kind, key, oids = LOOKUPS[left[0]], unescape(left[1:]), ()
    elif "&" in left:
        kind, key, bits, flipped, oids = _oid_pair(left)
        reversed_ = reversed_ != flipped
    else:
        return TargetSpec("invalid", host=host, hosts=(host,), communities=frozenset({community}))
    bits = bits if kind in COMPARABLE else 0
    return TargetSpec(kind, key, reversed_, host, port, version, bits, oids, (host,),
                      frozenset({community}))  # fmt: skip


def parse_target(value: str) -> TargetSpec:
    """Parse a whole ``Target[]`` value: one spec, an expression, or a script.

    Args:
        value: The keyword's value, continuation lines already joined.

    Returns:
        TargetSpec: For an expression, kind ``expression`` with every host
        and community of its operands; for a script, kind ``script``.

    Raises:
        None
    """
    text = value.strip()
    if text.startswith("`"):
        return TargetSpec("script")
    tokens = [t for t in _UNESCAPED_SPACE.split(text) if t]
    specs = [_single(t) for t in tokens if not _OPERATOR.match(t)]
    if len(tokens) == 1 and specs:
        return specs[0]
    hosts = tuple(dict.fromkeys(h for s in specs for h in s.hosts))
    communities = frozenset(c for s in specs for c in s.communities)
    return TargetSpec("expression", hosts=hosts, communities=communities,
                      host=hosts[0] if len(hosts) == 1 else "")  # fmt: skip
