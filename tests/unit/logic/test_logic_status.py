"""Tests for device status (E.2), strategies, composite (MPOS-007), and list order (7.7).

Each E.2 row has at least one table entry, plus entries proving first-match
precedence (collector trouble before Down, maintenance before everything but
disabled). Strategy tables cover ``icmp_only`` and ``snmp_only``. Composite
rows model MPOS-T01: a partition reads "Reachable from east only".

Dependencies:
    pytest: Tables.
    vakr.logic.status, vakr.logic.status_table: Under test.

Example:
    $ uv run pytest tests/unit/logic/test_logic_status.py
"""

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from vakr.domain.enums import (
    AdminState,
    CollectorState,
    IcmpState,
    ReachabilityStrategy,
    SnmpState,
    StatusBucket,
    StatusColor,
)
from vakr.logic.icmp import Impairment
from vakr.logic.status import (
    CompositeRule,
    DeviceStatus,
    PositionStatus,
    composite_status,
    device_sort_key,
    device_status,
)
from vakr.logic.status_table import StatusEvidence

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
FRESH = NOW - timedelta(seconds=30)
IC, S = IcmpState, SnmpState
OK_EVIDENCE = StatusEvidence(IC.REACHABLE, S.RESPONDING, FRESH)
ATT, ACT, OFF = StatusBucket.ATTENTION, StatusBucket.ACTIVE, StatusBucket.OFFLINE
RED, AMBER, GREEN, GRAY = StatusColor.RED, StatusColor.AMBER, StatusColor.GREEN, StatusColor.GRAY


def ev(**changes: object) -> StatusEvidence:
    """Build evidence from a fresh, healthy, reachable, SNMP-responding device.

    Args:
        **changes: Fields to replace.

    Returns:
        StatusEvidence: The evidence.

    Raises:
        None
    """
    return replace(OK_EVIDENCE, **changes)


