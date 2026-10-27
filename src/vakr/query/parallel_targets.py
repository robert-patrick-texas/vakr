r"""Which interfaces and devices ``vakr acceptance export`` reads (MVP-ACC-03, MVP-ACC-04).

During the parallel run, vakr's numbers are compared with MRTG's for the
interfaces MRTG graphs and with SmokePing's for the hosts SmokePing probes.
A targets file names them, one per line, so the export reads only what the
comparison needs. The field kit writes the file from the MRTG and SmokePing
configuration it parses; an operator can also write one by hand.

Two line forms are accepted. A JSON object, as the field kit writes it::

    {"kind": "interface", "device": ["rtr1.example.net", "192.0.2.1"], "ifindex": 2}
    {"kind": "icmp", "device": ["rtr1.example.net"]}

or plain text, ``KIND DEVICE[|ALIAS...] [SELECTOR=VALUE]``, where the value
is the rest of the line, so an ``ifDescr`` may contain spaces::

    interface rtr1.example.net|192.0.2.1 ifname=Gi0/0/1
    interface 192.0.2.7 ifdescr=GigabitEthernet0/0/1 - uplink
    icmp rtr1.example.net

Selectors are ``ifindex``, ``ifname``, ``ifdescr``, ``ip``, ``mac``, and
``all``. A device matches when any alias equals its name, its name's first
label, or its poll address, ignoring case. ``ifIndex`` selects the
interface that has that ``ifIndex`` now; every exported row carries the
``ifIndex`` it was read from, because ``ifIndex`` is never identity
(invariant 6) and MRTG polls by number.

Dependencies:
    json, re: Line parsing.
    vakr.domain: Devices, interfaces, and validation errors.

Safety:
    The file holds names and addresses only; parse errors name the line
    number, never echo the line.

Example:
    >>> spec = parse_targets("interface rtr1|192.0.2.1 ifindex=2\n")[0]
    >>> spec.kind, spec.aliases, spec.selector, spec.value
    ('interface', ('rtr1', '192.0.2.1'), 'ifindex', '2')
"""

import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from vakr.domain.errors import ValidationError
from vakr.domain.interfaces import Interface
from vakr.domain.inventory import Device

MAX_TARGET_LINES = 20_000
"""Most targets one file may name (E.34)."""
MAX_LINE_CHARS = 1_000
SELECTORS = frozenset({"ifindex", "ifname", "ifdescr", "ip", "mac", "all"})
KINDS = frozenset({"interface", "icmp"})
_HEX = re.compile(r"[^0-9a-f]")


@dataclass(frozen=True, slots=True)
class TargetSpec:
    """One line of a targets file.

    Attributes:
        line: Line number, for reports.
        kind: ``interface`` or ``icmp``.
        aliases: Device names or addresses, lower-cased; any one may match.
        selector: For interfaces, how the interface is named; ``""`` for ICMP.
        value: The selector's value (``""`` for ``all`` and ICMP).
    """

    line: int
    kind: str
    aliases: tuple[str, ...]
    selector: str = ""
    value: str = ""


def _from_json(line: int, text: str) -> TargetSpec:
    """Parse one JSON target line.

    Args:
        line: Line number.
        text: The line.

    Returns:
        TargetSpec: The target.

    Raises:
        ValidationError: If the object is not a valid target.
    """
    try:
        data = json.loads(text)
    except ValueError:
        raise ValidationError(f"targets line {line}: not valid JSON", field="targets") from None
    if not isinstance(data, dict):
        raise ValidationError(f"targets line {line}: not a JSON object", field="targets")
    device = data.get("device")
    aliases = [device] if isinstance(device, str) else device
    if not isinstance(aliases, list) or not all(isinstance(a, str) for a in aliases):
        raise ValidationError(f"targets line {line}: device must be text", field="targets")
    found = [k for k in SELECTORS if k in data]
    selector = found[0] if len(found) == 1 else ""
    value = "" if selector in ("", "all") else str(data[selector])
    return _checked(TargetSpec(line, str(data.get("kind", "")), _aliases(aliases), selector, value))


def _from_text(line: int, text: str) -> TargetSpec:
    """Parse one plain-text target line.

    Args:
        line: Line number.
        text: The stripped line.

    Returns:
        TargetSpec: The target.

    Raises:
        ValidationError: If the line is malformed.
    """
    parts = text.split(None, 2)
    if len(parts) < 2:
        raise ValidationError(f"targets line {line}: expected KIND DEVICE", field="targets")
    kind, device = parts[0].lower(), parts[1]
    selector, value = "", ""
    if len(parts) == 3:
        selector, _, value = parts[2].partition("=")
        selector = selector.strip().lower()
        value = "" if selector == "all" else value.strip()
    return _checked(TargetSpec(line, kind, _aliases(device.split("|")), selector, value))


