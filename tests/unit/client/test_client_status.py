"""``SocketStatusSource``: exactly the in-process views, and every failure mapped (TUI-007, 13.4).

The real application runs under uvicorn with ``PeerProtocol`` on a Unix
socket over the mixed fleet. For each request the socket client's answer
must equal what the API's own ``OverviewQueries`` returns in-process, with
telemetry available and unavailable (E.29 #10). Lookups by name and by
UUID find the device ``find_device`` would. Hand-written servers cover
answers a real ``vakr-api`` should never send.

Dependencies:
    pytest; tests.unit.api.api_testkit, tests.unit.client.client_testkit.

Example:
    $ uv run pytest tests/unit/client/test_client_status.py
"""

import pytest

from tests.unit.api.api_testkit import ApiKit
from tests.unit.client.client_testkit import ROOT, api_socket, canned, http, raw_server, short_dir
from tests.unit.query.query_testkit import device_id_of, mixed_fleet
from vakr.client.answers import envelope, failure
from vakr.client.status import SocketStatusSource, list_query
from vakr.domain.enums import StatusBucket
from vakr.domain.errors import StorageError, ValidationError
from vakr.domain.ids import CollectorId, new_id
from vakr.domain.paging import MAX_OFFSET, offset_of
from vakr.query import (
    DeviceListRequest,
    DeviceSort,
    FreshnessFilter,
    SourceUnavailableError,
    StatusSource,
)
from vakr.query.source import MAX_DEVICE_KEY_LENGTH

REQUESTS = (
    DeviceListRequest(),
    DeviceListRequest(search="sw"),
    DeviceListRequest(buckets=frozenset({StatusBucket.ATTENTION})),
    DeviceListRequest(sort=DeviceSort.NAME, offset=2, limit=2),
    DeviceListRequest(sort=DeviceSort.AGE, include_retired=True),
    DeviceListRequest(freshness=frozenset({FreshnessFilter.STALE})),
)


@pytest.mark.parametrize("telemetry", [True, False], ids=["telemetry", "no-telemetry"])
def test_e29_10_socket_answers_equal_the_in_process_views(telemetry: bool) -> None:
    """E.29 #10, TUI-007: list pages and device screens are identical on both paths.

    Args:
        telemetry: Whether the node has a telemetry database.

    Returns:
        None

    Raises:
        None
    """
    fleet = mixed_fleet()
    kit = ApiKit.create(fleet=fleet, telemetry=telemetry)
    local = kit.context.overview
    with short_dir() as d, api_socket(kit.app, d / "api.sock") as source:
        assert isinstance(source, StatusSource)
        source.check()
        for request in REQUESTS:
            assert source.device_list(request) == local.device_list(request)
        for row in local.device_list().rows:
            assert source.device_detail(str(row.device_id)) == local.device_detail(
                str(row.device_id)
            )
        by_name = source.device_detail("core")
        assert by_name is not None
        assert by_name == local.device_detail("core")
        assert by_name.row.device_id == device_id_of(fleet, "core")
        assert source.device_detail("Core") is None  # names compare exactly, as find_device does
        assert source.device_detail("no-such-device") is None
        assert source.device_detail("") is None
        assert source.device_detail("x" * (MAX_DEVICE_KEY_LENGTH + 1)) is None


@pytest.mark.skipif(ROOT, reason="root is admin on the socket by rule")
def test_e30_2_a_refused_account_is_told_how_to_join() -> None:
    """E.30 #2, 13.4: 403 ``peer_refused`` names the groups and the way in.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = ApiKit.create(fleet=mixed_fleet())
    with short_dir() as d, api_socket(kit.app, d / "api.sock", refuse=True) as source:
        for read in (source.check, source.device_list, lambda: source.device_detail("core")):
            with pytest.raises(SourceUnavailableError) as exc:
                read()
            assert "only root and members of vakr-admin or vakr may" in str(exc.value)
            assert "sudo usermod -aG vakr NAME" in str(exc.value)


def test_answers_map_onto_the_local_failures() -> None:
    """13.4: 422 is a usage error, 503 storage trouble, anything else the API being unusable.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert isinstance(
        failure(422, ("invalid_request", "limit too big", "r1"), "/s"), ValidationError
    )
    storage = failure(
        503, ("storage_unavailable", "the metadata database is unavailable", "r2"), "/s"
    )
    assert isinstance(storage, StorageError)
    assert str(storage) == "the metadata database is unavailable (vakr-api, request r2)"
    other = str(failure(500, ("internal_error", "the request failed", "r3"), "/s"))
    assert other.startswith(
        "vakr-api answered HTTP 500 internal_error: the request failed (request r3)"
    )
    assert "journalctl -u vakr-api" in other
    bare = str(failure(401, envelope(None), "/s"))
    assert bare.startswith("vakr-api answered HTTP 401 without an error code (no request ID)")
    long = envelope({"code": 5, "message": "m" * 2000, "request_id": "r"})
    assert (long[0], len(long[1]), long[2]) == ("", 512, "r")


@pytest.mark.parametrize(
    ("answer", "says"),
    [
        (http(200, b"{}"), "vakr-api's answer does not fit this release's views ($.rows: missing)"),
        (http(200, b"[1, 2]"), "the answer is not a JSON object"),
        (http(200, b"not json"), "the answer is not a JSON object"),
        (http(200, b"[" * 200_000 + b"]" * 200_000), "the answer is not a JSON object"),
        (http(404, b'{"code": "not_found", "message": "gone"}'), "HTTP 404 not_found: gone"),
        (http(503, b'{"code": "busy", "message": "too many"}'), "HTTP 503 busy: too many"),
    ],
)
def test_malformed_and_mismatched_lists_are_the_api_being_unusable(
    answer: bytes, says: str
) -> None:
    """13.4: a list answer that is not the view, or a failure, is never shown as data.

    Args:
        answer: What the server sends.
        says: Part of the expected message.

    Returns:
        None

    Raises:
        None
    """
    with (
        short_dir() as d,
        raw_server(d / "api.sock", canned(answer)),
        pytest.raises(SourceUnavailableError) as exc,
    ):
        SocketStatusSource(str(d / "api.sock")).device_list()
    assert says in str(exc.value)


def test_list_query_carries_every_request_field_and_the_offset_as_a_cursor() -> None:
    """E.30 #7: the offset travels as the API's cursor; sets are sent in a fixed order.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    collector = new_id(CollectorId)
    request = DeviceListRequest(
        search="core", buckets=frozenset({StatusBucket.OFFLINE, StatusBucket.ATTENTION}),
        sort=DeviceSort.SITE, offset=200, limit=50, include_retired=True,
        freshness=frozenset({FreshnessFilter.NEVER, FreshnessFilter.FRESH}), collector=collector,
    )  # fmt: skip
    query = list_query(request)
    assert query[:-1] == [
        ("sort", "site"), ("limit", "50"), ("search", "core"), ("bucket", "attention"),
        ("bucket", "offline"), ("include_retired", "true"), ("freshness", "fresh"),
        ("freshness", "never"), ("collector", str(collector)),
    ]  # fmt: skip
    assert query[-1][0] == "cursor"
    assert offset_of(query[-1][1]) == 200
    with pytest.raises(ValidationError, match="offset is limited to"):
        SocketStatusSource("/nonexistent/api.sock").device_list(
            DeviceListRequest(offset=MAX_OFFSET + 1)
        )
