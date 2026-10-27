# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Match MRTG targets and SmokePing hosts to the application's interfaces and devices.

A device matches when any of its aliases (name, first label, poll address,
probed addresses) equals any alias of the MRTG or SmokePing host, which
the field kit pseudonymizes the same way on every host that shares the
pseudonym key. The interface is then found the way MRTG found it:

* ``ifindex``: the interface whose samples were read from that ``ifIndex``
  (the application never uses ``ifIndex`` as identity, but every sample
  records the one it was read from; only those samples are compared);
* ``ifname`` and ``ifdescr``: equal names;
* ``ip``: an interface address; ``mac``: the same 12 hex digits.

Two candidates are never merged: an ambiguous match is reported, as are
targets with no match (invariant 7 applied to the comparison).

Dependencies:
    acc_vakr: The application's devices and interfaces.

Safety:
    Pure matching over loaded records.

Example:
    >>> mac_key("00-1A-2b-3c-4d-5e") == mac_key("00:1a:2b:3c:4d:5e") == "001a2b3c4d5e"
    True
"""

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from acc_vakr import Device, Export, Interface

_HEX = re.compile(r"[^0-9a-f]")


@dataclass(frozen=True, slots=True)
class Match:
    """The outcome of matching one target.

    Attributes:
        device: The matched device, or ``None``.
        interface: The matched interface, or ``None`` (always ``None`` for ICMP).
        problem: Why there is no match, or ``""``.
    """

    device: Device | None
    interface: Interface | None = None
    problem: str = ""


def mac_key(text: str) -> str:
    """Reduce a MAC address in any notation to 12 lower-case hex digits.

    Args:
        text: MAC text.

    Returns:
        str: Twelve hex digits, or ``""``.

    Raises:
        None
    """
    parts = re.split(r"[:.-]", text.strip().lower())
    width = 2 if len(parts) == 6 else 4 if len(parts) == 3 else 12
    joined = "".join(p.zfill(width) for p in parts)
    return joined if len(joined) == 12 and not _HEX.search(joined) else ""


def match_device(export: Export, aliases: Iterable[str]) -> Match:
    """Find the one device that shares an alias.

    Args:
        export: The application's export.
        aliases: The other tool's aliases for the host.

    Returns:
        Match: The device, or the problem.

    Raises:
        None
    """
    wanted = {a.lower() for a in aliases}
    found = [d for d in export.devices.values() if d.aliases & wanted]
    if not found:
        return Match(None, problem="no device of the application has this host's names")
    if len(found) > 1:
        return Match(None, problem=f"{len(found)} devices share this host's names; not merged")
    return Match(found[0])


def _by_index(candidates: list[Interface], key: str) -> list[Interface]:
    """Choose the interface whose samples were read from ``ifIndex`` ``key``.

    Args:
        candidates: The device's interfaces.
        key: The ``ifIndex`` digits.

    Returns:
        list[Interface]: The interface with most such samples, or every one
        whose current ``ifIndex`` matches when none has samples.

    Raises:
        None
    """
    index = int(key) if key.isdigit() else -1
    counts = Counter({i.interface_id: sum(1 for s in i.samples.values() if s.if_index == index)
                      for i in candidates})  # fmt: skip
    best = [i for i in candidates if counts[i.interface_id] and
            counts[i.interface_id] == max(counts.values())]  # fmt: skip
    return best or [i for i in candidates if i.if_index == index]


def match_interface(export: Export, aliases: Iterable[str], kind: str, key: str) -> Match:
    """Find the interface an MRTG target graphs.

    Args:
        export: The application's export.
        aliases: The MRTG host's aliases.
        kind: ``ifindex``, ``ifname``, ``ifdescr``, ``ip``, or ``mac``.
        key: The selector's value.

    Returns:
        Match: Device and interface, or the problem.

    Raises:
        None
    """
    device = match_device(export, aliases)
    if device.device is None:
        return device
    mine = [i for i in export.interfaces.values() if i.device_id == device.device.device_id]
    tests = {
        "ifname": lambda i: i.if_name == key,
        "ifdescr": lambda i: i.if_descr == key,
        "ip": lambda i: key.lower() in i.addresses,
        "mac": lambda i: bool(mac_key(key)) and mac_key(i.mac) == mac_key(key),
    }
    if kind == "ifindex":
        found = _by_index(mine, key)
    elif kind in tests:
        found = [i for i in mine if tests[kind](i)]
    else:
        return Match(device.device, problem=f"MRTG selects by {kind}; not comparable")
    if not found:
        return Match(device.device, problem=f"no exported interface with this {kind}")
    if len(found) > 1:
        return Match(device.device, problem=f"{len(found)} interfaces match this {kind}")
    return Match(device.device, found[0])