DOWN = {"icmp": IC.UNREACHABLE, "snmp": S.SKIPPED}
GATED_ROWS = [
    (ev(admin=AdminState.DISABLED, **DOWN), 1, OFF, GRAY, "Disabled", "E.2 row 1 disabled"),
    (ev(admin=AdminState.RETIRED), 1, OFF, GRAY, "Retired", "E.2 row 1 retired"),
    (
        ev(admin=AdminState.MAINTENANCE, **DOWN),
        2,
        OFF,
        GRAY,
        "Maintenance (collection continues)",
        "E.2 row 2 maintenance beats Down",
    ),
    (
        ev(observed_at=NOW - timedelta(seconds=151)),
        3,
        ATT,
        AMBER,
        "Unknown — no fresh observation",
        "E.2 row 3 stale (E.3)",
    ),
    (
        ev(observed_at=None),
        3,
        ATT,
        AMBER,
        "Unknown — no fresh observation",
        "E.2 row 3 never observed",
    ),
    (
        ev(not_attempted=True, **DOWN),
        3,
        ATT,
        AMBER,
        "Unknown — poll not attempted",
        "E.2 row 3 NOT_ATTEMPTED is not Down (OBS-003)",
    ),
    (
        ev(collector=CollectorState.OVERLOADED, **DOWN),
        3,
        ATT,
        AMBER,
        "Unknown — collector overloaded",
        "E.2 row 3 overload is never target-down (invariant 2)",
    ),
    (
        ev(collector=CollectorState.DEGRADED),
        3,
        ATT,
        AMBER,
        "Unknown — collector degraded",
        "E.2 row 3 degraded collector",
    ),
    (
        ev(collector=CollectorState.UNAVAILABLE),
        3,
        ATT,
        AMBER,
        "Unknown — collector unavailable",
        "E.2 row 3 unavailable collector",
    ),
    (
        ev(icmp=IC.UNKNOWN),
        3,
        ATT,
        AMBER,
        "Unknown — no ICMP result",
        "E.2 row 3 no reachability result is never green (UI-015)",
    ),
    (ev(**DOWN), 4, ATT, RED, "Down", "E.2 row 4 ICMP lost, SNMP skipped"),
    (ev(icmp=IC.UNREACHABLE, snmp=S.TIMEOUT), 4, ATT, RED, "Down", "E.2 row 4 SNMP timeout"),
    (ev(icmp=IC.UNREACHABLE, snmp=S.ERROR), 4, ATT, RED, "Down", "E.2 row 4 SNMP error"),
    (
        ev(icmp=IC.UNREACHABLE),
        5,
        ATT,
        AMBER,
        "ICMP lost, SNMP OK",
        "E.2 row 5 snmp_fallback success (ICMP-T05 logic)",
    ),
    (
        ev(icmp=IC.UNREACHABLE, snmp=S.PARTIAL),
        5,
        ATT,
        AMBER,
        "ICMP lost, SNMP OK",
        "E.2 row 5 a partial SNMP reply still proves reachability (SNMP-013)",
    ),
    (
        ev(icmp=IC.IMPAIRED, icmp_impairments=(Impairment.LOSS,)),
        6,
        ATT,
        AMBER,
        "Packet loss",
        "E.2 row 6 loss",
    ),
    (
        ev(icmp=IC.IMPAIRED, icmp_impairments=(Impairment.LATENCY,)),
        6,
        ATT,
        AMBER,
        "Latency",
        "E.2 row 6 latency",
    ),
    (
        ev(icmp=IC.IMPAIRED, icmp_impairments=(Impairment.LOSS, Impairment.LATENCY)),
        6,
        ATT,
        AMBER,
        "Packet loss and latency",
        "E.2 row 6 both",
    ),
    (
        ev(icmp=IC.IMPAIRED, snmp=S.TIMEOUT),
        6,
        ATT,
        AMBER,
        "Packet loss / Latency",
        "E.2 row 6 beats row 7; reasons unknown",
    ),
    (ev(snmp=S.TIMEOUT), 7, ATT, AMBER, "SNMP failing", "E.2 row 7 SNMP timeout"),
    (ev(snmp=S.ERROR), 7, ATT, AMBER, "SNMP failing", "E.2 row 7 SNMP error"),
    (ev(snmp=S.PARTIAL), 8, ATT, AMBER, "Partial data", "E.2 row 8 partial SNMP"),
    (ev(partial_discovery=True), 8, ATT, AMBER, "Partial data", "E.2 row 8 partial discovery"),
    (
        ev(identity_review_pending=True),
        8,
        ATT,
        AMBER,
        "Partial data",
        "E.2 row 8 identity review pending",
    ),
    (
        ev(last_reset_at=NOW - timedelta(minutes=60)),
        9,
        ATT,
        AMBER,
        "Restarted",
        "E.2 row 9 restart exactly 60 minutes ago",
    ),
    (
        ev(last_reset_at=NOW - timedelta(minutes=61)),
        10,
        ACT,
        GREEN,
        "OK",
        "E.2 row 9 window has passed",
    ),
    (OK_EVIDENCE, 10, ACT, GREEN, "OK", "E.2 row 10 OK"),
]


@pytest.mark.parametrize(
    ("evidence", "row", "bucket", "color", "label"),
    [pytest.param(*case[:5], id=case[5]) for case in GATED_ROWS],
)
def test_e2_rows(
    evidence: StatusEvidence, row: int, bucket: StatusBucket, color: StatusColor, label: str
) -> None:
    """E.2 rows under ``icmp_gated``; the first matching row wins.

    Args:
        evidence: One collector's evidence.
        row: Expected row.
        bucket: Expected bucket.
        color: Expected colour.
        label: Expected label.

    Returns:
        None

    Raises:
        None
    """
    s = device_status(evidence, NOW)
    assert (s.row, s.bucket, s.color, s.label) == (row, bucket, color, label)


