"""The read surface: paging, bounded history, per-position series, exports (API-005, API-006).

Covers API-T02 (stable paging without duplicates or gaps), API-T04 (bounded
history that discloses its resolution), API-T05 (UUIDs only), API-T09 and
API-012 (bounded exports), MPOS-008 (one series per position), and UI-014
(telemetry trouble stays a 200 with ``available: false``).

Dependencies:
    tests.unit.api.api_testkit, tests.unit.query.history_testkit.

Example:
    $ uv run pytest tests/unit/api/test_api_reads.py
"""

import re
import uuid
from typing import Any

import pytest

from tests.unit.api.api_testkit import ApiKit
from tests.unit.query.history_testkit import ETHERNET, minutes
from vakr.api import routes_export
from vakr.domain.enums import InterestClass
from vakr.domain.errors import ValidationError
from vakr.domain.interfaces import InterfaceAnnotation
from vakr.domain.users import Role

UUID_TEXT = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def ids_are_uuids(value: Any, path: str = "") -> None:
    """Assert every ``id`` and ``*_id`` field in a JSON answer is a UUID or null (API-T05).

    Args:
        value: Decoded JSON.
        path: Where it sits.

    Returns:
        None

    Raises:
        AssertionError: On an integer or other non-UUID identifier.
    """
    if isinstance(value, list):
        for i, item in enumerate(value):
            ids_are_uuids(item, f"{path}[{i}]")
    elif isinstance(value, dict):
        for key, item in value.items():
            if key == "id" or (key.endswith("_id") and key != "request_id"):
                ok = item is None or (isinstance(item, str) and UUID_TEXT.fullmatch(item))
                assert ok or key in ("circuit_id",), f"{path}.{key} = {item!r}"
            ids_are_uuids(item, f"{path}.{key}")


def traverse(client: Any, path: str, limit: int, **params: str) -> tuple[list[Any], set[int]]:
    """Walk every page of a list with its cursors.

    Args:
        client: The client.
        path: List endpoint.
        limit: Page size.
        **params: Filters.

    Returns:
        tuple: All rows or items in order, and the totals seen.

    Raises:
        None
    """
    rows, totals, cursor = [], set(), None
    for _ in range(50):
        query = {"limit": str(limit), **params} | ({"cursor": cursor} if cursor else {})
        body = client.get(path, params=query).json()
        rows += body.get("rows", body.get("items", []))
        totals.add(body["page"]["total"])
        cursor = body["page"]["next_cursor"]
        if cursor is None:
            break
    return rows, totals


def test_api_t02_device_and_interface_paging_is_complete_and_stable() -> None:
    """API-T02, API-005: cursors walk every row once; totals stay; bounds hold.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = ApiKit.create()
    names = [f"edge{n}" for n in range(7)]
    devices = [kit.fleet.device(n) for n in names]
    for n in range(5):
        kit.fleet.interface(devices[0], f"Gi0/{n}", if_type=ETHERNET, if_high_speed=1000)
    client = kit.socket_client(Role.OPERATOR)
    rows, totals = traverse(client, "/api/v1/devices", 3, sort="name")
    assert [r["name"] for r in rows] == names
    assert totals == {7}
    ifaces, totals = traverse(client, f"/api/v1/devices/{devices[0].id}/interfaces", 2)
    assert len({r["ref"]["interface_id"] for r in ifaces}) == 5
    assert totals == {5}
    everything, _ = traverse(client, "/api/v1/interfaces", 2, sort="name")
    assert len(everything) == 5
    for n in range(3):
        kit.fleet.site(f"Site {n}", f"S{n}")
    sites, totals = traverse(client, "/api/v1/sites", 2)
    assert [s["name"] for s in sites] == ["Site 0", "Site 1", "Site 2"]
    assert totals == {3}
    ids_are_uuids(client.get("/api/v1/devices").json())


def test_api_t04_history_is_bounded_and_discloses_resolution() -> None:
    """API-T04, API-006, SEC-019: refused out of bounds; answers say source, zone, truncation.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = ApiKit.create()
    device = kit.fleet.device("core1")
    iface = kit.fleet.interface(device, "Gi0/0/1", if_type=ETHERNET, if_high_speed=1000)
    minutes(kit.fleet, iface, range(30), in_bps=1000.0)
    client = kit.socket_client(Role.OPERATOR)
    url = f"/api/v1/interfaces/{iface.id}/history/traffic"
    body = client.get(url, params={"range": "1h"}).json()
    assert body["window"]["timezone"] == "UTC"
    assert "truncated" in body
    for series in body["series"]:
        assert {"source", "bucket_seconds"} <= set(series["resolutions"][0])
        assert {"truncated", "gaps", "markers"} <= set(series)
    for params, field in (
        ({"start": "2025-01-01T00:00:00Z", "end": "2026-10-20T00:00:00Z"}, "end"),
        ({"start": "2026-10-20T10:00:00", "end": "2026-10-20T11:00:00"}, "start"),
        ({"start": "2026-10-20T11:00:00Z", "end": "2026-10-21T11:00:00Z"}, "end"),
        ({"max_points": "1201"}, "max_points"),
        ({"range": "2y"}, "range"),
        ({"source": "guess"}, "source"),
    ):
        refused = client.get(url, params=params)
        assert refused.status_code == 422, params
        assert any(f["field"] == field for f in refused.json()["fields"]), refused.json()
    custom = client.get(url, params={"start": "2026-10-20T11:00:00+02:00",
                                     "end": "2026-10-20T11:30:00+02:00"})  # fmt: skip
    assert custom.status_code == 200
    assert custom.json()["window"]["start"] == "2026-10-20T09:00:00Z"
    for kind in ("packets", "errors", "utilization", "status"):
        assert client.get(f"/api/v1/interfaces/{iface.id}/history/{kind}").status_code == 200


