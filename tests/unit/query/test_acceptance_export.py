"""The parallel-run export: targets, plan, and member stream (MVP-ACC-03, MVP-ACC-04).

``vakr acceptance export`` writes vakr's native interface samples and ICMP
cycles so the development side can re-aggregate them over MRTG's and
SmokePing's windows. These tests check the targets file forms, how targets
resolve against the inventory (``ifIndex`` is the current one, every row
carries the observed one), that a missing rate is an empty cell and never
``0`` (invariant 1), and that the row budget stops the export and says so.

Dependencies:
    pytest; tests.unit.query.query_testkit; tests.contract.contract_samples.
    vakr.query.acceptance*: The modules under test.

Example:
    $ uv run pytest tests/unit/query/test_acceptance_export.py
"""

import csv
import io
import json
from datetime import timedelta
from typing import Any

import pytest

from tests.contract import contract_samples as samples
from tests.unit.query.query_testkit import NOW, Fleet
from vakr.domain.enums import BaselineState, CounterFamily, IcmpState
from vakr.domain.errors import ValidationError
from vakr.domain.quality import Quality
from vakr.domain.series import TimeWindow
from vakr.query.acceptance import AcceptanceExport, check_window
from vakr.query.acceptance_stream import write_export
from vakr.query.acceptance_targets import mac_key, parse_targets
from vakr.query.settings import QuerySettings

WINDOW = TimeWindow(NOW - timedelta(hours=1), NOW)


def split(text: str) -> dict[str, tuple[str, list[str]]]:
    """Split a member stream into ``{name: (kind, lines)}``.

    Args:
        text: The stream.

    Returns:
        dict[str, tuple[str, list[str]]]: Members.

    Raises:
        AssertionError: If the stream is malformed.
    """
    lines = text.splitlines()
    assert lines[0] == "#member-stream 1"
    assert lines[-1] == "@end"
    members: dict[str, tuple[str, list[str]]] = {}
    current: list[str] = []
    for line in lines[1:-1]:
        if line.startswith("@member "):
            _, name, kind = line.split(" ")
            current = []
            members[name] = (kind, current)
        else:
            current.append(line[1:] if line.startswith("@@") else line)
    return members


def table(member: tuple[str, list[str]]) -> list[dict[str, str]]:
    """Parse a CSV member.

    Args:
        member: ``(kind, lines)``.

    Returns:
        list[dict[str, str]]: Rows.

    Raises:
        None
    """
    return list(csv.DictReader(io.StringIO("\n".join(member[1]) + "\n")))


def fleet_with_traffic() -> tuple[Fleet, dict[str, Any]]:
    """Build two devices, three interfaces, samples, and ICMP cycles.

    Args:
        None

    Returns:
        tuple: The fleet and the records by name.

    Raises:
        None
    """
    fleet = Fleet()
    col = fleet.collectors["east"].id
    rtr = fleet.device("rtr1.example.net", poll_address="192.0.2.10")
    sw = fleet.device("sw2", poll_address="192.0.2.20")
    up = fleet.interface(rtr, "Gi0/0/1", if_index=2, if_descr="GigabitEthernet0/0/1",
                         mac="00:1a:2b:3c:4d:5e", addresses=("198.51.100.1/30",),
                         if_alias="WAN, ISP \"A\"")  # fmt: skip
    fleet.interface(rtr, "Gi0/0/2", if_index=3)
    port = fleet.interface(sw, "Gi1/0/1", if_index=10101)
    rows = []
    for m in range(4):
        at = NOW - timedelta(minutes=10 - m)
        rows.append(samples.sample(rtr.id, up.id, col, at, if_index=2, in_bps=8000.0 * (m + 1),
                                   out_bps=1e9 / 3))  # fmt: skip
        rows.append(samples.icmp(rtr.id, col, at, target_address="192.0.2.10"))
    gap = samples.sample(rtr.id, up.id, col, NOW - timedelta(minutes=5), in_bps=None,
                         out_bps=None, baseline_state=BaselineState.INVALIDATED,
                         quality=Quality.BASELINE_UPTIME_RESET, sample_elapsed_us=None,
                         counter_family=CounterFamily.LEGACY32, in_octets=5,
                         out_octets=6)  # fmt: skip
    rows.append(gap)
    rows.append(samples.sample(sw.id, port.id, col, NOW - timedelta(minutes=3)))
    lost = (None, None, None, None, None)
    rows.append(samples.icmp(sw.id, col, NOW - timedelta(minutes=2), rtts_us=lost,
                             state=IcmpState.UNREACHABLE, quality=Quality.NONE, min_rtt_us=None,
                             avg_rtt_us=None, median_rtt_us=None, max_rtt_us=None,
                             jitter_us=None, target_address="192.0.2.20"))  # fmt: skip
    fleet.write(*rows)
    return fleet, {"rtr": rtr, "sw": sw, "up": up, "port": port}


