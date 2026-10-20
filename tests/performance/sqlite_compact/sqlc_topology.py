# A seeded, reproducible PRNG is the point: the workload must be identical on
# every re-run. Nothing generated here is a secret.
# ruff: noqa: S311
"""Seeded inventory for the ADR-021 benchmark: 100 devices, 1,600 interfaces, 2 collectors.

The MVP reference workload (blueprint 27.2) is 80 routers with about 10
active interfaces each and 20 switches with about 40 each. Two positions
poll everything, and each node stores its peer's observations too (27.5),
so one node holds 3,200 interface series and 200 ICMP and 200 SNMP device
series. Everything here derives from one integer seed, so a re-run builds
byte-identical keys, UUIDs, capacities, and traffic profiles.

Dependencies:
    random: ``random.Random(seed)`` for reproducible, non-secret values.
    vakr.domain.enums: Shared state vocabularies; the benchmark stores them
        as small integer codes (see ``vocab_codes``).

Safety:
    Generated UUIDs come from a seeded PRNG on purpose, so they are
    predictable. Production observation UUIDs come from ``uuid.uuid7()``.
    No credentials or community strings exist anywhere in this workload.

Example:
    >>> topo = build_topology(seed=21)
    >>> len(topo.devices), len(topo.interfaces), len(topo.collectors)
    (100, 1600, 2)
    >>> len(uuid7_bytes(1_760_000_000_000, 5))
    16
"""

import random
from dataclasses import dataclass
from enum import Enum

DAY_US = 86_400_000_000
MINUTE_US = 60_000_000
GBPS = 1_000_000_000.0


@dataclass(frozen=True, slots=True)
class Interface:
    """One monitored up/up interface and its synthetic traffic profile. Immutable.

    Attributes:
        key (int): Integer surrogate stored in telemetry rows (1-based).
        uuid (bytes): 16-byte persistent interface UUID (ADR-004).
        device_key (int): Owning device's surrogate key.
        ifindex (int): Observed ifIndex; identity never depends on it.
        capacity_bps (float): Speed used for utilization rankings.
        base_util (float): Peak-hour utilization fraction before noise.
        out_ratio (float): Outbound rate as a multiple of inbound.
        pkt_bytes (float): Mean packet size, which sets packet rates.
        error_prone (bool): True for the few interfaces with error bursts.
    """

    key: int
    uuid: bytes
    device_key: int
    ifindex: int
    capacity_bps: float
    base_util: float
    out_ratio: float
    pkt_bytes: float
    error_prone: bool


@dataclass(frozen=True, slots=True)
class Device:
    """One polled device and its per-collector timing. Immutable.

    Attributes:
        key (int): Integer surrogate stored in telemetry rows (1-based).
        uuid (bytes): 16-byte device UUID.
        is_switch (bool): Switches carry 40 active interfaces, routers 10.
        address (bytes): Packed IPv4 management address.
        phase_us (tuple[int, ...]): Poll offset within the minute, per collector.
        rtt_us (tuple[int, ...]): Base round-trip time, per collector.
        iface_keys (tuple[int, ...]): Keys of the device's interfaces.
    """

    key: int
    uuid: bytes
    is_switch: bool
    address: bytes
    phase_us: tuple[int, ...]
    rtt_us: tuple[int, ...]
    iface_keys: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class Topology:
    """The whole seeded inventory. Immutable.

    Attributes:
        seed (int): Seed every other value derives from.
        collectors (tuple[bytes, ...]): Collector UUIDs; key ``i`` is index ``i-1``.
        devices (tuple[Device, ...]): Devices ordered by key.
        interfaces (tuple[Interface, ...]): Interfaces ordered by key.
    """

    seed: int
    collectors: tuple[bytes, ...]
    devices: tuple[Device, ...]
    interfaces: tuple[Interface, ...]


def random_uuid4(rng: random.Random) -> bytes:
    """Return 16 bytes shaped like a version-4 UUID, drawn from ``rng``.

    Args:
        rng (random.Random): Seeded generator; the result is reproducible.

    Returns:
        bytes: Big-endian UUID bytes with version 4 and the RFC 9562 variant.

    Raises:
        None
    """
    value = rng.getrandbits(128)
    value = (value & ~(0xF << 76)) | (0x4 << 76)
    value = (value & ~(0x3 << 62)) | (0x2 << 62)
    return value.to_bytes(16)


