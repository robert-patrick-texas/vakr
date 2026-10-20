"""Device status through the list service: every Appendix E.2 row, composites, and outages.

Each case writes observations through the telemetry port exactly as the
collector would and reads the status back through ``StatusQueries``, so
the E.2 table is checked end to end: evidence selection, collector health,
freshness, and the 7.7 bucket order (TUI-T01, WEB-T01 semantics, TUI-T04).

Dependencies:
    pytest; tests.unit.query.query_testkit.

Example:
    $ uv run pytest tests/unit/query/test_query_status.py
"""

from collections.abc import Callable
from typing import Any

import pytest

from tests.unit.query.query_testkit import Fleet
from vakr.domain.enums import (
    AdminState,
    CollectorState,
    IcmpState,
    PollExecution,
    ReachabilityStrategy,
    SnmpState,
    StatusBucket,
    StatusColor,
    UptimeContinuity,
)
from vakr.domain.errors import StorageError
from vakr.domain.quality import Quality
from vakr.ports.telemetry import TelemetryBackend
from vakr.query import DeviceListRequest, DeviceRow, StatusQueries


def row_of(fleet: Fleet, name: str, **request: Any) -> DeviceRow:
    """Return one device's list row.

    Args:
        fleet: The fleet.
        name: Device name.
        **request: ``DeviceListRequest`` fields.

    Returns:
        DeviceRow: The row.

    Raises:
        LookupError: If the device is not listed.
    """
    for row in fleet.queries().device_list(DeviceListRequest(**request)).rows:
        if row.name == name:
            return row
    raise LookupError(name)


def _disabled(f: Fleet) -> None:
    """Row 1: a disabled device.

    Args:
        f: The fleet.

    Returns:
        None

    Raises:
        None
    """
    f.poll(f.device("d", admin_state=AdminState.DISABLED))


def _maintenance(f: Fleet) -> None:
    """Row 2: maintenance, still collected.

    Args:
        f: The fleet.

    Returns:
        None

    Raises:
        None
    """
    f.poll(f.device("d", admin_state=AdminState.MAINTENANCE), icmp=IcmpState.UNREACHABLE)


def _stale(f: Fleet) -> None:
    """Row 3: the newest evidence is ten minutes old.

    Args:
        f: The fleet.

    Returns:
        None

    Raises:
        None
    """
    f.poll(f.device("d"), ago=600)


def _not_attempted(f: Fleet) -> None:
    """Row 3: the newest poll record was not attempted.

    Args:
        f: The fleet.

    Returns:
        None

    Raises:
        None
    """
    d = f.device("d")
    f.poll(d, ago=60)
    f.poll(d, ago=1, icmp=None, snmp=None, execution=PollExecution.NOT_ATTEMPTED,
           poll_quality=Quality.NOT_ATTEMPTED)  # fmt: skip


def _collector_degraded(f: Fleet) -> None:
    """Row 3: the collector reports itself degraded.

    Args:
        f: The fleet.

    Returns:
        None

    Raises:
        None
    """
    f.poll(f.device("d"), icmp=IcmpState.UNREACHABLE, snmp=None)
    f.health(state=CollectorState.DEGRADED)


def _collector_silent(f: Fleet) -> None:
    """Row 3: the collector's last health report is older than E.3 allows.

    Args:
        f: The fleet.

    Returns:
        None

    Raises:
        None
    """
    f.poll(f.device("d"), icmp=IcmpState.UNREACHABLE, snmp=None)
    f.health(ago=400)


def _poll_flagged(f: Fleet) -> None:
    """Row 3: the poll itself carries ``COLLECTOR_DEGRADED``.

    Args:
        f: The fleet.

    Returns:
        None

    Raises:
        None
    """
    f.poll(f.device("d"), icmp=IcmpState.UNKNOWN, poll_quality=Quality.COLLECTOR_DEGRADED)


def _never(f: Fleet) -> None:
    """Row 3: a device never observed is never green (UI-015).

    Args:
        f: The fleet.

    Returns:
        None

    Raises:
        None
    """
    f.device("d")


def _down(f: Fleet) -> None:
    """Row 4: no reply of any kind.

    Args:
        f: The fleet.

    Returns:
        None

    Raises:
        None
    """
    f.poll(f.device("d"), icmp=IcmpState.UNREACHABLE, snmp=None)