def test_mpos008_positions_are_separate_series_in_answers() -> None:
    """MPOS-008, MPOS-T04: traffic and latency answers keep each collector's series apart.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = ApiKit.create(positions=("east", "west"))
    device = kit.fleet.device("core1")
    iface = kit.fleet.interface(device, "Gi0/0/1", if_type=ETHERNET, if_high_speed=1000)
    minutes(kit.fleet, iface, range(5), position="east", in_bps=100.0)
    minutes(kit.fleet, iface, range(5), position="west", in_bps=900.0)
    kit.fleet.poll(device, position="east")
    kit.fleet.poll(device, position="west")
    client = kit.socket_client(Role.OPERATOR)
    graph = client.get(f"/api/v1/interfaces/{iface.id}/history/traffic", params={"range": "1h"})
    inbound = [s for s in graph.json()["series"] if s["metric"] == "in_bps"]
    assert {s["position"] for s in inbound} == {"east", "west"}
    assert len({s["collector_id"] for s in inbound}) == 2
    west = str(kit.fleet.collectors["west"].id)
    only = client.get(f"/api/v1/interfaces/{iface.id}/history/traffic",
                      params={"range": "1h", "collector": west}).json()  # fmt: skip
    assert {s["position"] for s in only["series"]} == {"west"}
    latency = client.get(f"/api/v1/devices/{device.id}/history/reachability").json()
    assert [p["position"] for p in latency["positions"]] == ["east", "west"]


def test_ui014_views_answer_200_when_telemetry_is_unavailable() -> None:
    """UI-014, UI-015, invariant 2: no telemetry database is a view state, never an error.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = ApiKit.create(telemetry=False)
    device = kit.fleet.device("core1")
    iface = kit.fleet.interface(device, "Gi0/0/1", if_type=ETHERNET, if_high_speed=1000)
    client = kit.socket_client(Role.OPERATOR)
    graph = client.get(f"/api/v1/interfaces/{iface.id}/history/traffic")
    assert graph.status_code == 200
    assert graph.json()["available"] is False
    listing = client.get("/api/v1/devices").json()
    assert listing["health"]["telemetry"]["value"] != "ok"
    assert "Down" not in str(listing["rows"][0]["status"]["label"])
    ready = kit.client().get("/api/v1/health/ready").json()
    assert ready["status"] == "degraded"
    detail = client.get("/api/v1/health/detail").json()
    assert {c["name"]: c["status"] for c in detail["checks"]}["telemetry"] == "unavailable"


