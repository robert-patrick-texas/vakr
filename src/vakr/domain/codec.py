"""Plain JSON-compatible dicts for every observation type, and back (ARCH-002).

The codec is the schema of observation payloads, written out field by field
rather than derived from the Python classes, so a class change cannot alter
the wire format by accident and a Go implementation can follow it line by
line (blueprint 24). Wire rules:

- UUIDs are canonical lower-case hyphenated strings;
- timestamps are integer microseconds since the UTC epoch, and their keys
  end in ``_us`` (``timestamp_us``, ``started_at_us``);
- enums are their stored values: strings for state enums, integers for the
  IF-MIB status enums; ``Quality`` is its integer bit set;
- ICMP RTTs are an array of integers with ``null`` for no reply;
- a missing value is ``null``; every key is always present.

Every observation has the header keys ``observation_id``, ``collector_id``,
``poll_id`` (``null`` for collector health), ``timestamp_us`` and
``quality``. A ``DevicePoll``'s ``timestamp_us`` is its scheduled time.

Dependencies:
    vakr.domain.observations: The observation classes.
    vakr.domain.codec_fields, codec_values: Per-type field lists and converters.

Safety:
    Decoding is strict: unknown or missing keys, wrong JSON types, and
    out-of-range values raise ``ValidationError`` instead of being guessed.

Example:
    >>> from datetime import UTC, datetime
    >>> from vakr.domain.ids import CollectorId, ObservationId, new_id
    >>> from vakr.domain.enums import CollectorState
    >>> from vakr.domain.observations import CollectorHealthObservation
    >>> h = CollectorHealthObservation(new_id(ObservationId), new_id(CollectorId),
    ...     datetime(2026, 10, 20, tzinfo=UTC), CollectorState.HEALTHY, "0.0.0")
    >>> observation_from_dict(observation_to_dict(h)) == h
    True
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import UUID

from vakr.domain.codec_fields import SPECS
from vakr.domain.codec_values import decode_value, encode_value
from vakr.domain.enums import ObservationType
from vakr.domain.errors import ValidationError
from vakr.domain.ids import parse_uuid
from vakr.domain.json_values import JsonValue
from vakr.domain.observations import Observation, observation_type
from vakr.domain.quality import Quality
from vakr.domain.timestamps import from_epoch_us, to_epoch_us

HEADER_KEYS = ("observation_id", "collector_id", "poll_id", "timestamp_us", "quality")
"""Keys every observation dict carries besides its payload."""

MAX_QUALITY = (1 << 63) - 1  # quality is stored as a signed 64-bit integer


def encode_payload(observation: Observation) -> dict[str, JsonValue]:
    """Encode the type-specific fields of an observation.

    Args:
        observation: Any observation.

    Returns:
        dict[str, JsonValue]: Payload keys and wire values; header fields excluded.

    Raises:
        ValidationError: If ``observation`` is not an observation type.
    """
    spec = SPECS[observation_type(observation)]
    return {f.key: encode_value(f, getattr(observation, f.attr)) for f in spec.payload}


def decode_observation(
    kind: ObservationType,
    *,
    observation_id: UUID,
    collector_id: UUID,
    poll_id: UUID | None,
    timestamp: datetime,
    quality: Quality,
    payload: Mapping[str, Any],
) -> Observation:
    """Rebuild an observation from its decoded header and its wire payload.

    Args:
        kind: Observation type.
        observation_id: Header observation UUID.
        collector_id: Header collector UUID.
        poll_id: Header poll UUID; must be ``None`` exactly for collector health.
        timestamp: Header timestamp (a poll's scheduled time).
        quality: Header quality flags.
        payload: Type-specific wire fields.

    Returns:
        Observation: The rebuilt, validated observation.

    Raises:
        ValidationError: If keys are missing or unknown, a value has the wrong
            JSON type, ``poll_id`` presence does not match ``kind``, or the
            rebuilt record breaks a domain invariant.
    """
    spec = SPECS[kind]
    _require_keys(payload, {f.key for f in spec.payload}, f"{kind} payload")
    kwargs: dict[str, Any] = {f.attr: decode_value(f, payload[f.key]) for f in spec.payload}
    if spec.has_poll != (poll_id is not None):
        state = "requires" if spec.has_poll else "must not have"
        raise ValidationError(f"a {kind} observation {state} a poll_id", field="poll_id")
    if spec.has_poll:
        kwargs["poll_id"] = poll_id
    kwargs[spec.timestamp_attr] = timestamp
    return spec.cls(
        observation_id=observation_id, collector_id=collector_id, quality=quality, **kwargs
    )


def observation_to_dict(observation: Observation) -> dict[str, JsonValue]:
    """Encode an observation as one flat JSON-compatible dict.

    Args:
        observation: Any observation.

    Returns:
        dict[str, JsonValue]: ``observation_type``, the header keys, and the payload keys.

    Raises:
        ValidationError: If ``observation`` is not an observation type.
    """
    kind = observation_type(observation).value
    return {"observation_type": kind, **encode_header(observation), **encode_payload(observation)}


def observation_from_dict(data: Mapping[str, Any]) -> Observation:
    """Decode a flat dict produced by ``observation_to_dict``.

    Args:
        data: The flat dict.

    Returns:
        Observation: The rebuilt observation, equal to the one encoded.

    Raises:
        ValidationError: If the dict is malformed or breaks a domain invariant.
    """
    kind = parse_observation_type(data.get("observation_type"))
    header = {k: data[k] for k in HEADER_KEYS if k in data}
    _require_keys(header, set(HEADER_KEYS), "observation header")
    payload = {k: v for k, v in data.items() if k not in HEADER_KEYS and k != "observation_type"}
    return decode_observation(kind, **decode_header(header), payload=payload)


def parse_observation_type(value: object) -> ObservationType:
    """Parse the ``observation_type`` wire value.

    Args:
        value: Parsed JSON value.

    Returns:
        ObservationType: The type.

    Raises:
        ValidationError: If ``value`` is not a known observation type.
    """
    try:
        return ObservationType(value)
    except ValueError:
        raise ValidationError(
            f"unknown observation_type {value!r}", field="observation_type"
        ) from None


def encode_header(observation: Observation) -> dict[str, JsonValue]:
    """Encode the header fields every observation shares.

    Args:
        observation: Any observation.

    Returns:
        dict[str, JsonValue]: The ``HEADER_KEYS`` and their wire values.

    Raises:
        None
    """
    poll_id = getattr(observation, "poll_id", None)
    return {
        "observation_id": str(observation.observation_id),
        "collector_id": str(observation.collector_id),
        "poll_id": None if poll_id is None else str(poll_id),
        "timestamp_us": to_epoch_us(observation.timestamp),
        "quality": int(observation.quality),
    }


def decode_header(header: Mapping[str, Any]) -> dict[str, Any]:
    """Decode the shared header keys into keyword arguments for ``decode_observation``.

    Args:
        header: A mapping holding exactly ``HEADER_KEYS``.

    Returns:
        dict[str, Any]: ``observation_id``, ``collector_id``, ``poll_id``,
        ``timestamp``, and ``quality`` as domain values.

    Raises:
        ValidationError: If a value has the wrong JSON type or range.
    """
    poll = header["poll_id"]
    return {
        "observation_id": parse_uuid(header["observation_id"], "observation_id"),
        "collector_id": parse_uuid(header["collector_id"], "collector_id"),
        "poll_id": None if poll is None else parse_uuid(poll, "poll_id"),
        "timestamp": from_epoch_us(header["timestamp_us"]),
        "quality": decode_quality(header["quality"]),
    }


def decode_quality(value: object) -> Quality:
    """Decode a quality bit set, keeping bits this version does not know yet.

    Unknown bits come from a newer producer (positions are append-only) and
    are evidence, so they are preserved rather than dropped.

    Args:
        value: Wire value.

    Returns:
        Quality: The flags.

    Raises:
        ValidationError: If ``value`` is not an integer from 0 to 2^63 - 1.
    """
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_QUALITY:
        raise ValidationError("quality must be an integer from 0 to 2^63-1", field="quality")
    return Quality(value)


def _require_keys(data: Mapping[str, Any], expected: set[str], what: str) -> None:
    """Require exactly the expected keys, naming any that are missing or unknown.

    Args:
        data: Mapping to check.
        expected: Keys that must be present, and the only ones allowed.
        what: Description for the error message.

    Returns:
        None

    Raises:
        ValidationError: If keys are missing or unknown.
    """
    missing, unknown = expected - data.keys(), data.keys() - expected
    if missing or unknown:
        raise ValidationError(
            f"{what}: missing keys {sorted(missing)}, unknown keys {sorted(unknown)}"
        )