def _icmp_lost(f: Fleet) -> None:
    """Row 5: ICMP lost but the SNMP fallback answered.

    Args:
        f: The fleet.

    Returns:
        None

    Raises:
        None
    """
    f.poll(
        f.device("d"), icmp=IcmpState.UNREACHABLE, reachability=ReachabilityStrategy.SNMP_FALLBACK
    )


def _impaired(f: Fleet) -> None:
    """Row 6: three of five probes answered.

    Args:
        f: The fleet.

    Returns:
        None

    Raises:
        None
    """
    f.poll(f.device("d"), icmp=IcmpState.IMPAIRED)


def _snmp_failing(f: Fleet) -> None:
    """Row 7: reachable, SNMP timing out.

    Args:
        f: The fleet.

    Returns:
        None

    Raises:
        None
    """
    f.poll(f.device("d"), snmp=SnmpState.TIMEOUT)


def _partial(f: Fleet) -> None:
    """Row 8: a partial SNMP reply.

    Args:
        f: The fleet.

    Returns:
        None

    Raises:
        None
    """
    f.poll(f.device("d"), snmp=SnmpState.PARTIAL)


def _restarted(f: Fleet) -> None:
    """Row 9: the agent restart is detected in this poll.

    Args:
        f: The fleet.

    Returns:
        None

    Raises:
        None
    """
    f.poll(f.device("d"), uptime_ticks=6_000, continuity=UptimeContinuity.RESET_DETECTED)


def _restarted_earlier(f: Fleet) -> None:
    """Row 9: the restart was detected 10 minutes ago; history is read to find it.

    Args:
        f: The fleet.

    Returns:
        None

    Raises:
        None
    """
    d = f.device("d")
    f.poll(d, ago=600, uptime_ticks=3_000, continuity=UptimeContinuity.RESET_DETECTED)
    f.poll(d, ago=30, uptime_ticks=60_000)


def _ok(f: Fleet) -> None:
    """Row 10: everything fine.

    Args:
        f: The fleet.

    Returns:
        None

    Raises:
        None
    """
    f.poll(f.device("d"))


CASES: list[tuple[Callable[[Fleet], None], int, str, StatusColor, str]] = [
    (_disabled, 1, "Disabled", StatusColor.GRAY, "○"),
    (_maintenance, 2, "Maintenance (collection continues)", StatusColor.GRAY, "◐"),
    (_stale, 3, "Unknown — no fresh observation", StatusColor.AMBER, "?"),
    (_not_attempted, 3, "Unknown — poll not attempted", StatusColor.AMBER, "?"),
    (_collector_degraded, 3, "Unknown — collector degraded", StatusColor.AMBER, "?"),
    (_collector_silent, 3, "Unknown — collector unavailable", StatusColor.AMBER, "?"),
    (_poll_flagged, 3, "Unknown — collector degraded", StatusColor.AMBER, "?"),
    (_never, 3, "Unknown — no fresh observation", StatusColor.AMBER, "?"),
    (_down, 4, "Down", StatusColor.RED, "✖"),
    (_icmp_lost, 5, "ICMP lost, SNMP OK", StatusColor.AMBER, "▲"),
    (_impaired, 6, "Packet loss", StatusColor.AMBER, "▲"),
    (_snmp_failing, 7, "SNMP failing", StatusColor.AMBER, "▲"),
    (_partial, 8, "Partial data", StatusColor.AMBER, "▲"),
    (_restarted, 9, "Restarted", StatusColor.AMBER, "▲"),
    (_restarted_earlier, 9, "Restarted", StatusColor.AMBER, "▲"),
    (_ok, 10, "OK", StatusColor.GREEN, "✔"),
]


@pytest.mark.parametrize(
    ("build", "row", "label", "color", "icon"),
    CASES,
    ids=[getattr(c[0], "__name__", "case") for c in CASES],
)
def test_every_e2_row_is_reachable_through_the_list(
    build: Callable[[Fleet], None], row: int, label: str, color: StatusColor, icon: str
) -> None:
    """TUI-T01, E.2: each row's label, colour, and a distinct icon (12.8: never colour alone).

    Args:
        build: Writes the scenario.
        row: Expected E.2 row.
        label: Expected label.
        color: Expected colour.
        icon: Expected icon.

    Returns:
        None

    Raises:
        None
    """
    fleet = Fleet()
    build(fleet)
    status = row_of(fleet, "d").status
    assert (status.row, status.label, status.color, status.icon) == (row, label, color, icon)