def test_views_detail_search_and_catalog_answer() -> None:
    """12.1-12.5, UI-008, UI-017: the views a front end composes pages from all answer.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = ApiKit.create()
    device = kit.fleet.device("core1")
    note = InterfaceAnnotation(is_wan_circuit=True, provider="Example ISP",
                               interest=InterestClass.PRIMARY)  # fmt: skip
    wan = kit.fleet.interface(device, "Gi0/0/0", if_type=ETHERNET, if_high_speed=1000,
                              annotation=note)  # fmt: skip
    kit.fleet.sample(wan, in_bps=5_000_000.0)
    client = kit.socket_client(Role.OPERATOR)
    detail = client.get(f"/api/v1/interfaces/{wan.id}").json()
    assert detail["interface"]["provider"] == "Example ISP"
    assert detail["current"]["ref"]["interface_id"] == str(wan.id)
    assert client.get(f"/api/v1/devices/{device.id}").json()["row"]["name"] == "core1"
    hits = client.get("/api/v1/search", params={"q": "gi0/0/0"}).json()["hits"]
    assert any(h.get("interface_id") == str(wan.id) for h in hits)
    assert client.get("/api/v1/wan-circuits").json()["rows"][0]["ref"]["provider"] == "Example ISP"
    assert client.get("/api/v1/high-priority").json()["page"]["total"] == 1
    overview = client.get("/api/v1/overview").json()
    assert {"devices", "health", "top_utilization", "events"} <= set(overview)
    for path in ("/api/v1/rankings/utilization", "/api/v1/rankings/errors", "/api/v1/tags",
                 "/api/v1/groups", "/api/v1/collectors", "/api/v1/identity-reviews",
                 f"/api/v1/interfaces/{wan.id}/identity-history"):  # fmt: skip
        answer = client.get(path, params={"window": "1h"} if "rankings" in path else None)
        assert answer.status_code == 200, path
        ids_are_uuids(answer.json())
    collector = kit.fleet.collectors["east"]
    health = client.get(f"/api/v1/collectors/{collector.id}/health").json()
    assert health["position"] == "east"
    assert client.get(f"/api/v1/collectors/{uuid.uuid4()}/health").status_code == 404
    assert client.get(f"/api/v1/interfaces/{uuid.uuid4()}").status_code == 404
    assert client.get(f"/api/v1/devices/{uuid.uuid4()}/interfaces").status_code == 404


def test_api012_exports_are_attachments_with_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    """API-012, API-T09: CSV and JSON files with UTC times and zone; too large is a 422.

    Args:
        monkeypatch: Simulates an oversized export.

    Returns:
        None

    Raises:
        None
    """
    kit = ApiKit.create()
    device = kit.fleet.device("core1")
    iface = kit.fleet.interface(device, "Gi0/0/1", if_type=ETHERNET, if_high_speed=1000)
    minutes(kit.fleet, iface, range(10), in_bps=1000.0)
    client = kit.socket_client(Role.OPERATOR)
    csv_file = client.get(f"/api/v1/exports/interfaces/{iface.id}/history/traffic",
                          params={"range": "1h"})  # fmt: skip
    assert csv_file.headers["content-type"].startswith("text/csv")
    assert csv_file.headers["content-disposition"].startswith("attachment; filename=")
    assert csv_file.text.splitlines()[0].startswith("time_utc,metric,position")
    as_json = client.get(f"/api/v1/exports/devices/{device.id}/history/reachability",
                         params={"format": "json"})  # fmt: skip
    assert as_json.json()["window"]["timezone"] == "UTC"
    ranking = client.get("/api/v1/exports/rankings/utilization")
    assert ranking.text.startswith("rank,device,interface")
    errors = client.get("/api/v1/exports/rankings/errors", params={"format": "json"})
    assert errors.json()["kind"] == "errors"

    def too_big(*_args: object) -> None:
        """Refuse like an export over 20,000 rows.

        Args:
            *_args: Ignored.

        Returns:
            None

        Raises:
            ValidationError: Always.
        """
        raise ValidationError("the export is too large; narrow the range", field="range")

    monkeypatch.setattr(routes_export, "export_view", too_big)
    refused = client.get(f"/api/v1/exports/interfaces/{iface.id}/history/traffic")
    assert (refused.status_code, refused.json()["fields"][0]["field"]) == (422, "range")
