"""Promotion and demotion decisions, and two primaries as node health (MPOS-003, MPOS-006, E.39).

``vakr.replication.promotion`` decides; these tests pin each precondition
and refusal, what ``--force`` overrides, how the partner's answer is read,
and that a second primary is CRITICAL node health that names which node to
demote, never a target's state.

Dependencies:
    pytest; vakr.replication.promotion, role_record; vakr.health.replication_check.

Example:
    $ uv run pytest tests/unit/replication/test_node_promotion.py
"""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tests.unit.replication import obs_testkit as k
from vakr.config.node import NodeSettings
from vakr.domain.ids import CollectorId, new_id
from vakr.health.model import Status
from vakr.health.replication_check import check_replication
from vakr.lifecycle.egress.errors import EgressError
from vakr.paths import InstallPaths
from vakr.ports.clock import ManualClock
from vakr.replication import conflict, promotion, role_record
from vakr.replication import observation_status as rec
from vakr.replication.client import PartnerError
from vakr.replication.observation_pull import ObservationPuller
from vakr.replication.overview import replication_reader
from vakr.replication.promotion import PartnerProbe
from vakr.replication.status import PullStatus
from vakr.replication.token_file import TokenFileError

NOW = datetime(2026, 10, 27, 12, tzinfo=UTC)
EAST = "https://vakr-east.example.net"
WEST = "https://vakr-west.example.net"
PEER = NodeSettings("peer", "west", EAST)
PRIMARY = NodeSettings("primary", "east", "", WEST)
GONE = PartnerProbe(EAST, False, detail="tcp: cannot connect")