def test_retired_devices_are_hidden_unless_asked_for() -> None:
    """7.5: retired devices leave default views but stay listable.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet = Fleet()
    fleet.device("gone", admin_state=AdminState.RETIRED)
    assert fleet.queries().device_list().total == 0
    assert row_of(fleet, "gone", include_retired=True).status.label == "Retired"


def test_older_snmp_never_rescues_a_newer_unreachable_poll() -> None:
    """E.29 decision 2: only evidence of the newest poll counts, so this is Down, not row 5.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet = Fleet()
    d = fleet.device("d")
    fleet.poll(d, ago=90)
    fleet.poll(d, ago=30, icmp=IcmpState.UNREACHABLE, snmp=None)
    row = row_of(fleet, "d")
    assert row.status.label == "Down"
    assert row.snmp.value is None
    assert row.snmp.reason == "the latest poll made no SNMP request"


def test_partition_between_positions_is_amber_not_red() -> None:
    """MPOS-007: Down from one position, reachable from the other.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet = Fleet(("east", "west"))
    d = fleet.device("d")
    fleet.poll(d, icmp=IcmpState.UNREACHABLE, snmp=None, position="east")
    fleet.poll(d, position="west")
    row = row_of(fleet, "d")
    assert (row.status.label, row.status.color) == ("Reachable from west only", StatusColor.AMBER)
    assert [p.position for p in row.positions] == ["east", "west"]
    assert [p.status.label for p in row.positions] == ["Down", "OK"]


def test_an_unknown_position_does_not_turn_a_healthy_device_amber() -> None:
    """E.17 #7: a position without evidence is ignored when another position has some.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet = Fleet(("east", "west"))
    fleet.poll(fleet.device("d"), position="west")
    row = row_of(fleet, "d")
    assert row.status.label == "OK"
    assert row.collector.text == "west"


def test_no_telemetry_database_keeps_inventory_and_says_why() -> None:
    """TUI-T04, 13.4: storage trouble is Unknown with a reason, never Down; admin rows still apply.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet = Fleet()
    fleet.device("up")
    fleet.device("off", admin_state=AdminState.DISABLED)
    view = fleet.queries(telemetry=False).device_list()
    labels = {r.name: r.status.label for r in view.rows}
    assert labels == {"up": "Unknown — telemetry unavailable", "off": "Disabled"}
    assert view.health.telemetry.value == "unavailable"
    assert view.health.telemetry.reason == "no telemetry database yet"
    assert not view.health.telemetry_ok


def test_telemetry_read_failure_is_reported_not_raised() -> None:
    """TUI-T04: a ``StorageError`` from telemetry becomes an explicit unavailable state.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet = Fleet()
    fleet.poll(fleet.device("d"), icmp=IcmpState.UNREACHABLE, snmp=None)

    def broken() -> TelemetryBackend:
        """Fail like a locked or damaged telemetry database.

        Args:
            None

        Returns:
            TelemetryBackend: Never.

        Raises:
            StorageError: Always.
        """
        raise StorageError("database telemetry.db is locked", retryable=True)

    view = StatusQueries(fleet.uow, broken, clock=fleet.clock).device_list()
    assert view.rows[0].status.label == "Unknown — telemetry unavailable"
    assert view.rows[0].status.bucket is StatusBucket.ATTENTION
    assert "telemetry.db is locked" in (view.health.telemetry.reason or "")


def test_no_collector_registered_still_honours_admin_state() -> None:
    """E.2 rows 1-3 without any collector record.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet = Fleet(())
    fleet.device("new")
    fleet.device("off", admin_state=AdminState.DISABLED)
    rows = {r.name: r for r in fleet.queries().device_list().rows}
    assert rows["new"].status.label == "Unknown — no fresh observation"
    assert rows["off"].status.label == "Disabled"
    assert rows["new"].collector.reason == "no collector registered"
    assert rows["new"].positions == ()
