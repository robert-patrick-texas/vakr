"""``GET /api/v1/metrics``: never anonymous, always parseable, missing never zero (OBS-T02, E.33).

The real application over a memory fleet: anonymous callers get 401 over
TCP; signed-in operators, kiosk bearer tokens, and socket peers get the
text format; the answer has ``no-store``; requests are counted by fixed
group; a missing or stale collector snapshot gives ``up 0`` with the
reason and no collector series; inventory figures are read once per
cache period whatever the scrape rate.

Dependencies:
    pytest; tests.unit.api.api_testkit, tests.unit.metrics.metrics_testkit.

Example:
    $ uv run pytest tests/unit/metrics/test_metrics_endpoint.py
"""

import json
from pathlib import Path

import pytest

from tests.unit.api.api_testkit import ApiKit
from tests.unit.metrics.metrics_testkit import parse
from tests.unit.query.query_testkit import mixed_fleet
from vakr.domain.users import Role
from vakr.metrics.catalog import COLLECTOR_NAMES, build
from vakr.metrics.exposition import CONTENT_TYPE
from vakr.metrics.service import MetricsService
from vakr.metrics.snapshot import STALE_AFTER_S, encode_snapshot, write_snapshot
from vakr.metrics.state import state_families

URL = "/api/v1/metrics"


@pytest.fixture
def kit() -> ApiKit:
    """The app over the mixed fleet, with an operator and fresh metrics.

    Args:
        None

    Returns:
        ApiKit: The kit.

    Raises:
        None
    """
    kit = ApiKit.create(fleet=mixed_fleet())
    kit.context.metrics = MetricsService()
    kit.add_user("olive", Role.OPERATOR)
    return kit


def test_obs_t02_anonymous_is_refused_and_every_credential_works(kit: ApiKit) -> None:
    """OBS-005, OBS-T02: 401 without a credential; operator, kiosk bearer, socket peers get 200.

    Args:
        kit: The app.

    Returns:
        None

    Raises:
        None
    """
    anonymous = kit.client().get(URL)
    assert (anonymous.status_code, anonymous.json()["code"]) == (401, "unauthenticated")
    signed_in = kit.client()
    assert kit.login(signed_in, "olive").status_code == 200
    bearer = {"Authorization": f"Bearer {kit.kiosk_token('prometheus')}"}
    answers = [signed_in.get(URL), kit.client().get(URL, headers=bearer),
               kit.socket_client(Role.OPERATOR).get(URL),
               kit.socket_client(Role.ADMIN).get(URL)]  # fmt: skip
    for answer in answers:
        assert answer.status_code == 200
        assert answer.headers["content-type"] == CONTENT_TYPE
        assert answer.headers["cache-control"] == "no-store"
        parse(answer.text)
    refused = kit.socket_client(None).get(URL)
    assert (refused.status_code, refused.json()["code"]) == (403, "peer_refused")
    assert "vkk_" not in answers[1].text
    assert "olive" not in answers[0].text


def test_e33_requests_are_counted_by_fixed_group_and_code(kit: ApiKit) -> None:
    """E.33, OBS-004: answers are counted by group and code; sign-in failures show as login 401.

    Args:
        kit: The app.

    Returns:
        None

    Raises:
        None
    """
    client = kit.socket_client(Role.OPERATOR)
    client.get("/api/v1/devices")
    client.get("/api/v1/devices/00000000-0000-0000-0000-000000000000")
    kit.client().post("/api/v1/auth/login", json={"username": "olive", "password": "nope"})
    counts = parse(client.get(URL).text)["vakr_api_requests_total"].values()
    assert counts[(("group", "reads"), ("code", "200"))] == 1
    assert counts[(("group", "reads"), ("code", "404"))] == 1
    assert counts[(("group", "login"), ("code", "401"))] == 1
    families = parse(client.get(URL).text)
    durations = families["vakr_api_request_duration_seconds"]
    assert any(n.endswith("_count") and v == 2 for n, labels, v in durations.samples
               if labels == (("group", "reads"),))  # fmt: skip
    assert all(dict(labels).get("group") != "/api/v1/devices"
               for _, labels, _ in families["vakr_api_requests_total"].samples)  # fmt: skip