def _pulled(minutes_ago: float) -> PullStatus:
    """A pull record whose newest success was that long ago.

    Args:
        minutes_ago: Age of the newest success.

    Returns:
        PullStatus: The record.

    Raises:
        None
    """
    stamp = (NOW - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")
    return PullStatus(last_attempt_at=stamp, last_success_at=stamp)


class _Answer:
    """A partner client that answers, or raises, one fixed thing."""

    def __init__(self, answer: Any) -> None:
        """Bind the answer.

        Args:
            answer: A state document, or an exception to raise.

        Returns:
            None

        Raises:
            None
        """
        self.answer = answer

    def get_json(self, path: str, query: Mapping[str, Any] | None = None) -> Any:  # noqa: ARG002
        """Answer ``metadata/state``.

        Args:
            path: The endpoint.
            query: Unused.

        Returns:
            Any: The answer.

        Raises:
            Exception: The bound exception.
        """
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer


@pytest.mark.parametrize(("answer", "reachable", "role"), [
    ({"role": "primary", "position": "east"}, True, "primary"),
    ({"role": "peer", "position": "east"}, True, "peer"),
    (PartnerError(503, "unavailable", "down"), True, None),
    (EgressError("tcp", "cannot connect to east:443: timed out"), False, None),
    (EgressError("dns", "cannot resolve east"), False, None),
    (EgressError("proxy", "CONNECT east:443 answered 502"), False, None),
    (EgressError("tls", "handshake with east:443 failed"), None, None),
    (TokenFileError("no replication token at /etc/vakr/secrets/replication.token"), None, None),
])  # fmt: skip
def test_e39_the_partner_probe_tells_reachable_unreachable_and_unknown(
    answer: Any, reachable: bool | None, role: str | None
) -> None:
    """E.39: an HTTP answer is reachable, a DNS/TCP/proxy failure is not, the rest is unknown.

    Args:
        answer: What the partner does.
        reachable: The expected ``answered``.
        role: The expected role.

    Returns:
        None

    Raises:
        None
    """

    def client_of(_url: str) -> Any:
        """Build the fake client, or fail as a missing token file does.

        Args:
            _url: The partner URL.

        Returns:
            Any: The client.

        Raises:
            TokenFileError: For that case.
        """
        if isinstance(answer, TokenFileError):
            raise answer
        return _Answer(answer)

    probe = promotion.probe_partner(EAST, client_of)
    assert (probe.answered, probe.role) == (reachable, role)
    assert probe.detail
    assert promotion.probe_partner("", client_of).answered is None


def test_mpos003_promote_refuses_on_a_primary_and_without_a_pull() -> None:
    """MPOS-003: a primary cannot be promoted; a peer that never pulled needs --force.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    found = promotion.promote_decision(PRIMARY, None, GONE, force=True, now=NOW)
    assert not found.allowed
    assert "already a primary" in found.refusals[0]
    found = promotion.promote_decision(PEER, None, GONE, force=False, now=NOW)
    assert not found.allowed
    assert "never copied" in found.refusals[0]
    assert "none has succeeded" in found.lines[0]
    forced = promotion.promote_decision(PEER, PullStatus(), GONE, force=True, now=NOW)
    assert forced.allowed
    assert forced.forced


def test_mpos003_promote_reports_the_newest_pull_and_goes_ahead_when_the_primary_is_gone() -> None:
    """MPOS-003: the newest pull and its age are reported; an unreachable primary allows it.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    found = promotion.promote_decision(PEER, _pulled(42), GONE, force=False, now=NOW)
    assert found.allowed
    assert not found.forced
    assert "42 min" in found.lines[0] or "42m" in found.lines[0]
    assert any(EAST in line and "cannot connect" in line for line in found.lines)


def test_mpos003_promote_refuses_while_the_old_primary_answers_unless_forced() -> None:
    """MPOS-003: an answering or unknown primary blocks promotion without --force.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    alive = PartnerProbe(EAST, True, "primary", "east", "answers as primary")
    found = promotion.promote_decision(PEER, _pulled(1), alive, force=False, now=NOW)
    assert not found.allowed
    assert "still answers" in found.refusals[0]
    assert "two primaries" in found.refusals[0]
    assert "--force" in found.refusals[0]
    unknown = PartnerProbe(EAST, None, detail="tls: handshake failed")
    assert not promotion.promote_decision(PEER, _pulled(1), unknown, force=False, now=NOW).allowed
    forced = promotion.promote_decision(PEER, _pulled(1), alive, force=True, now=NOW)
    assert forced.allowed
    assert "still answers" in forced.forced[0]
    swapped = PartnerProbe(EAST, True, "peer", "east", "answers as peer")
    assert promotion.promote_decision(PEER, _pulled(1), swapped, force=False, now=NOW).allowed


def test_mpos003_promotion_makes_the_old_primary_the_observation_partner() -> None:
    """MPOS-004: after promotion the old primary is ``peer_url``, so observations flow both ways.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert promotion.promoted(PEER) == {"role": "primary", "primary_url": None, "peer_url": EAST}
    assert promotion.demoted(WEST) == {"role": "peer", "primary_url": WEST, "peer_url": None}


def test_mpos003_demote_needs_a_primary_a_url_and_a_position() -> None:
    """MPOS-003: demotion refuses on a peer, without a new primary, and without a position.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    probe = PartnerProbe(WEST, True, "primary", "west", "answers as primary")
    assert "already a peer" in promotion.demote_decision(PEER, WEST, probe).refusals[0]
    assert "--primary-url" in promotion.demote_decision(PRIMARY, "", probe).refusals[0]
    nameless = NodeSettings("primary", "", "", WEST)
    assert "node.position" in promotion.demote_decision(nameless, WEST, probe).refusals[0]
    assert promotion.demote_decision(PRIMARY, WEST, probe).allowed
    still_peer = PartnerProbe(WEST, True, "peer", "west", "answers as peer")
    found = promotion.demote_decision(PRIMARY, WEST, still_peer)
    assert found.allowed
    assert any("node promote" in line for line in found.lines)
    gone = promotion.demote_decision(PRIMARY, WEST, PartnerProbe(WEST, False, detail="tcp"))
    assert any("resyncs" in line for line in gone.lines)


def test_mpos006_two_primaries_names_the_node_to_demote() -> None:
    """MPOS-006, E.39: a primary whose partner is a primary names the older one to demote.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    later = {"role": "primary", "position": "west", "promoted_at": "2026-10-27T11:00:00+00:00"}
    why = conflict.two_primaries(PRIMARY, later, None)
    assert why is not None
    assert "this node looks like the old primary" in why
    assert "demote` here" in why
    promoted_here = "2026-10-27T11:00:00+00:00"
    seen_after = {"role": "primary", "position": "west", "seen_at": "2026-10-27T11:00:30.5+00:00"}
    own = conflict.two_primaries(PRIMARY, seen_after, promoted_here)
    assert own is not None
    assert "west looks like the old primary" in own
    # As a peer this node saw its primary say "primary"; that is not a conflict after promotion.
    seen_before = {**seen_after, "seen_at": "2026-10-27T10:59:00+00:00"}
    assert conflict.two_primaries(PRIMARY, seen_before, promoted_here) is None
    assert conflict.two_primaries(PRIMARY, {"role": "primary"}, promoted_here) is None
    neither = conflict.two_primaries(PRIMARY, {"role": "primary"}, None)
    assert neither is not None
    assert "may be discarded" in neither
    assert conflict.two_primaries(PRIMARY, {"role": "peer"}, None) is None
    assert conflict.two_primaries(PEER, later, None) is None
    assert conflict.two_primaries(PRIMARY, None, None) is None


@pytest.fixture
def installed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> InstallPaths:
    """An installed primary with a peer URL and an observation pull record.

    Args:
        tmp_path: The install root.
        monkeypatch: ``VAKR_ROOT``.

    Returns:
        InstallPaths: The layout.

    Raises:
        None
    """
    monkeypatch.setenv("VAKR_ROOT", str(tmp_path))
    paths = InstallPaths.under(tmp_path)
    for directory in (paths.config_dir, paths.state_dir):
        directory.mkdir(parents=True, exist_ok=True)
    (paths.config_dir / "config.toml").write_text(
        f'config_version = 1\n[node]\nrole = "primary"\nposition = "east"\npeer_url = "{WEST}"\n'
    )
    return paths


def test_mpos006_a_second_primary_is_critical_node_health_and_in_the_health_line(
    installed: InstallPaths, tmp_path: Path
) -> None:
    """MPOS-006, E.39: the observation pull records the partner's role; two primaries are CRITICAL.

    Args:
        installed: The node.
        tmp_path: Directory for the telemetry stores.

    Returns:
        None

    Raises:
        None
    """
    east = new_id(CollectorId)
    src, dst = k.store_at(tmp_path / "src"), k.store_at(tmp_path / "dst")
    record = installed.state_dir / rec.FILE
    now = ManualClock(datetime.now(UTC))  # the partner's answer must be fresh (E.39)
    puller = ObservationPuller(k.FakePartner(k.source_for(src, east)), k.backend(dst).writer,
                               record, now, new_id(CollectorId))  # fmt: skip
    puller.pull_once(100)
    partner = role_record.read_json(installed.state_dir / role_record.PARTNER_FILE)
    assert partner is not None
    assert partner["role"] == "primary"
    found = check_replication()
    assert found.status is Status.CRITICAL
    assert "two primaries" in found.summary
    assert any(p.label == "replication_primaries" for p in found.perfdata)
    line = replication_reader(installed.state_dir, PRIMARY)()
    assert line is not None
    assert "TWO PRIMARIES" in line.text
    role_record.write_json(installed.state_dir / role_record.PARTNER_FILE, {"role": "peer"})
    assert "two primaries" not in check_replication().summary
    src.close()
    dst.close()


def test_e39_the_newest_promotion_is_served_with_its_fork_point(tmp_path: Path) -> None:
    """E.39: a promotion's time and fork point are what the partner sees; a demotion clears it.

    Args:
        tmp_path: The state directory.

    Returns:
        None

    Raises:
        None
    """
    path = tmp_path / role_record.ROLE_FILE
    assert role_record.promotion(path) is None
    role_record.append_change(path, {"action": "promote", "at": "2026-10-27T12:00:00+00:00",
                                     "last_pull_at": "2026-10-27T11:58:00+00:00"})  # fmt: skip
    assert role_record.promotion_reader(tmp_path)() == {
        "at": "2026-10-27T12:00:00+00:00", "fork_at": "2026-10-27T11:58:00+00:00"}  # fmt: skip
    for _ in range(role_record.MAX_CHANGES + 3):
        role_record.append_change(path, {"action": "demote", "at": "x"})
    assert role_record.promotion(path) is None
    assert len(role_record.read_changes(path)) == role_record.MAX_CHANGES
    assert oct(path.stat().st_mode & 0o777) == "0o640"