def _aliases(names: Iterable[str]) -> tuple[str, ...]:
    """Normalise device aliases: stripped, lower-cased, no blanks or repeats.

    Args:
        names: Raw aliases.

    Returns:
        tuple[str, ...]: Aliases in their given order.

    Raises:
        None
    """
    return tuple(dict.fromkeys(n.strip().lower() for n in names if n.strip()))


def _checked(spec: TargetSpec) -> TargetSpec:
    """Validate a parsed target.

    Args:
        spec: The target.

    Returns:
        TargetSpec: The same target.

    Raises:
        ValidationError: If the kind, aliases, or selector are invalid.
    """
    where = f"targets line {spec.line}"
    if spec.kind not in KINDS:
        raise ValidationError(f"{where}: kind must be interface or icmp", field="targets")
    if not spec.aliases:
        raise ValidationError(f"{where}: no device", field="targets")
    if spec.kind == "icmp":
        return TargetSpec(spec.line, spec.kind, spec.aliases)
    if spec.selector not in SELECTORS:
        raise ValidationError(f"{where}: selector must be one of {sorted(SELECTORS)}",
                              field="targets")  # fmt: skip
    if spec.selector != "all" and not spec.value:
        raise ValidationError(f"{where}: {spec.selector} needs a value", field="targets")
    if spec.selector == "ifindex" and not spec.value.isdigit():
        raise ValidationError(f"{where}: ifindex must be a number", field="targets")
    return spec


def parse_targets(text: str) -> list[TargetSpec]:
    """Parse a targets file; ``#`` starts a comment line.

    Args:
        text: File content.

    Returns:
        list[TargetSpec]: The targets in file order.

    Raises:
        ValidationError: On the first malformed line, or if there are more
            than ``MAX_TARGET_LINES`` targets.
    """
    found: list[TargetSpec] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if len(stripped) > MAX_LINE_CHARS:
            raise ValidationError(f"targets line {number}: too long", field="targets")
        parse = _from_json if stripped.startswith("{") else _from_text
        found.append(parse(number, stripped))
        if len(found) > MAX_TARGET_LINES:
            raise ValidationError(f"more than {MAX_TARGET_LINES} targets", field="targets")
    return found


def device_matches(spec: TargetSpec, device: Device) -> bool:
    """Report whether a target names a device.

    Args:
        spec: The target.
        device: A device.

    Returns:
        bool: True when an alias equals its name, its name's first label, or
        its poll address, ignoring case. Selecting a little more is
        harmless: the comparison joins on every alias.

    Raises:
        None
    """
    name = device.name.lower()
    names = {name, device.poll_address.lower(), name.split(".")[0]}
    return bool(names & set(spec.aliases))


def mac_key(text: str | None) -> str:
    """Reduce a MAC address in any notation to 12 lower-case hex digits.

    Args:
        text: ``00:1a:2b:..``, ``0-1a-2b-..``, ``001a.2b3c.4d5e``, or ``None``.

    Returns:
        str: Twelve hex digits, or ``""`` when it is not a MAC address.

    Raises:
        None
    """
    if not text:
        return ""
    parts = re.split(r"[:.-]", text.strip().lower())
    width = 2 if len(parts) == 6 else 4 if len(parts) == 3 else 12
    joined = "".join(p.zfill(width) for p in parts)
    return joined if len(joined) == 12 and not _HEX.search(joined) else ""


def interface_matches(spec: TargetSpec, iface: Interface) -> bool:
    """Report whether an interface target names an interface of a matching device.

    Args:
        spec: An interface target.
        iface: An interface of the device the target names.

    Returns:
        bool: True when the selector matches.

    Raises:
        None
    """
    value = spec.value
    tests: dict[str, Callable[[], bool]] = {
        "all": lambda: True,
        "ifindex": lambda: iface.if_index == int(value),
        "ifname": lambda: iface.if_name == value,
        "ifdescr": lambda: iface.if_descr == value,
        "ip": lambda: value.lower() in {a.lower().split("/")[0] for a in iface.addresses},
        "mac": lambda: bool(mac_key(value)) and mac_key(value) == mac_key(iface.mac),
    }
    test = tests.get(spec.selector)
    return test() if test else False