def _collector_snapshot(path: Path, written_at: float) -> None:
    """Write a small valid collector snapshot.

    Args:
        path: The file.
        written_at: Unix seconds.

    Returns:
        None

    Raises:
        None
    """
    families = [build("vakr_collector_workers", [((), 4)]),
                build("vakr_collector_polls_started_total", [((), 120)])]  # fmt: skip
    write_snapshot(path, encode_snapshot(families, written_at))


@pytest.mark.parametrize(
    ("age", "reason"), [(1.0, "ok"), (STALE_AFTER_S + 5, "stale"), (None, "missing")]
)
def test_invariant1_collector_series_appear_only_from_a_fresh_snapshot(
    kit: ApiKit, tmp_path: Path, age: float | None, reason: str
) -> None:
    """Invariant 1, OBS-T01: a missing or stale snapshot gives up 0 and no collector series.

    Args:
        kit: The app.
        tmp_path: Directory for the snapshot.
        age: Snapshot age, or ``None`` for no file.
        reason: Expected reason.

    Returns:
        None

    Raises:
        None
    """
    path = tmp_path / "collector-metrics.json"
    now = kit.fleet.clock.now_utc().timestamp()
    if age is not None:
        _collector_snapshot(path, now - age)
    service = MetricsService(snapshot_path=path)
    text = service.render(service.live(), now_s=now)
    families = parse(text)
    up = families["vakr_collector_metrics_up"].values()
    assert up == {(("reason", reason),): 1.0 if reason == "ok" else 0.0}
    collector = {n for n in families if n in COLLECTOR_NAMES}
    if reason == "ok":
        assert collector == {"vakr_collector_workers", "vakr_collector_polls_started_total"}
        assert families["vakr_collector_metrics_age_seconds"].values() == {(): 1.0}
    else:
        assert collector == set()
        assert ("vakr_collector_metrics_age_seconds" in families) == (reason == "stale")


def test_e33_inventory_figures_are_cached_and_never_zero_when_unreadable(kit: ApiKit) -> None:
    """E.33 #5: aggressive scrapes cost one database pass per period; trouble is absence, not 0.

    Args:
        kit: The app.

    Returns:
        None

    Raises:
        None
    """
    calls = []

    def reader() -> list:
        """Count the database passes.

        Args:
            None

        Returns:
            list: The fleet's state families.

        Raises:
            None
        """
        calls.append(1)
        return state_families(kit.fleet.uow, kit.context.overview)

    kit.context.metrics = MetricsService(readers=(reader,))
    client = kit.socket_client(Role.OPERATOR)
    texts = [client.get(URL).text for _ in range(5)]
    assert len(calls) == 1
    families = parse(texts[-1])
    assert families["vakr_devices"].values()[(("admin_state", "enabled"),)] == 5
    assert families["vakr_devices"].values()[(("admin_state", "disabled"),)] == 1
    buckets = families["vakr_devices_by_status"].values()
    assert sum(buckets.values()) == 6

    def broken() -> object:
        """A metadata store that cannot be opened.

        Args:
            None

        Returns:
            object: Never.

        Raises:
            StorageError: Always.
        """
        from vakr.domain.errors import StorageError

        raise StorageError("database is locked", retryable=True)

    down = state_families(broken, None)  # ty: ignore[invalid-argument-type]
    assert [f.name for f in down] == ["vakr_state_metrics_up"]
    assert down[0].samples[0].labels == (("reason", "metadata_unavailable"),)
    assert json.dumps([s.value for s in down[0].samples]) == "[0.0]"
