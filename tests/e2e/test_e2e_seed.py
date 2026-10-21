"""The end-to-end seed says what the Playwright suite relies on (WEB-T01 to WEB-T07, WEB-T11).

The install is built exactly as ``tests.e2e.serve`` builds it, then opened
with ``build_parts`` (what ``vakr api serve`` runs) and read through the
real application as a socket operator, without starting a process. Each
check names the browser test that depends on it.

Dependencies:
    tests.e2e.seed; vakr.app.api.build; tests.unit.api.api_testkit.peer_client.

Example:
    $ uv run pytest tests/e2e
"""

from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

import pytest

from tests.e2e.fleet import EXPECTED_ORDER
from tests.e2e.seed import ADMIN_USER, DISPLAY_ZONE, OPERATOR_USER, Install, build_install
from tests.e2e.telemetry import GAP_MINUTES, LOSS_OFFSETS, OPER_DOWN_MINUTES
from tests.unit.api.api_testkit import peer_client
from tests.unit.auth.auth_testkit import cheap_hasher, new_password
from vakr.api.app import create_app
from vakr.app.api.build import build_parts
from vakr.auth import Passwords
from vakr.domain.users import Role


@pytest.fixture(scope="module")
def served(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[Install, Any]]:
    """Seed one install and serve it in-process to a socket operator.

    Args:
        tmp_path_factory: Temporary directories for the module.

    Returns:
        Iterator[tuple[Install, Any]]: The install and a test client.

    Raises:
        None
    """
    passwords = {ADMIN_USER: new_password(), OPERATOR_USER: new_password()}
    hashing = Passwords(cheap_hasher())
    install = build_install(tmp_path_factory.mktemp("e2e"), 18999, "normal", passwords, hashing)
    parts = build_parts(install.paths, passwords=hashing)
    try:
        yield install, peer_client(create_app(parts.context), Role.OPERATOR)
    finally:
        parts.close()
        if install.collector is not None:
            install.collector.close()


def test_web_t01_device_list_order_and_hidden_retired(served: tuple[Install, Any]) -> None:
    """WEB-T01, UI-005: Attention, Active, Offline, natural names; retired hidden.

    Args:
        served: The install and client.

    Returns:
        None

    Raises:
        None
    """
    _, client = served
    page = client.get("/api/v1/devices", params={"limit": 50}).json()
    assert tuple(r["name"] for r in page["rows"]) == EXPECTED_ORDER
    labels = {r["name"]: r["status"]["label"] for r in page["rows"]}
    assert labels["branch3"] == "Down"
    assert labels["branch9"].startswith("Unknown")
    assert labels["core1"] == "OK"
    every = client.get("/api/v1/devices", params={"include_retired": "true"}).json()
    assert "old-rtr" in {r["name"] for r in every["rows"]}


def test_web_t02_t03_t11_core1_interfaces_priority_first(served: tuple[Install, Any]) -> None:
    """WEB-T02, WEB-T03, WEB-T11, UI-006, UI-017: 30 interfaces, sticky and WAN first.

    Args:
        served: The install and client.

    Returns:
        None

    Raises:
        None
    """
    install, client = served
    core1 = install.seeded.devices["core1"].id
    detail = client.get(f"/api/v1/devices/{core1}").json()
    assert detail["interfaces_total"] == 30
    names = [i["name"] for i in detail["interfaces"]]
    assert names[:6] == ["Te1/0/1", "Te1/0/2", "Gi0/0/1", "Gi0/0/2", "Gi0/0/3", "Gi0/0/4"]
    page = client.get(f"/api/v1/devices/{core1}/interfaces", params={"limit": 12}).json()
    assert len(page["rows"]) == 12
    assert page["page"]["next_cursor"] is not None