def uuid7_bytes(ts_ms: int, rand74: int) -> bytes:
    """Build a UUIDv7 from a millisecond timestamp and 74 random bits (RFC 9562).

    The 48-bit timestamp prefix makes UUIDv7 values roughly time-ordered,
    which is why a unique index on them appends near the right edge of the
    B-tree instead of splitting pages at random.

    Args:
        ts_ms (int): Unix time in milliseconds.
        rand74 (int): 74 random bits for ``rand_a`` (12) and ``rand_b`` (62).

    Returns:
        bytes: The 16-byte UUID.

    Raises:
        None
    """
    rand_a = (rand74 >> 62) & 0xFFF
    rand_b = rand74 & ((1 << 62) - 1)
    value = (ts_ms & 0xFFFF_FFFF_FFFF) << 80 | 0x7 << 76 | rand_a << 64 | 0x2 << 62 | rand_b
    return value.to_bytes(16)


def vocab_codes[E: Enum](enum_cls: type[E]) -> dict[E, int]:
    """Map each member of a shared vocabulary enum to a 1-based storage code.

    Codes follow definition order, which is append-only for these enums.
    SQLite stores the integers 0 and 1 in zero payload bytes, so the first
    (most common) member costs only its one-byte header.

    Args:
        enum_cls (type[E]): An enum from ``vakr.domain.enums``.

    Returns:
        dict[E, int]: Member to integer code.

    Raises:
        None
    """
    return {member: code for code, member in enumerate(enum_cls, start=1)}


def _interfaces_for(
    rng: random.Random, device_key: int, count: int, first_key: int
) -> list[Interface]:
    """Create one device's interfaces: two 10G uplinks, then access or transit ports.

    Args:
        rng (random.Random): Seeded generator.
        device_key (int): Owning device key.
        count (int): Number of active interfaces on the device.
        first_key (int): Surrogate key of the first interface.

    Returns:
        list[Interface]: The device's interfaces in key order.

    Raises:
        None
    """
    out = []
    for i in range(count):
        uplink = i < 2
        capacity = 10 * GBPS if uplink else rng.choice((GBPS, GBPS, GBPS, 0.1 * GBPS))
        util = rng.uniform(0.15, 0.6) if uplink else min(0.9, rng.lognormvariate(-3.0, 1.0))
        out.append(
            Interface(
                key=first_key + i,
                uuid=random_uuid4(rng),
                device_key=device_key,
                ifindex=1 + i if uplink else 10_000 + i,
                capacity_bps=capacity,
                base_util=util,
                out_ratio=rng.uniform(0.25, 1.4),
                pkt_bytes=rng.uniform(300.0, 1100.0),
                error_prone=rng.random() < 0.05,
            )
        )
    return out


def build_topology(
    seed: int, routers: int = 80, switches: int = 20, collectors: int = 2
) -> Topology:
    """Build the reference inventory: routers first, then switches (blueprint 27.2).

    Args:
        seed (int): Seed for every derived value.
        routers (int): Router count; 10 active interfaces each.
        switches (int): Switch count; 40 active interfaces each.
        collectors (int): Positions storing observations on this node.

    Returns:
        Topology: The seeded inventory.

    Raises:
        None
    """
    rng = random.Random(seed)
    coll_uuids = tuple(random_uuid4(rng) for _ in range(collectors))
    devices, interfaces = [], []
    for d in range(routers + switches):
        key, is_switch = d + 1, d >= routers
        ifaces = _interfaces_for(rng, key, 40 if is_switch else 10, len(interfaces) + 1)
        interfaces.extend(ifaces)
        # The far position sees every device 55-70 ms further away (27.2).
        near = rng.randint(400, 25_000)
        rtts = tuple(
            near + (0 if c == key % collectors else rng.randint(55_000, 70_000))
            for c in range(collectors)
        )
        devices.append(
            Device(
                key=key,
                uuid=random_uuid4(rng),
                is_switch=is_switch,
                address=bytes((10, 20 + d // 250, d % 250, 1)),
                phase_us=tuple(rng.randrange(0, 59_000_000) for _ in range(collectors)),
                rtt_us=rtts,
                iface_keys=tuple(i.key for i in ifaces),
            )
        )
    return Topology(seed, coll_uuids, tuple(devices), tuple(interfaces))
