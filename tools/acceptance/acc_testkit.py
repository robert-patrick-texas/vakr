# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Build extracted field bundles with known answers for the comparison tests.

``Truth`` is one interface's real traffic: a constant rate in each of the
application's sample intervals (60 s, starting 13 s after the minute, as a
collector's schedule would). The application's samples are that truth.
MRTG's 5-minute values are computed from it independently of the code
under test: MRTG polls at phase ``d`` after each boundary, each poll's
rate is the traffic since the previous poll, and the aligned window ``T``
holds ``d/300`` of the poll ending at ``T-300+d`` and the rest of the next
one, stored in whole bytes per second as rateup does. So a correct
comparison finds phase ``d`` and every window within the tolerance.

Dependencies:
    csv, json: Writing the members the field kit writes.

Safety:
    Synthetic addresses from the documentation ranges; no credentials.

Example:
    >>> t = Truth(start=0, minutes=10, rate=lambda k: 8000.0)
    >>> t.bits(13, 313)
    2400000.0
"""

import csv
import io
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

T0 = 1792497600 - 3 * 86400  # 2026-10-17T12:00:00Z
OFFSET = 13


@dataclass
class Truth:
    """Piecewise-constant traffic, one rate per application sample interval.

    Attributes:
        start: First sample interval start, epoch seconds (before ``OFFSET``).
        minutes: Number of intervals.
        rate: Bits per second of interval ``k``.
    """

    start: int
    minutes: int
    rate: Callable[[int], float]

    def intervals(self) -> list[tuple[int, int, float]]:
        """List ``(start, end, rate)`` of every interval.

        Args:
            None

        Returns:
            list[tuple[int, int, float]]: Intervals in time order.

        Raises:
            None
        """
        base = self.start + OFFSET
        return [(base + 60 * k, base + 60 * (k + 1), self.rate(k)) for k in range(self.minutes)]

    def bits(self, a: float, b: float) -> float:
        """Integrate the traffic over ``[a, b)``.

        Args:
            a: Start.
            b: End.

        Returns:
            float: Bits.

        Raises:
            None
        """
        return sum(r * max(0.0, min(b, e) - max(a, s)) for s, e, r in self.intervals())


def mrtg_log(truth: Truth, phase: int, *, step: int = 300, edit: Callable | None = None) -> str:
    """Write the rateup ``.log`` MRTG would have kept for this traffic.

    Args:
        truth: The traffic.
        phase: MRTG's poll phase after each boundary.
        step: Window length.
        edit: Optional ``(end, in_Bps, out_Bps) -> (in_Bps, out_Bps)`` to inject faults.

    Returns:
        str: Counter line, current row, then 5-minute rows newest first.

    Raises:
        None
    """
    first = truth.start + OFFSET + 2 * step
    last = truth.start + OFFSET + 60 * truth.minutes - 2 * step
    rows = []
    for end in range((first // step + 1) * step, last // step * step + 1, step):
        polls = [(end - 2 * step + phase, end - step + phase), (end - step + phase, end + phase)]
        rates = [truth.bits(a, b) / step for a, b in polls]
        value = (phase / step) * rates[0] + (1 - phase / step) * rates[1]
        inn, out = round(value / 8), round(value / 8 / 4)
        if edit is not None:
            inn, out = edit(end, inn, out)
        rows.append(f"{end} {inn} {out} {inn} {out}")
    newest = rows[-1].split()[0]
    current = int(newest) + phase
    return "\n".join([f"{current} 0 0", f"{current} 1 1 1 1", *reversed(rows)]) + "\n"


def table(header: Sequence[str], rows: Sequence[Sequence[object]]) -> str:
    """Render CSV text.

    Args:
        header: Columns.
        rows: Rows.

    Returns:
        str: CSV.

    Raises:
        None
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue()