ICMP_ONLY, SNMP_ONLY = ReachabilityStrategy.ICMP_ONLY, ReachabilityStrategy.SNMP_ONLY
STRATEGY_ROWS = [
    (
        ev(strategy=ICMP_ONLY, icmp=IC.UNREACHABLE, snmp=S.RESPONDING),
        4,
        "Down",
        "ICMP-014 icmp_only: stale SNMP state cannot rescue a lost device",
    ),
    (
        ev(strategy=ICMP_ONLY, icmp=IC.IMPAIRED),
        6,
        "Packet loss / Latency",
        "ICMP-014 icmp_only row 6",
    ),
    (ev(strategy=ICMP_ONLY, snmp=S.TIMEOUT), 10, "OK", "ICMP-014 icmp_only skips row 7"),
    (ev(strategy=ICMP_ONLY, snmp=S.PARTIAL), 10, "OK", "ICMP-014 icmp_only skips SNMP partial"),
    (ev(strategy=ICMP_ONLY, last_reset_at=NOW), 10, "OK", "ICMP-014 icmp_only skips row 9"),
    (
        ev(strategy=ICMP_ONLY, icmp=IC.UNKNOWN),
        3,
        "Unknown — no ICMP result",
        "ICMP-014 icmp_only without ICMP is unknown",
    ),
    (
        ev(strategy=SNMP_ONLY, icmp=IC.UNKNOWN, snmp=S.TIMEOUT),
        4,
        "Down",
        "ICMP-014 snmp_only: SNMP timeout is Down",
    ),
    (
        ev(strategy=SNMP_ONLY, icmp=IC.UNKNOWN, snmp=S.ERROR),
        7,
        "SNMP failing",
        "ICMP-014 snmp_only: SNMP error means the device answered",
    ),
    (
        ev(strategy=SNMP_ONLY, icmp=IC.UNREACHABLE),
        10,
        "OK",
        "ICMP-014 snmp_only ignores ICMP rows 4-6",
    ),
    (ev(strategy=SNMP_ONLY, icmp=IC.IMPAIRED), 10, "OK", "ICMP-014 snmp_only ignores row 6"),
    (ev(strategy=SNMP_ONLY, snmp=S.PARTIAL), 8, "Partial data", "ICMP-014 snmp_only row 8"),
    (ev(strategy=SNMP_ONLY, last_reset_at=NOW), 9, "Restarted", "ICMP-014 snmp_only row 9"),
    (
        ev(strategy=SNMP_ONLY, snmp=S.SKIPPED),
        3,
        "Unknown — no SNMP result",
        "ICMP-014 snmp_only without SNMP is unknown",
    ),
]


@pytest.mark.parametrize(
    ("evidence", "row", "label"),
    [pytest.param(*case[:3], id=case[3]) for case in STRATEGY_ROWS],
)
def test_strategy_skips(evidence: StatusEvidence, row: int, label: str) -> None:
    """E.2: ICMP rows are skipped for ``snmp_only``, SNMP rows for ``icmp_only``.

    Args:
        evidence: One collector's evidence.
        row: Expected row.
        label: Expected label.

    Returns:
        None

    Raises:
        None
    """
    s = device_status(evidence, NOW)
    assert (s.row, s.label) == (row, label)


def status(evidence: StatusEvidence) -> DeviceStatus:
    """Evaluate evidence at ``NOW``.

    Args:
        evidence: One collector's evidence.

    Returns:
        DeviceStatus: The E.2 result.

    Raises:
        None
    """
    return device_status(evidence, NOW)


UP, LOST = status(OK_EVIDENCE), status(ev(**DOWN))
LOSSY = status(ev(icmp=IC.IMPAIRED, icmp_impairments=(Impairment.LOSS,)))
BLIND = status(ev(collector=CollectorState.UNAVAILABLE))
OFFLINE, MAINT = status(ev(admin=AdminState.DISABLED)), status(ev(admin=AdminState.MAINTENANCE))