def export(fleet: Fleet, targets: str | None, max_rows: int = 1000) -> dict:
    """Run the export into a string and split it.

    Args:
        fleet: The fleet.
        targets: Targets file text, or ``None``.
        max_rows: Row budget.

    Returns:
        dict: Members.

    Raises:
        None
    """
    service = AcceptanceExport(fleet.uow, fleet.telemetry, QuerySettings(), fleet.clock)
    plan = service.plan(None if targets is None else parse_targets(targets))
    out = io.StringIO()
    write_export(service, plan, WINDOW, out, max_rows=max_rows, about={"host": "east1"})
    return split(out.getvalue())


def test_targets_file_forms_and_errors() -> None:
    """Both line forms parse; malformed lines name their number, never their text.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    text = (
        "# MRTG targets\n"
        '{"kind": "interface", "device": ["RTR1.example.net", "192.0.2.10"], "ifindex": 2}\n'
        "interface rtr1|192.0.2.10 ifdescr=GigabitEthernet0/0/1 - uplink\n"
        '{"kind": "icmp", "device": "sw2"}\n'
        "icmp sw2\n"
        "interface sw2 all\n"
    )
    specs = parse_targets(text)
    assert [(s.line, s.kind, s.selector) for s in specs] == [
        (2, "interface", "ifindex"), (3, "interface", "ifdescr"), (4, "icmp", ""),
        (5, "icmp", ""), (6, "interface", "all")]  # fmt: skip
    assert specs[0].aliases == ("rtr1.example.net", "192.0.2.10")
    assert specs[1].value == "GigabitEthernet0/0/1 - uplink"
    bad = ["interface sw2 ifindex=Gi1", "router sw2", "interface", "interface sw2 speed=1",
           "interface sw2 ifname=", "{nope", "[1]", '{"kind": "icmp", "device": 7}',
           "x" * 1001]  # fmt: skip
    for line in bad:
        with pytest.raises(ValidationError, match="targets line 2") as caught:
            parse_targets(f"# c\n{line}\n")
        assert line not in str(caught.value) or len(line) < 3
    assert mac_key("0:1A:2b:3c:4d:5e") == "001a2b3c4d5e" == mac_key("001a.2b3c.4d5e")
    assert mac_key("zz:1a:2b:3c:4d:5e") == mac_key(None) == mac_key("00:11") == ""


def test_plan_matches_by_every_selector_and_reports_unmatched() -> None:
    """Names, addresses, ifIndex, ifName, ifDescr, IP, and MAC all select (invariant 6).

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet, rec = fleet_with_traffic()
    service = AcceptanceExport(fleet.uow, fleet.telemetry, QuerySettings(), fleet.clock)
    targets = parse_targets(
        "interface 192.0.2.10 ifindex=2\n"
        "interface rtr1.example.net ifname=Gi0/0/1\n"
        "interface RTR1.EXAMPLE.NET ifdescr=GigabitEthernet0/0/1\n"
        "interface rtr1.example.net ip=198.51.100.1\n"
        "interface rtr1.example.net mac=00-1a-2b-3c-4d-5e\n"
        "interface rtr1.example.net ifindex=99\n"
        "interface nosuch.example.net ifindex=1\n"
        "icmp sw2\n"
    )
    plan = service.plan(targets)
    assert plan.collector.name == "east"
    (rtr, sw) = plan.devices
    assert rtr.device.id == rec["rtr"].id
    assert not rtr.icmp
    assert [i.interface.if_name for i in rtr.interfaces] == ["Gi0/0/1"]
    assert rtr.interfaces[0].lines == (1, 2, 3, 4, 5)
    assert sw.icmp
    assert sw.interfaces == ()
    assert plan.unmatched == ((6, "no interface matches"), (7, "no device matches"))
    everything = service.plan(None)
    assert [len(d.interfaces) for d in everything.devices] == [2, 1]
    assert all(d.icmp for d in everything.devices)
    with pytest.raises(ValidationError, match="no collector named"):
        service.plan(None, "west")


