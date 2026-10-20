"""Envelope, codec, canonical JSON, and timestamp tests (ARCH-002, 6.3, blueprint 24, TIME-001).

The golden tests are the cross-language contract: a future Go codec must
reproduce ``tests/fixtures/observations/*.json`` byte for byte too.

Dependencies:
    pytest; vakr.domain; tests.unit.domain.golden_observations.

Example:
    $ uv run pytest tests/unit/domain/test_domain_envelope.py
"""

import dataclasses
import json
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest

from tests.unit.domain.golden_observations import FIXTURE_DIR, GOLDEN, GOLDEN_BATCH, golden_bytes
from vakr.domain.codec import (
    HEADER_KEYS,
    decode_quality,
    observation_from_dict,
    observation_to_dict,
)
from vakr.domain.codec_fields import SPECS
from vakr.domain.enums import ObservationType
from vakr.domain.envelope import (
    envelope_from_bytes,
    envelope_from_dict,
    envelope_to_bytes,
    envelope_to_dict,
    open_envelope,
    seal,
)
from vakr.domain.errors import ValidationError
from vakr.domain.json_values import MAX_JSON_DEPTH, canonical_json, parse_json_object, require_json
from vakr.domain.observations import observation_type
from vakr.domain.quality import Quality
from vakr.domain.timestamps import from_epoch_us, is_utc, require_utc, to_epoch_us


@pytest.mark.parametrize("name", sorted(GOLDEN))
def test_golden_fixtures_are_reproduced_byte_for_byte(name: str) -> None:
    """ARCH-002, blueprint 24: sealing the golden observation yields the fixture bytes exactly.

    Args:
        name: Fixture name.

    Returns:
        None

    Raises:
        None
    """
    on_disk = (FIXTURE_DIR / f"{name}.json").read_bytes()
    assert golden_bytes(name) == on_disk
    envelope = envelope_from_bytes(on_disk)
    assert open_envelope(envelope) == GOLDEN[name]
    assert envelope.batch_id == GOLDEN_BATCH
    assert envelope_to_bytes(envelope) == on_disk


def test_golden_set_covers_every_type_and_nothing_else() -> None:
    """Blueprint 24: one or more fixtures per type, and no orphaned files.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert {observation_type(o) for o in GOLDEN.values()} == set(ObservationType)
    assert {p.stem for p in FIXTURE_DIR.glob("*.json")} == set(GOLDEN)
    assert not [p for p in FIXTURE_DIR.iterdir() if p.suffix != ".json"]


def test_codec_schema_covers_every_dataclass_field() -> None:
    """ARCH-002: adding a field to an observation class without a codec entry fails here.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    for spec in SPECS.values():
        header = {"observation_id", "collector_id", "quality", spec.timestamp_attr}
        if spec.has_poll:
            header.add("poll_id")
        declared = {f.name for f in dataclasses.fields(cast(Any, spec.cls))}
        assert declared == {f.attr for f in spec.payload} | header


@pytest.mark.parametrize("name", sorted(GOLDEN))
def test_flat_dicts_round_trip_exactly(name: str) -> None:
    """ARCH-002: every type survives ``observation_to_dict`` and JSON exactly.

    Args:
        name: Golden observation name.

    Returns:
        None

    Raises:
        None
    """
    flat = observation_to_dict(GOLDEN[name])
    assert set(HEADER_KEYS) <= flat.keys()
    assert observation_from_dict(json.loads(json.dumps(flat))) == GOLDEN[name]


def _wire(name: str = "icmp_impaired") -> dict[str, Any]:
    """Return a mutable parsed copy of a golden envelope.

    Args:
        name: Golden fixture name.

    Returns:
        dict[str, Any]: The parsed envelope.

    Raises:
        None
    """
    return json.loads(golden_bytes(name))


