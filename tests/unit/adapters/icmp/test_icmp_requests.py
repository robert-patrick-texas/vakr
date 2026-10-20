"""Tests for probe requests: target guardrails and bounds (15.5, ICMP-001, ICMP-002).

Only canonical unicast IP literals may be probed; names are resolved before
a request exists (9.11). Every count and duration is bounded, so a request
can never run unbounded.

Dependencies:
    pytest: Parametrised cases.
    vakr.ports.icmp: The request type under test.

Example:
    $ uv run pytest tests/unit/adapters/icmp/test_icmp_requests.py
"""

import pytest

from vakr.domain.errors import ValidationError
from vakr.ports.icmp import ProbeRequest, probe_address


@pytest.mark.parametrize(
    ("target", "message"),
    [
        ("core1.example.net", "must be an IP address"),
        ("2001:DB8::1", "canonically as 2001:db8::1"),
        ("224.0.0.1", "unicast"),
        ("ff02::1", "unicast"),
        ("0.0.0.0", "unicast"),  # noqa: S104 - the point is to refuse it
        ("::", "unicast"),
        ("255.255.255.255", "unicast"),
        ("::ffff:192.0.2.1", "use the IPv4 address 192.0.2.1"),
    ],
)
def test_targets_must_be_canonical_unicast_literals(target: str, message: str) -> None:
    """15.5: malformed, multicast, unspecified, and broadcast targets are refused.

    Args:
        target: Candidate target.
        message: Expected explanation.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(ValidationError, match=message):
        ProbeRequest(target, 5, 0.2, 1.0)


@pytest.mark.parametrize(
    ("count", "spacing", "timeout", "field"),
    [
        (True, 0.2, 1.0, "count"),
        (0, 0.2, 1.0, "count"),
        (101, 0.2, 1.0, "count"),
        (5, -0.1, 1.0, "spacing_s"),
        (5, 61.0, 1.0, "spacing_s"),
        (5, 0.2, 0.0, "timeout_s"),
        (5, 0.2, 61.0, "timeout_s"),
    ],
)
def test_counts_and_durations_are_bounded(
    count: int, spacing: float, timeout: float, field: str
) -> None:
    """Every bound in a request is checked, with the field named.

    Args:
        count: Probes.
        spacing: Seconds between probes.
        timeout: Seconds per probe.
        field: Field expected in the error.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(ValidationError) as caught:
        ProbeRequest("192.0.2.1", count, spacing, timeout)
    assert caught.value.field == field


def test_source_must_match_the_target_family_and_be_unicast() -> None:
    """ICMP-011: a pinned source is a unicast address of the target's family.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(ValidationError, match="same address family"):
        ProbeRequest("192.0.2.1", 5, 0.2, 1.0, "2001:db8::10")
    with pytest.raises(ValidationError, match="source must be a unicast"):
        ProbeRequest("192.0.2.1", 5, 0.2, 1.0, "224.0.0.5")
    request = ProbeRequest("fe80::1%lo", 5, 0.2, 1.0, "fe80::2%lo")
    assert request.family.value == "ipv6"
    assert probe_address("fe80::1%lo", "target").version == 6


def test_deadline_is_e9_two_seconds_by_default() -> None:
    """E.9, E.22: 4 x 200 ms + 1 s + 200 ms slack = 2.0 s.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert round(ProbeRequest("192.0.2.1", 5, 0.2, 1.0).deadline_s, 9) == 2.0
    assert round(ProbeRequest("192.0.2.1", 1, 0.0, 0.5).deadline_s, 9) == 0.7