def test_stream_rows_keep_missing_rates_empty_and_carry_if_index() -> None:
    """Rates in bit/s with elapsed time, flags, observed ifIndex; missing is never 0.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet, _ = fleet_with_traffic()
    members = export(fleet, "interface rtr1.example.net ifindex=2\nicmp sw2\nicmp rtr1\n")
    assert members["rates/d0001.csv"][0] == "text"
    rates = table(members["rates/d0001.csv"])
    assert len(rates) == 5
    assert rates[0] == {"key": "i00001", "end": f"{(NOW - timedelta(minutes=10)).timestamp():.3f}",
                        "elapsed_s": "60.000", "in_bps": "8000", "out_bps": "333333333",
                        "if_index": "2", "counter_bits": "64", "flags": ""}  # fmt: skip
    reset = next(r for r in rates if r["flags"])
    assert reset["in_bps"] == reset["out_bps"] == reset["elapsed_s"] == ""
    assert reset["flags"] == "BASELINE_UPTIME_RESET"
    assert reset["counter_bits"] == "32"
    assert reset["if_index"] == "7"
    icmp = {k: table(v) for k, v in members.items() if k.startswith("icmp/")}
    assert sorted(icmp) == ["icmp/d0001.csv", "icmp/d0002.csv"]
    first = icmp["icmp/d0001.csv"][0]
    assert first["rtts_us"] == "900 -1 1100 1000 1000"
    assert (first["sent"], first["received"], first["median_us"]) == ("5", "4", "1000")
    assert first["flags"] == "PARTIAL_LOSS|RETRIED"
    dark = icmp["icmp/d0002.csv"][0]
    assert dark["median_us"] == ""
    assert dark["received"] == "0"
    assert dark["state"] == "unreachable"
    devices = table(members["devices.csv"])
    assert devices[0]["aliases"] == "rtr1.example.net rtr1 192.0.2.10"
    (iface,) = table(members["interfaces.csv"])
    assert iface["if_alias"] == 'WAN, ISP "A"'
    assert iface["lines"] == "1"
    assert iface["mac"] == "00:1a:2b:3c:4d:5e"
    assert iface["addresses"] == "198.51.100.1/30"
    summary = json.loads("\n".join(members["export.json"][1]))
    assert summary["rows"] == {"rates": 5, "icmp": 5, "max": 1000}
    assert summary["truncated"] is False
    assert summary["host"] == "east1"
    assert summary["collector"] == {"name": "east", "position": "east"}
    assert summary["window"]["end"] == "2026-10-20T12:00:00Z"


def test_row_budget_stops_the_export_and_says_so() -> None:
    """``max_rows`` is a hard bound across cycles and samples; the summary says it was cut.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet, _ = fleet_with_traffic()
    members = export(fleet, None, max_rows=6)
    summary = json.loads("\n".join(members["export.json"][1]))
    assert summary["truncated"] is True
    assert summary["rows"]["icmp"] + summary["rows"]["rates"] == 6
    assert summary["rows"]["icmp"] == 5  # ICMP goes first: it is small and all of ACC-04
    assert "devices.csv" in members
    assert "interfaces.csv" in members


def test_window_bounds() -> None:
    """Windows longer than 31 days, or ending more than 5 minutes ahead, are refused.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    check_window(WINDOW, NOW)
    with pytest.raises(ValidationError, match="31 days"):
        check_window(TimeWindow(NOW - timedelta(days=32), NOW), NOW)
    with pytest.raises(ValidationError, match="future"):
        check_window(TimeWindow(NOW, NOW + timedelta(minutes=6)), NOW)
    empty = Fleet(positions=())
    with pytest.raises(ValidationError, match="no collector"):
        AcceptanceExport(empty.uow, empty.telemetry).plan(None)
