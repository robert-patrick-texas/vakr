"""The end-to-end fleet: devices, interfaces, and how each one behaves (WEB-T01, WEB-T03, WEB-T11).

One fixed plan feeds both the metadata seed and the telemetry seed, so the
Playwright suite can name what it expects:

- every status bucket (UI-005, WEB-T01): ``branch3`` is Down (ICMP
  unreachable), ``branch9`` is stale (last poll 15 minutes ago, "Unknown —
  no fresh observation"), ``branch7`` has packet loss, six devices are OK
  (``access2`` sorts before ``access10``: natural order), ``lab-sw`` is
  disabled, and ``old-rtr`` is retired (hidden unless asked for);
- ``core1`` has 30 interfaces (UI-006, UI-016, UI-017, WEB-T03, WEB-T11):
  two sticky (``Te1/0/1``, ``Te1/0/2``), two WAN circuits (``Gi0/0/1``
  ACME, ``Gi0/0/2`` Globex), two primary-interest (``Gi0/0/3``,
  ``Gi0/0/4``), and 24 ordinary ``Gi1/0/1`` to ``Gi1/0/24``;
- ``dist1`` carries an open identity review for ``ifIndex`` 5.

Addresses are documentation ranges (RFC 5737); names are invented.

Dependencies:
    dataclasses: The plan records.

Example:
    >>> [d.name for d in DEVICES if d.behavior == "down"]
    ['branch3']
"""

from dataclasses import dataclass, field

OK, DOWN, LOSS, STALE, DISABLED, RETIRED = "ok", "down", "loss", "stale", "disabled", "retired"
POLLED = frozenset({OK, DOWN, LOSS})
"""Behaviours the fixture collector keeps polling, so their state never decays."""


@dataclass(frozen=True, slots=True)
class IfaceSpec:
    """One planned interface.

    Attributes:
        name: ``ifName``.
        if_index: ``ifIndex``.
        alias: ``ifAlias``.
        sticky: Pinned to the top (``sticky``).
        wan: A WAN circuit.
        primary: Primary interest (high priority, META-011).
        provider: Carrier.
        circuit: Circuit ID.
        contracted_mbps: Contracted rate, Mbit/s.
        speed_mbps: ``ifHighSpeed``.
        history_h: Hours of native samples to seed.
        mac: ``ifPhysAddress``.
        addresses: IP interface addresses.
        errors: Inbound errors in every sample, so error rankings always have a row.
    """

    name: str
    if_index: int
    alias: str = ""
    sticky: bool = False
    wan: bool = False
    primary: bool = False
    provider: str | None = None
    circuit: str | None = None
    contracted_mbps: int | None = None
    speed_mbps: int = 1000
    history_h: int = 6
    mac: str | None = None
    addresses: tuple[str, ...] = ()
    errors: int = 0


@dataclass(frozen=True, slots=True)
class DeviceSpec:
    """One planned device.

    Attributes:
        name: Display name.
        address: Poll address.
        behavior: ``ok``, ``down``, ``loss``, ``stale``, ``disabled``, or ``retired``.
        site: Site code, or ``None``.
        tags: Tag names.
        groups: Group names.
        interfaces: Planned interfaces.
    """

    name: str
    address: str
    behavior: str
    site: str | None = None
    tags: tuple[str, ...] = ()
    groups: tuple[str, ...] = ()
    interfaces: tuple[IfaceSpec, ...] = field(default_factory=tuple)


SITES = (
    ("Dallas DC1", "DAL1", "America/Chicago"),
    ("Austin", "AUS1", None),
    ("Frankfurt", "FRA1", "Europe/Berlin"),
)
"""Name, code, and site timezone; Frankfurt differs from the display zone (TIME-003)."""
TAGS = ("wan", "core")
GROUPS = ("backbone", "branches")
COLLECTORS = ("east", "west")
"""``east`` is the local collector and stays healthy; ``west`` stopped reporting (WEB-T07)."""


