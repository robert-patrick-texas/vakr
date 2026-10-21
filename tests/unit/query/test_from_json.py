"""``from_jsonable``: the API's JSON comes back as exactly the in-process views (TUI-007, E.29 #10).

The round trip ``from_jsonable(T, json(to_jsonable(view))) == view`` is
checked on real views from ``StatusQueries``: the list and every device's
screen, with telemetry available and unavailable. The refusals cover each
way an answer can fail to fit, and the E.29 depth and size bounds.

Dependencies:
    pytest; tests.unit.query.query_testkit.

Example:
    $ uv run pytest tests/unit/query/test_from_json.py
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import UUID

import pytest

from tests.unit.query.query_testkit import mixed_fleet
from vakr.domain.ids import DeviceId
from vakr.query import (
    ContractError,
    DeviceDetailView,
    DeviceListRequest,
    DeviceListView,
    DeviceSort,
    Reading,
    StatusView,
    from_jsonable,
    to_jsonable,
)
from vakr.query import from_json as module


def wire(view: object) -> Any:
    """Send a view through JSON text, as the API and the socket client do.

    Args:
        view: A view.

    Returns:
        Any: The parsed JSON.

    Raises:
        None
    """
    return json.loads(json.dumps(to_jsonable(view), ensure_ascii=False, separators=(",", ":")))


@pytest.mark.parametrize("telemetry", [True, False], ids=["telemetry", "no-telemetry"])
def test_e29_10_real_views_round_trip_exactly(telemetry: bool) -> None:
    """E.29 #10, TUI-007: list and detail views survive JSON unchanged, types and all.

    Args:
        telemetry: Whether the fleet has a telemetry store.

    Returns:
        None

    Raises:
        None
    """
    queries = mixed_fleet().queries(telemetry=telemetry)
    for request in (DeviceListRequest(), DeviceListRequest(sort=DeviceSort.AGE, offset=2, limit=3)):
        view = queries.device_list(request)
        assert from_jsonable(DeviceListView, wire(view)) == view
    for row in queries.device_list().rows:
        detail = queries.device_detail(str(row.device_id))
        assert detail is not None
        back = from_jsonable(DeviceDetailView, wire(detail))
        assert back == detail
        values = [r.value for _, r in back.facts]
        assert [type(v) for v in values] == [type(r.value) for _, r in detail.facts]


def test_unknown_keys_pass_and_defaults_fill_missing_optional_fields() -> None:
    """E.30 #8: an answer may carry more than the view (``page``, later additions).

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    view = mixed_fleet().queries().device_list()
    doc = wire(view)
    doc["page"] = {"limit": 100, "total": 6, "next_cursor": None}
    doc["counts"]["added_later"] = 1
    assert from_jsonable(DeviceListView, doc) == view
    assert from_jsonable(Reading, {"text": "—"}) == Reading("—")


class Shade(Enum):
    """A test enum."""

    DARK = "dark"


@dataclass(frozen=True, slots=True)
class Sample:
    """Every shape the decoder handles.

    Attributes:
        when: An aware time.
        ids: A list of NewType UUIDs.
        pair: A fixed tuple.
        tags: A frozenset.
        named: A mapping.
        shade: An enum or nothing.
        ratio: A float.
    """

    when: datetime
    ids: list[DeviceId]
    pair: tuple[int, str]
    tags: frozenset[str]
    named: Mapping[str, int]
    shade: Shade | None
    ratio: float


@dataclass(frozen=True, slots=True)
class Either:
    """A union without ``None``.

    Attributes:
        value: A number or text.
    """

    value: int | str


GOOD = {
    "when": "2026-10-21T12:00:00Z",
    "ids": ["0190a3c4-0000-7000-8000-000000000001"],
    "pair": [1, "a"],
    "tags": ["x", "y"],
    "named": {"a": 1},
    "shade": "dark",
    "ratio": 2,
}


