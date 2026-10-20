"""Value types for the simulated fleet: one device and its interfaces.

Dependencies:
    dataclasses: Immutable descriptions.

Example:
    >>> Port(1, "GigabitEthernet0/0/0", 6, 10**9, 1000).up
    True
"""

from dataclasses import dataclass

SATURATED = 4_294_967_295  # ifSpeed for links faster than 4.29 Gb/s (RFC 2863)


@dataclass(frozen=True, slots=True)
class Port:
    """One simulated interface.

    Attributes:
        if_index: The ifIndex.
        name: ``ifName``.
        if_type: IANAifType number.
        speed_bps: ``ifSpeed`` as the agent reports it.
        high_speed: ``ifHighSpeed`` in Mb/s.
        admin: ``ifAdminStatus`` (1 up, 2 down).
        oper: ``ifOperStatus`` (1 up, 2 down, 6 notPresent, 7 lowerLayerDown).
        mtu: ``ifMtu``.
        alias: ``ifAlias``, the description.
        mac: ``ifPhysAddress`` octets; empty for logical interfaces.
        hc: Whether the ifHC* counters exist.
        discontinuity: ``ifCounterDiscontinuityTime``, or ``None`` if absent.
        descr: ``ifDescr`` when it differs from ``name``.
        addresses: Interface addresses such as ``192.0.2.0/31``.
        rate_bps: Simulated inbound traffic while up/up.
        error_rate: Inbound errors per second, or ``None`` for none.
    """

    if_index: int
    name: str
    if_type: int
    speed_bps: int
    high_speed: int
    admin: int = 1
    oper: int = 1
    mtu: int = 1500
    alias: str = ""
    mac: bytes = b""
    hc: bool = True
    discontinuity: int | None = None
    descr: str | None = None
    addresses: tuple[str, ...] = ()
    rate_bps: int = 0
    error_rate: int | None = None

    @property
    def up(self) -> bool:
        """Whether the interface is admin up and oper up.

        Args:
            None

        Returns:
            bool: True for up/up.

        Raises:
            None
        """
        return self.admin == 1 and self.oper == 1


@dataclass(frozen=True, slots=True)
class DeviceSpec:
    """One simulated device.

    Attributes:
        kind: ``router`` or ``switch``.
        number: Position in its kind, from 1.
        name: Short host name, such as ``rtr-sim-001``.
        ports: The interfaces, in ifIndex order.
        ip_mib: ``both``, ``modern``, or ``legacy`` IP-MIB tables.
    """

    kind: str
    number: int
    name: str
    ports: tuple[Port, ...]
    ip_mib: str = "both"

    @property
    def seed(self) -> int:
        """Return the number every derived value is computed from.

        Args:
            None

        Returns:
            int: Unique per device across both kinds.

        Raises:
            None
        """
        return self.number if self.kind == "router" else 1_000 + self.number
