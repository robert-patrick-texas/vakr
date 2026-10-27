"""Encode the parallel-run export as one member stream (MVP-ACC-03, MVP-ACC-04).

The export is read on the production node and joined with MRTG and
SmokePing data on the development host. It goes there inside a field
bundle, whose members are capped in size, so the export is written as one
*member stream*: a text stream that names each member before its lines.
The field kit splits it into bundle members (and pseudonymizes the
``table`` members column by column); a person can read it as is::

    #member-stream 1
    @member icmp/d0001.csv table
    key,start,target_address,sent,received,median_us,rtts_us,state,flags
    d0001,1793091600.000,192.0.2.1,5,5,812,790 812 801 1030 799,reachable,
    @member export.json text
    {...}
    @end

A content line that starts with ``@`` is written with one more ``@``.
Rates are bits per second; RTTs are microseconds, ``-1`` for a probe
without a reply (ICMP-004); an empty cell is a value vakr does not have,
never zero (invariant 1). Times are UTC epoch seconds.

Dependencies:
    csv, io, json: Encoding.
    vakr.query.acceptance, acceptance_plan, acceptance_rows: Reads, plan, and rows.
    vakr.query.member_stream: The stream format.

Safety:
    Writes only to the stream it is given. Stops at the row budget and says
    so in ``export.json``.

Example:
    >>> ExportCounts(budget=1).take()
    True
"""

import csv
import io
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, TextIO

from vakr.domain.series import TimeWindow
from vakr.query.acceptance import AcceptanceExport
from vakr.query.acceptance_plan import ExportPlan, PlannedDevice
from vakr.query.acceptance_rows import (
    DEVICE_COLUMNS,
    ICMP_COLUMNS,
    INTERFACE_COLUMNS,
    RATE_COLUMNS,
    device_row,
    icmp_row,
    interface_rows,
    rate_row,
)
from vakr.query.jsonable import iso_utc
from vakr.query.member_stream import MemberStream

FORMAT = 1


@dataclass
class ExportCounts:
    """Rows written so far, against the budget. Not thread-safe.

    Attributes:
        budget: Rows allowed.
        rates: Interface samples written.
        icmp: ICMP cycles written.
        truncated: True once the budget stopped the export.
        addresses: ICMP target addresses seen per device key.
    """

    budget: int
    rates: int = 0
    icmp: int = 0
    truncated: bool = False
    addresses: dict[str, set[str]] = field(default_factory=dict)

    def take(self) -> bool:
        """Spend one row of the budget.

        Args:
            None

        Returns:
            bool: False, and ``truncated`` set, when the budget is used up.

        Raises:
            None
        """
        if self.rates + self.icmp >= self.budget:
            self.truncated = True
            return False
        return True


def _table(stream: MemberStream, name: str, columns: Sequence[str],
           rows: Iterable[Sequence[object]]) -> None:  # fmt: skip
    """Write a small CSV member with its header.

    Args:
        stream: The stream.
        name: Member name.
        columns: Header.
        rows: Rows.

    Returns:
        None

    Raises:
        OSError: If the output cannot be written.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)
    writer.writerows(rows)
    stream.member(name, "table")
    for text in buffer.getvalue().splitlines():
        stream.line(text)


def _catalogs(stream: MemberStream, plan: ExportPlan, counts: ExportCounts) -> None:
    """Write ``devices.csv`` and ``interfaces.csv``.

    Args:
        stream: The stream.
        plan: The plan.
        counts: Rows written, with the ICMP addresses seen.

    Returns:
        None

    Raises:
        OSError: If the output cannot be written.
    """
    devices = [device_row(p, sorted(counts.addresses.get(p.key, set()))) for p in plan.devices]
    interfaces = [row for p in plan.devices for row in interface_rows(p)]
    _table(stream, "devices.csv", DEVICE_COLUMNS, devices)
    _table(stream, "interfaces.csv", INTERFACE_COLUMNS, interfaces)


def _icmp(stream: MemberStream, service: AcceptanceExport, plan: ExportPlan,
          device: PlannedDevice, window: TimeWindow, counts: ExportCounts) -> None:  # fmt: skip
    """Write one device's ICMP cycles.

    Args:
        stream: The stream.
        service: The reads.
        plan: The plan.
        device: The device.
        window: The window.
        counts: Row budget and counts.

    Returns:
        None

    Raises:
        StorageError: If the telemetry store cannot be read.
    """
    seen = counts.addresses.setdefault(device.key, set())
    rows = []
    for obs in service.cycles(plan, device, window):
        if not counts.take():
            break
        counts.icmp += 1
        seen.add(obs.target_address)
        rows.append(icmp_row(device.key, obs))
    _table(stream, f"icmp/{device.key}.csv", ICMP_COLUMNS, rows)


def _rates(stream: MemberStream, service: AcceptanceExport, plan: ExportPlan,
           device: PlannedDevice, window: TimeWindow, counts: ExportCounts) -> None:  # fmt: skip
    """Write one device's interface samples, interface by interface.

    Args:
        stream: The stream.
        service: The reads.
        plan: The plan.
        device: The device.
        window: The window.
        counts: Row budget and counts.

    Returns:
        None

    Raises:
        StorageError: If the telemetry store cannot be read.
    """
    stream.member(f"rates/{device.key}.csv", "text")
    stream.line(",".join(RATE_COLUMNS))
    for iface in device.interfaces:
        for obs in service.samples(plan, iface, window):
            if not counts.take():
                return
            counts.rates += 1
            stream.line(rate_row(iface.key, obs))


def write_export(service: AcceptanceExport, plan: ExportPlan, window: TimeWindow,
                 out: TextIO, *, max_rows: int, about: dict[str, Any]) -> ExportCounts:  # fmt: skip
    """Write the whole export: ICMP first (it is small), then rates, then catalogs.

    Args:
        service: The reads.
        plan: The plan.
        window: The window.
        out: Text output.
        max_rows: Row budget across samples and cycles.
        about: Facts for ``export.json``, such as the vakr version and host.

    Returns:
        ExportCounts: What was written.

    Raises:
        StorageError: If a store cannot be read.
        OSError: If the output cannot be written.
    """
    stream = MemberStream(out)
    counts = ExportCounts(max_rows)
    for device in (d for d in plan.devices if d.icmp):
        if not counts.truncated:
            _icmp(stream, service, plan, device, window, counts)
    for device in (d for d in plan.devices if d.interfaces):
        if not counts.truncated:
            _rates(stream, service, plan, device, window, counts)
    _catalogs(stream, plan, counts)
    summary = {
        **about,
        "format": FORMAT,
        "generated_utc": iso_utc(service.clock.now_utc()),
        "window": {"start": iso_utc(window.start), "end": iso_utc(window.end)},
        "collector": {"name": plan.collector.name, "position": plan.collector.position},
        "positions": [{"name": c.name, "position": c.position} for c in plan.collectors],
        "rows": {"rates": counts.rates, "icmp": counts.icmp, "max": max_rows},
        "truncated": counts.truncated or plan.truncated,
        "devices": len(plan.devices),
        "interfaces": sum(len(d.interfaces) for d in plan.devices),
        "unmatched": [{"line": line, "reason": why} for line, why in plan.unmatched],
    }
    stream.member("export.json", "text")
    for text in json.dumps(summary, indent=2, sort_keys=True).splitlines():
        stream.line(text)
    stream.end()
    return counts