def test_every_supported_shape_decodes() -> None:
    """Times, NewType UUIDs, fixed tuples, sets, mappings, enums; numbers kept as sent.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    sample = from_jsonable(Sample, GOOD)
    assert sample.when == datetime(2026, 10, 21, 12, tzinfo=UTC)
    assert sample.ids == [UUID("0190a3c4-0000-7000-8000-000000000001")]
    assert (sample.pair, sample.tags, sample.named) == ((1, "a"), frozenset({"x", "y"}), {"a": 1})
    assert sample.shade is Shade.DARK
    assert type(sample.ratio) is int
    assert from_jsonable(Sample, {**GOOD, "shade": None}).shade is None


@pytest.mark.parametrize(
    ("change", "where"),
    [
        ({"when": "2026-10-21T12:00:00"}, "$.when: a time without an offset"),
        ({"when": "yesterday"}, "$.when: not a datetime"),
        ({"when": 5}, "$.when: expected datetime text"),
        ({"ids": ["not-a-uuid"]}, "$.ids[0]: not a UUID"),
        ({"pair": [1]}, "$.pair: expected 2 items, got 1"),
        ({"pair": [True, "a"]}, "$.pair[0]: expected int, got bool"),
        ({"tags": "x"}, "$.tags: expected an array"),
        ({"named": []}, "$.named: expected an object"),
        ({"shade": "light"}, "$.shade: expected Shade | NoneType"),
        ({"ratio": "2"}, "$.ratio: expected float, got str"),
        ({"ratio": None}, "$.ratio: expected float, got NoneType"),
    ],
)
def test_answers_that_do_not_fit_are_refused_with_the_path(
    change: dict[str, Any], where: str
) -> None:
    """A wrong type, value, or shape is a ``ContractError`` naming where, never the value.

    Args:
        change: Fields to replace in a good answer.
        where: Start of the expected message.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(ContractError) as exc:
        from_jsonable(Sample, {**GOOD, **change})
    assert str(exc.value).startswith(where)


def test_missing_fields_wrong_containers_and_refused_values() -> None:
    """Required fields, objects, and the dataclass's own checks.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(ContractError, match=r"^\$\.text: missing$"):
        from_jsonable(Reading, {"value": 1})
    with pytest.raises(ContractError, match=r"^\$: expected an object, got list$"):
        from_jsonable(Reading, [])
    with pytest.raises(ContractError, match=r"^\$\.bucket: expected a StatusBucket value"):
        from_jsonable(StatusView, {**wire(mixed_fleet().queries().device_list().rows[0].status),
                                   "bucket": True})  # fmt: skip
    with pytest.raises(ContractError, match=r"^\$: not a valid DeviceListRequest$"):
        from_jsonable(DeviceListRequest, {"limit": 0})
    with pytest.raises(ContractError, match=r"^\$\.text: expected str, got NoneType$"):
        from_jsonable(Reading, {"text": None, "value": None})
    with pytest.raises(ContractError, match=r"^\$\.value: null where a value is required$"):
        from_jsonable(Either, {"value": None})


def test_e29_depth_and_size_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    """E.29: at most ``MAX_DEPTH`` levels and ``MAX_NODES`` values per answer.

    Args:
        monkeypatch: Lowers the bounds.

    Returns:
        None

    Raises:
        None
    """
    deep: Any = 1
    for _ in range(module.MAX_DEPTH + 2):
        deep = [deep]
    nested = int
    for _ in range(module.MAX_DEPTH + 2):
        nested = list[nested]  # type: ignore[valid-type]
    with pytest.raises(ContractError, match="nested deeper than 32 levels"):
        from_jsonable(nested, deep)
    monkeypatch.setattr(module, "MAX_NODES", 50)
    with pytest.raises(ContractError, match="more than 50 values"):
        from_jsonable(DeviceListView, wire(mixed_fleet().queries().device_list()))


def test_unsupported_types_are_programming_errors() -> None:
    """A view field of a type the decoder does not know is a ``TypeError``, not a bad answer.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(TypeError, match="is not a type from_jsonable decodes"):
        from_jsonable(bytes, "x")
    with pytest.raises(TypeError, match="needs an item type"):
        from_jsonable(tuple[()], [])
    with pytest.raises(TypeError, match="is not a container"):
        from_jsonable(set[int], [])