def test_web_t04_t06_traffic_gap_baseline_and_zone(served: tuple[Install, Any]) -> None:
    """WEB-T04, WEB-T06, UI-010, TIME-003: gaps and invalid baselines are markers, never zero.

    Args:
        served: The install and client.

    Returns:
        None

    Raises:
        None
    """
    install, client = served
    iface = install.seeded.interfaces["core1/Gi0/0/1"].id
    view = client.get(f"/api/v1/interfaces/{iface}/history/traffic", params={"range": "6h"}).json()
    assert view["window"]["timezone"] == DISPLAY_ZONE
    inbound = next(s for s in view["series"] if s["metric"] == "in_bps")
    kinds = {m["kind"] for m in inbound["markers"]}
    assert {"gap", "baseline"} <= kinds or {"gap", "discontinuity"} <= kinds
    assert any(p["value"] is None for p in inbound["points"])
    gap_start = install.anchor - timedelta(minutes=max(GAP_MINUTES))
    gap_end = install.anchor - timedelta(minutes=min(GAP_MINUTES))
    in_gap = [
        p for p in inbound["points"] if gap_start <= datetime.fromisoformat(p["t"]) <= gap_end
    ]
    assert all(p["value"] is None for p in in_gap)  # missing is never zero (invariant 1)
    down_start = install.anchor - timedelta(minutes=max(OPER_DOWN_MINUTES))
    zeros = [datetime.fromisoformat(p["t"]) for p in inbound["points"] if p["value"] == 0]
    assert zeros
    assert all(z >= down_start for z in zeros)
    day = client.get(f"/api/v1/interfaces/{iface}/history/traffic", params={"range": "24h"})
    assert day.status_code == 200
    assert day.json()["available"] is True


def test_web_t05_smoke_has_lost_probes(served: tuple[Install, Any]) -> None:
    """WEB-T05, ICMP-009: the per-probe smoke shows the seeded losses.

    Args:
        served: The install and client.

    Returns:
        None

    Raises:
        None
    """
    install, client = served
    core1 = install.seeded.devices["core1"].id
    view = client.get(
        f"/api/v1/devices/{core1}/history/reachability", params={"range": "6h"}
    ).json()
    position = view["positions"][0]
    assert position["smoke_mode"] == "probes"
    losses = sorted({p["loss_pct"] for p in position["probes"] if p["loss_pct"] > 0})
    assert losses == [20.0, 40.0, 100.0]
    assert set(LOSS_OFFSETS) == {40, 50, 60}


def test_web_t07_west_is_unhealthy_and_review_is_open(served: tuple[Install, Any]) -> None:
    """WEB-T07, UI-014: the stale collector is reported, never as devices down; a review is open.

    Args:
        served: The install and client.

    Returns:
        None

    Raises:
        None
    """
    install, client = served
    collectors = client.get("/api/v1/collectors").json()["items"]
    states = {c["position"]: (c["health"] or {}).get("state", {}).get("value") for c in collectors}
    assert states["east"] == "healthy"
    assert states["west"] != "healthy"
    reviews = client.get("/api/v1/identity-reviews").json()
    assert [r["id"] for r in reviews["items"]] == install.seeded.reviews
    assert reviews["items"][0]["device"] == "dist1"


def test_fixtures_hold_names_and_uuids_only(served: tuple[Install, Any]) -> None:
    """The fixtures file the browser reads carries no secret (SEC-003).

    Args:
        served: The install and client.

    Returns:
        None

    Raises:
        None
    """
    install, _ = served
    fixtures = install.seeded.fixtures("normal", install.anchor)
    assert set(fixtures) == {"scenario", "anchor", "collectors", "devices", "interfaces", "sites",
                             "tags", "groups", "reviews"}  # fmt: skip
    assert "core1/Gi0/0/1" in fixtures["interfaces"]
    assert "password" not in str(fixtures).lower()


def test_live_collector_writes_one_poll_a_minute(served: tuple[Install, Any]) -> None:
    """E.3: the fixture collector adds the next poll of every kept device, so freshness holds.

    Runs last: it writes a poll two minutes after the anchor.

    Args:
        served: The install and client.

    Returns:
        None

    Raises:
        None
    """
    install, client = served
    collector = install.collector
    assert collector is not None
    assert collector.tick(install.anchor + timedelta(seconds=20)) == 1  # health only
    written = collector.tick(install.anchor + timedelta(minutes=2, seconds=5))
    assert written > 40
    assert collector.last_poll == install.anchor + timedelta(minutes=2)
    rows = client.get("/api/v1/devices", params={"limit": 50}).json()["rows"]
    assert {r["name"]: r["status"]["label"] for r in rows}["branch3"] == "Down"