def bundle(root: Path, name: str, members: dict[str, str], *, key_id: str = "") -> Path:
    """Write one extracted bundle.

    Args:
        root: The inbox.
        name: Bundle id.
        members: Member path to text.
        key_id: Pseudonym key id, or ``""`` for a clear bundle.

    Returns:
        Path: The bundle directory.

    Raises:
        None
    """
    path = root / name
    for member, text in members.items():
        (path / member).parent.mkdir(parents=True, exist_ok=True)
        (path / member).write_text(text, encoding="utf-8")
    manifest = {"bundle_id": name, "created_utc": "2026-10-20T12:05:00Z",
                "pseudonymization": {"enabled": bool(key_id),
                                     "key_id": key_id or None}}  # fmt: skip
    (path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return path


MRTG_COLUMNS = ["key", "target", "kind", "reversed", "ifindex", "ifname", "ifdescr", "ip", "mac",
                "host", "aliases", "port", "snmp_version", "counter_bits", "maxbytes",
                "maxbytes1", "maxbytes2", "absmax", "options", "interval_s", "logformat", "data",
                "comparable", "note"]  # fmt: skip


def mrtg_row(key: str, target: str, **fields: object) -> list[object]:
    """Build a ``targets.csv`` row with defaults for an ifIndex-2 target on 192.0.2.10.

    Args:
        key: Target key.
        target: Target name.
        **fields: Column overrides.

    Returns:
        list[object]: Cells in ``MRTG_COLUMNS`` order.

    Raises:
        None
    """
    base: dict[str, object] = {
        "key": key, "target": target, "kind": "ifindex", "reversed": "no", "ifindex": "2",
        "host": "192.0.2.10", "aliases": "192.0.2.10", "snmp_version": "2", "counter_bits": 64,
        "maxbytes": "125000000", "options": "bits,growright", "interval_s": 300,
        "logformat": "rateup", "data": f"mrtg/logs/{key}.log", "comparable": "yes", "note": "",
    }  # fmt: skip
    base.update(fields)
    return [base.get(c, "") for c in MRTG_COLUMNS]


def vakr_members(truth: Truth, *, if_index: Callable[[int], int] | None = None,
                 flags: Callable[[int], str] | None = None,
                 skip: frozenset[int] = frozenset(), cycles: list[str] | None = None,
                 out_ratio: float = 0.25) -> dict[str, str]:  # fmt: skip
    """Write the application's export for one device and one interface.

    Args:
        truth: The traffic.
        if_index: ``ifIndex`` each sample was read from (default 2).
        flags: Quality flags of each sample (a flagged sample has no rate).
        skip: Sample numbers that are missing altogether.
        cycles: ``icmp`` rows ``start,rtts_us`` (default none).
        out_ratio: Outbound rate as a share of inbound.

    Returns:
        dict[str, str]: Members under ``acceptance/``.

    Raises:
        None
    """
    rates = []
    for k, (_, end, rate) in enumerate(truth.intervals()):
        if k in skip:
            continue
        flag = flags(k) if flags else ""
        values = ["", "", ""] if flag else ["60.000", f"{rate:.9g}", f"{rate * out_ratio:.9g}"]
        rates.append(["i00001", f"{end:.3f}", *values, (if_index or (lambda _k: 2))(k), 64, flag])
    icmp = [["d0001", *c.split(",", 1)[0:1], "192.0.2.10", 5, 5, "", c.split(",", 1)[1],
             "reachable", ""] for c in cycles or []]  # fmt: skip
    about = {"host": "vakr1", "collector": {"name": "vakr1", "position": "east"},
             "truncated": False}  # fmt: skip
    return {
        "acceptance/export.json": json.dumps(about),
        "acceptance/devices.csv": table(["key", "device_id", "device", "poll_address", "aliases",
                                         "icmp"], [["d0001", "dev-1", "rtr1", "192.0.2.10",
                                                    "rtr1 192.0.2.10", "yes"]]),
        "acceptance/interfaces.csv": table(
            ["key", "device_key", "interface_id", "if_index", "if_name", "if_descr", "if_alias",
             "mac", "addresses", "if_speed", "if_high_speed", "lifecycle", "lines"],
            [["i00001", "d0001", "if-1", 2, "Gi0/0/0", "GigabitEthernet0/0/0", "",
              "00:1a:2b:3c:4d:5e", "198.51.100.1/30", "", 1000, "active", "1"]]),
        "acceptance/rates/d0001.csv": table(["key", "end", "elapsed_s", "in_bps", "out_bps",
                                             "if_index", "counter_bits", "flags"], rates),
        "acceptance/icmp/d0001.csv": table(["key", "start", "target_address", "sent", "received",
                                            "median_us", "rtts_us", "state", "flags"], icmp),
    }  # fmt: skip


def smokeping_fetch(rows: Sequence[tuple[int, float | None, float | None, Sequence[float]]],
                    pings: int = 20) -> str:  # fmt: skip
    """Write ``rrdtool fetch`` output of a SmokePing RRD.

    Args:
        rows: ``(end, loss, median_s, rtts_s)``; missing RTTs are centred as SmokePing does.
        pings: Pings per round.

    Returns:
        str: Header, blank line, and rows.

    Raises:
        None
    """
    names = ["uptime", "loss", "median", *(f"ping{i}" for i in range(1, pings + 1))]
    lines = ["".join(f"{n:>17}" for n in names), ""]
    for end, loss, median, rtts in rows:
        lost = pings - len(rtts)
        cells = ["nan"] * (lost // 2) + [f"{r:.10e}" for r in sorted(rtts)]
        cells += ["nan"] * (lost - lost // 2)
        head = [f"{3.6e6:.10e}", "nan" if loss is None else f"{loss:.10e}",
                "nan" if median is None else f"{median:.10e}"]  # fmt: skip
        lines.append(f"{end}: " + " ".join(head + cells))
    return "\n".join(lines) + "\n"