@pytest.mark.parametrize(
    ("positions", "rule", "label", "color"),
    [
        pytest.param(
            {"east": UP, "west": LOST},
            CompositeRule.PARTITION,
            "Reachable from east only",
            AMBER,
            id="MPOS-T01 down from west only reads reachable from east only",
        ),
        pytest.param(
            {"west": LOST, "east": LOST},
            CompositeRule.ALL_DOWN,
            "Down",
            RED,
            id="MPOS-007 down from every position",
        ),
        pytest.param(
            {"east": LOST, "west": BLIND},
            CompositeRule.ALL_DOWN,
            "Down",
            RED,
            id="MPOS-007 down from the only position with evidence",
        ),
        pytest.param(
            {"east": UP, "west": BLIND},
            CompositeRule.MOST_SEVERE,
            "OK",
            GREEN,
            id="MPOS-007 a blind collector does not turn a healthy device amber",
        ),
        pytest.param(
            {"west": BLIND, "east": BLIND},
            CompositeRule.ALL_UNKNOWN,
            "Unknown — collector unavailable",
            AMBER,
            id="MPOS-007 nobody knows",
        ),
        pytest.param(
            {"east": LOSSY, "west": UP},
            CompositeRule.MOST_SEVERE,
            "Packet loss",
            AMBER,
            id="MPOS-007 most severe evidence-backed result",
        ),
        pytest.param(
            {"east": OFFLINE, "west": MAINT},
            CompositeRule.ADMIN,
            "Disabled",
            GRAY,
            id="MPOS-007 admin state; row 1 wins if metadata lags",
        ),
        pytest.param(
            {"west": LOSSY, "north": UP, "east": LOST},
            CompositeRule.PARTITION,
            "Reachable from north, west only",
            AMBER,
            id="MPOS-007 three positions",
        ),
        pytest.param(
            {},
            CompositeRule.NO_EVIDENCE,
            "Unknown — no observation",
            AMBER,
            id="MPOS-007 no position has reported",
        ),
    ],
)
def test_composite(
    positions: dict[str, DeviceStatus], rule: CompositeRule, label: str, color: StatusColor
) -> None:
    """MPOS-007: composite rules, with per-position evidence kept (MPOL-004).

    Args:
        positions: Position label to that collector's status.
        rule: Expected composite rule.
        label: Expected label.
        color: Expected colour.

    Returns:
        None

    Raises:
        None
    """
    given = [PositionStatus(p, s) for p, s in positions.items()]
    c = composite_status(given)
    assert (c.rule, c.status.label, c.status.color) == (rule, label, color)
    assert [p.position for p in c.positions] == sorted(positions)
    assert {p.position: p.status for p in c.positions} == positions
    if rule is CompositeRule.PARTITION:
        assert c.status.row is None


def test_device_list_order() -> None:
    """WEB-T01 logic (7.7): bucket, severity, most recent change, natural name.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    split = composite_status([PositionStatus("east", UP), PositionStatus("west", LOST)]).status
    t = NOW - timedelta(hours=1)
    devices = [
        ("ok-b", UP, t),
        ("ge-0/0/10", LOSSY, t),
        ("disabled", OFFLINE, NOW),
        ("blind", BLIND, t),
        ("ge-0/0/2", LOSSY, t),
        ("never-changed", LOSSY, None),
        ("newer", LOSSY, NOW),
        ("down", LOST, t),
        ("split", split, t),
        ("ok-a", UP, t),
    ]
    ordered = sorted(devices, key=lambda d: device_sort_key(d[1], d[2], d[0]))
    assert [d[0] for d in ordered] == [
        "down",
        "split",
        "blind",
        "newer",
        "ge-0/0/2",
        "ge-0/0/10",
        "never-changed",
        "ok-a",
        "ok-b",
        "disabled",
    ]


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(
            lambda: device_status(ev(last_reset_at=NOW.replace(tzinfo=None)), NOW),
            id="naive reset time",
        ),
        pytest.param(lambda: device_status(OK_EVIDENCE, NOW.replace(tzinfo=None)), id="naive now"),
        pytest.param(
            lambda: device_sort_key(UP, NOW.replace(tzinfo=None), "x"), id="naive change time"
        ),
    ],
)
def test_naive_datetimes_are_refused(call: Callable[[], object]) -> None:
    """Canonical times are UTC (TIME-001); a naive time would be read as local.

    Args:
        call: Zero-argument callable that must raise.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(ValueError, match="timezone-aware"):
        call()
