"""Rows of the parallel-run export: samples, ICMP cycles, and the two catalogs.

Each interface sample becomes one compact CSV line (``RATE_COLUMNS``) and
each ICMP cycle one line (``ICMP_COLUMNS``); ``devices.csv`` and
``interfaces.csv`` say what the short keys stand for. Rates are bits per
second as vakr derived them (RATE-006); a rate vakr could not derive is an
empty cell, never ``0`` (invariant 1), and the sample's quality flags say
why. RTTs are microseconds with ``-1`` for a probe without a reply
(ICMP-004). Times are UTC epoch seconds with milliseconds.

The device catalog lists every name a comparison may know the device by
(``aliases``): its name and short name, its poll address, and the
addresses its ICMP cycles probed, so a host named one way in MRTG and
another in vakr still joins, also after the field kit pseudonymizes each
alias.

Dependencies:
    vakr.domain: Observations and quality flags.
    vakr.query.parallel_plan: Planned devices.

Safety:
    Pure formatting; no I/O.

Example:
    >>> from vakr.domain.quality import Quality
    >>> flag_names(Quality.BASELINE_GAP | Quality.RETRIED)
    'BASELINE_GAP|RETRIED'
    >>> short_label("rtr1.example.net"), short_label("192.0.2.1")
    ('rtr1', '')
"""

from collections.abc import Iterable
from datetime import datetime

from vakr.domain.enums import CounterFamily
from vakr.domain.observations import IcmpObservation, InterfaceObservation
from vakr.domain.quality import Quality
from vakr.domain.timestamps import to_epoch_us
from vakr.query.parallel_plan import PlannedDevice

RATE_COLUMNS = ("key", "end", "elapsed_s", "in_bps", "out_bps", "if_index", "counter_bits",
                "flags")  # fmt: skip
ICMP_COLUMNS = ("key", "start", "target_address", "sent", "received", "median_us", "rtts_us",
                "state", "flags")  # fmt: skip
INTERFACE_COLUMNS = ("key", "device_key", "interface_id", "if_index", "if_name", "if_descr",
                     "if_alias", "mac", "addresses", "if_speed", "if_high_speed", "lifecycle",
                     "lines")  # fmt: skip
DEVICE_COLUMNS = ("key", "device_id", "device", "poll_address", "aliases", "icmp")
_BITS = {CounterFamily.HC64: "64", CounterFamily.LEGACY32: "32"}


def _num(value: float | None) -> str:
    """Format a rate; a missing value is an empty cell, never zero.

    Args:
        value: The value.

    Returns:
        str: Nine significant digits, or ``""``.

    Raises:
        None
    """
    return "" if value is None else f"{value:.9g}"


def _epoch(moment: datetime) -> str:
    """Format a UTC time as epoch seconds with milliseconds.

    Args:
        moment: Aware UTC time.

    Returns:
        str: Such as ``1793091600.123``.

    Raises:
        None
    """
    return f"{to_epoch_us(moment) / 1e6:.3f}"


def flag_names(quality: Quality) -> str:
    """Name a sample's quality flags, ``|``-separated.

    Args:
        quality: The flags.

    Returns:
        str: Names such as ``BASELINE_GAP|RETRIED``, or ``""``.

    Raises:
        None
    """
    return "|".join(m.name or "" for m in Quality if m and m in quality)


def rate_row(key: str, obs: InterfaceObservation) -> str:
    """Encode one interface sample.

    Args:
        key: The interface's key.
        obs: The sample.

    Returns:
        str: One CSV line in ``RATE_COLUMNS`` order.

    Raises:
        None
    """
    elapsed = "" if obs.sample_elapsed_us is None else f"{obs.sample_elapsed_us / 1e6:.3f}"
    bits = _BITS.get(obs.counter_family, "") if obs.counter_family else ""
    return ",".join((key, _epoch(obs.timestamp), elapsed, _num(obs.in_bps), _num(obs.out_bps),
                     str(obs.if_index), bits, flag_names(obs.quality)))  # fmt: skip


def icmp_row(key: str, obs: IcmpObservation) -> list[str]:
    """Encode one ICMP cycle.

    Args:
        key: The device's key.
        obs: The cycle.

    Returns:
        list[str]: Cells in ``ICMP_COLUMNS`` order; RTTs in µs, ``-1`` = no reply.

    Raises:
        None
    """
    rtts = " ".join("-1" if r is None else str(r) for r in obs.rtts_us)
    median = "" if obs.median_rtt_us is None else str(obs.median_rtt_us)
    return [key, _epoch(obs.timestamp), obs.target_address, str(obs.sent), str(obs.received),
            median, rtts, str(obs.state), flag_names(obs.quality)]  # fmt: skip


def short_label(name: str) -> str:
    """Return a host name's first label when it is a name, not an address.

    Args:
        name: Device name or poll address.

    Returns:
        str: The first label, or ``""``.

    Raises:
        None
    """
    label = name.split(".")[0]
    return label if "." in name and any(c.isalpha() for c in label) and ":" not in name else ""


def device_row(device: PlannedDevice, seen: Iterable[str]) -> list[str]:
    """Encode one ``devices.csv`` row.

    Args:
        device: The planned device.
        seen: ICMP target addresses its cycles probed.

    Returns:
        list[str]: Cells in ``DEVICE_COLUMNS`` order.

    Raises:
        None
    """
    d = device.device
    names = (d.name, short_label(d.name), d.poll_address, short_label(d.poll_address), *seen)
    aliases = dict.fromkeys(a.lower() for a in names if a)
    return [device.key, str(d.id), d.name, d.poll_address, " ".join(aliases),
            "yes" if device.icmp else "no"]  # fmt: skip


def interface_rows(device: PlannedDevice) -> list[list[object]]:
    """Encode a device's ``interfaces.csv`` rows.

    Args:
        device: The planned device.

    Returns:
        list[list[object]]: Cells in ``INTERFACE_COLUMNS`` order, one row per interface.

    Raises:
        None
    """
    rows: list[list[object]] = []
    for planned in device.interfaces:
        f = planned.interface
        rows.append([planned.key, device.key, str(f.id), "" if f.if_index is None else f.if_index,
                     f.if_name, f.if_descr, f.if_alias, f.mac or "", " ".join(f.addresses),
                     f.if_speed or "", f.if_high_speed or "", str(f.lifecycle),
                     " ".join(map(str, planned.lines))])  # fmt: skip
    return rows