def test_checksum_catches_tampering_anywhere() -> None:
    """6.3: a changed payload or header byte is detected; the field is named.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    payload_edit, header_edit = _wire(), _wire()
    payload_edit["payload"]["rtts_us"][1] = 5
    header_edit["timestamp_us"] += 1
    for data in (payload_edit, header_edit):
        with pytest.raises(ValidationError) as caught:
            envelope_from_dict(data)
        assert caught.value.field == "checksum"


@pytest.mark.parametrize("version", [2, 0, True, "1", None])
def test_unsupported_schema_versions_are_refused(version: object) -> None:
    """MIG-004 spirit: a future or malformed schema version gets an actionable error.

    Args:
        version: The bad version.

    Returns:
        None

    Raises:
        None
    """
    data = _wire()
    data["schema_version"] = version
    with pytest.raises(ValidationError, match="Upgrade vakr"):
        envelope_from_dict(data)


def _resealed(data: dict[str, Any]) -> dict[str, Any]:
    """Recompute the checksum of an edited wire dict so only its content is wrong.

    Args:
        data: Edited envelope dict.

    Returns:
        dict[str, Any]: The dict with a matching checksum.

    Raises:
        None
    """
    import hashlib

    unsigned = {k: v for k, v in data.items() if k != "checksum"}
    return data | {"checksum": "sha256:" + hashlib.sha256(canonical_json(unsigned)).hexdigest()}


@pytest.mark.parametrize(
    ("name", "path", "value"),
    [
        ("icmp_impaired", ("payload", "rtts_us"), "fast"),
        ("icmp_impaired", ("payload", "rtts_us"), [1, True, 3, 4, 5]),
        ("icmp_impaired", ("payload", "state"), "sleepy"),
        ("icmp_impaired", ("payload", "state"), 1),
        ("icmp_impaired", ("payload", "target_address"), 7),
        ("icmp_impaired", ("payload", "device_id"), None),
        ("icmp_impaired", ("payload", "device_id"), "not-a-uuid"),
        ("icmp_impaired", ("payload", "min_rtt_us"), 1.5),
        ("interface_valid_rates", ("payload", "in_bps"), "fast"),
        ("interface_valid_rates", ("payload", "in_bps"), True),
        ("interface_valid_rates", ("payload", "admin_status"), "up"),
        ("interface_valid_rates", ("payload", "in_bps"), 10**400),
        ("collector_health", ("poll_id",), "01929b4a-0000-7000-8000-00000000f001"),
        ("icmp_impaired", ("poll_id",), None),
        ("icmp_impaired", ("quality",), -1),
        ("icmp_impaired", ("quality",), 1 << 63),
        ("icmp_impaired", ("observation_type",), "telepathy"),
        ("icmp_impaired", ("payload", "surprise"), 1),
        ("icmp_impaired", ("timestamp_us",), "noon"),
        ("icmp_impaired", ("batch_id",), "x"),
    ],
)
def test_decoding_is_strict(name: str, path: tuple[str, ...], value: object) -> None:
    """6.3: wrong JSON types, unknown keys, and impossible headers raise ``ValidationError``.

    Args:
        name: Golden fixture to start from.
        path: Key path to replace.
        value: Replacement value.

    Returns:
        None

    Raises:
        None
    """
    data = _wire(name)
    target = data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValidationError):
        open_envelope(envelope_from_dict(_resealed(data)))


def test_missing_and_extra_envelope_keys() -> None:
    """6.3: the envelope has exactly its ten keys.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    data = _wire()
    with pytest.raises(ValidationError):
        envelope_from_dict({k: v for k, v in data.items() if k != "batch_id"})
    with pytest.raises(ValidationError):
        envelope_from_dict(data | {"extra": 1})
    flat = observation_to_dict(GOLDEN["icmp_impaired"])
    with pytest.raises(ValidationError):
        observation_from_dict({k: v for k, v in flat.items() if k != "quality"})
    with pytest.raises(ValidationError):
        observation_from_dict(flat | {"observation_type": "nope"})
    with pytest.raises(ValidationError):
        envelope_from_bytes(b"[1, 2]")
    with pytest.raises(ValidationError):
        envelope_from_bytes(b"\xff\xfe")


def test_envelope_direct_construction_is_validated() -> None:
    """6.3: an envelope object in memory is always intact and well-typed.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    env = seal(GOLDEN["collector_health"], GOLDEN_BATCH)
    assert envelope_to_dict(env)["poll_id"] is None
    for change in ({"checksum": "sha256:00"}, {"payload": [1]}, {"quality": True}):
        with pytest.raises(ValidationError):
            dataclasses.replace(env, **change)
    with pytest.raises(ValidationError):
        seal(cast(Any, object()), GOLDEN_BATCH)
    assert decode_quality(1 << 40) & (1 << 40)  # future bits are kept, not dropped


def test_canonical_json_rules() -> None:
    """ARCH-002: sorted keys, compact separators, raw UTF-8, shortest floats, no NaN or tuples.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert canonical_json({"b": 1.0, "a": [1e16, 1e-05, 0.0001, 2**70], "é": None}) == (
        '{"a":[1e+16,1e-05,0.0001,1180591620717411303424],"b":1.0,"é":null}'.encode()
    )
    deep: list[Any] = []
    for _ in range(MAX_JSON_DEPTH + 1):
        deep = [deep]
    for bad in ({"a": float("nan")}, {"a": (1, 2)}, {1: "x"}, deep, {"a": "\ud800"}):
        with pytest.raises(ValidationError):
            canonical_json(cast(Any, bad))
    with pytest.raises(ValidationError):
        parse_json_object(b'{"a": NaN}')
    with pytest.raises(ValidationError):
        require_json({1, 2}, "value")


def test_timestamps_are_utc_and_exact() -> None:
    """Invariant 11, TIME-001: only aware UTC is accepted; microseconds round-trip exactly.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    moment = datetime(2026, 10, 1, 12, 0, 0, 123456, tzinfo=UTC)
    assert from_epoch_us(to_epoch_us(moment)) == moment
    assert to_epoch_us(datetime(1969, 12, 31, 23, 59, 59, 999999, tzinfo=UTC)) == -1
    assert is_utc(moment.astimezone(ZoneInfo("UTC")))
    assert is_utc(moment.astimezone(timezone(timedelta(0))))
    london_winter = datetime(2026, 1, 1, tzinfo=ZoneInfo("Europe/London"))
    for bad in (
        datetime(2026, 1, 1),
        london_winter,
        moment.astimezone(timezone(timedelta(hours=2))),
        "now",
    ):
        with pytest.raises(ValidationError):
            require_utc(bad, "t")
    for bad_us in (True, 1.5, 10**20):
        with pytest.raises(ValidationError):
            from_epoch_us(cast(Any, bad_us))
    assert Quality(int(Quality.PARTIAL_LOSS | Quality.RETRIED)) == 4194305