def _core1_interfaces() -> tuple[IfaceSpec, ...]:
    """Plan ``core1``'s 30 interfaces.

    Args:
        None

    Returns:
        tuple[IfaceSpec, ...]: Sticky, WAN, primary, then 24 ordinary ports.

    Raises:
        None
    """
    special = (
        IfaceSpec("Gi0/0/1", 1, "ACME internet", wan=True, provider="ACME", circuit="ACME-1001",
                  contracted_mbps=500, history_h=24, mac="00:00:5e:00:53:11",
                  addresses=("192.0.2.1/30",)),
        IfaceSpec("Gi0/0/2", 2, "Globex MPLS", wan=True, provider="Globex", circuit="GLX-2002",
                  contracted_mbps=200, history_h=24, addresses=("198.51.100.1/30",)),
        IfaceSpec("Gi0/0/3", 3, "server farm", primary=True),
        IfaceSpec("Gi0/0/4", 4, "storage", primary=True),
        IfaceSpec("Te1/0/1", 101, "core uplink A", sticky=True, speed_mbps=10000),
        IfaceSpec("Te1/0/2", 102, "core uplink B", sticky=True, speed_mbps=10000),
    )  # fmt: skip
    ports = tuple(IfaceSpec(f"Gi1/0/{n}", 200 + n, f"access port {n}", errors=2 if n == 5 else 0)
                  for n in range(1, 25))  # fmt: skip
    return special + ports


def _small(names: tuple[str, ...], wan: IfaceSpec | None = None) -> tuple[IfaceSpec, ...]:
    """Plan a few ordinary interfaces, optionally after one WAN circuit.

    Args:
        names: ``ifName`` values.
        wan: A WAN circuit to put first.

    Returns:
        tuple[IfaceSpec, ...]: The interfaces.

    Raises:
        None
    """
    plain = tuple(IfaceSpec(n, i + 10, history_h=1) for i, n in enumerate(names))
    return ((wan,) if wan else ()) + plain


DIST1_INTERFACES = (
    IfaceSpec("Te0/0/5", 5, "uplink", history_h=1, addresses=("192.0.2.9/31",)),
    IfaceSpec("Te0/0/6", 6, "uplink spare", history_h=1),
    *_small(("Gi0/0/1", "Gi0/0/2", "Gi0/0/3", "Gi0/0/4")),
)
EDGE_WAN = IfaceSpec("Gi0/0/0", 1, "Globex branch", wan=True, provider="Globex", circuit="GLX-77",
                     contracted_mbps=100, history_h=1)  # fmt: skip

DEVICES = (
    DeviceSpec("core1", "192.0.2.10", OK, "DAL1", ("core",), ("backbone",), _core1_interfaces()),
    DeviceSpec("dist1", "192.0.2.11", OK, "DAL1", ("core",), ("backbone",), DIST1_INTERFACES),
    DeviceSpec("edge-a", "192.0.2.20", OK, "AUS1", ("wan",), (), _small(("Gi0/0/1",), EDGE_WAN)),
    DeviceSpec("edge-b", "192.0.2.21", OK, "FRA1", ("wan",), (), _small(("Gi0/0/0", "Gi0/0/1"))),
    DeviceSpec("access2", "192.0.2.30", OK, "AUS1", (), (), _small(("Gi1/0/1", "Gi1/0/2"))),
    DeviceSpec("access10", "192.0.2.31", OK, "AUS1", (), (), _small(("Gi1/0/1", "Gi1/0/2"))),
    DeviceSpec("branch3", "198.51.100.3", DOWN, "AUS1", ("wan",), ("branches",)),
    DeviceSpec("branch7", "198.51.100.7", LOSS, "AUS1", ("wan",), ("branches",)),
    DeviceSpec("branch9", "198.51.100.9", STALE, "AUS1", (), ("branches",)),
    DeviceSpec("lab-sw", "203.0.113.5", DISABLED),
    DeviceSpec("old-rtr", "203.0.113.6", RETIRED),
)
"""The whole fleet, inserted in this (deliberately unsorted) order."""

FILE_MANAGED = frozenset({"edge-b", "edge-b/Gi0/0/1"})
"""Records ``inventory.toml`` manages (a device and one claimed interface), for the manual-entry
pages' read-only state (META-008); every other record is API-managed."""

EXPECTED_ORDER = ("branch3", "branch9", "branch7", "access2", "access10", "core1", "dist1",
                  "edge-a", "edge-b", "lab-sw")  # fmt: skip
"""Default device-list order (7.7, E.2 severity, natural names); ``old-rtr`` is hidden."""
