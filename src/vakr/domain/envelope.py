"""The versioned, language-neutral observation envelope (blueprint 6.3, ARCH-002).

Observations cross process boundaries (collector to ingester, spool to
database, later a Go collector to a Python ingester) inside an envelope: a
fixed header, a type-specific payload, and a checksum. The envelope is
defined by its JSON form (keys exactly ``ENVELOPE_KEYS``; see
``docs/architecture/domain.md``), not by Python classes. ``checksum`` is
SHA-256 over the canonical JSON bytes of the envelope object without its
``checksum`` key, so a flipped bit in the header is caught as surely as one
in the payload. The M3 spool stores the same envelope inside
length-delimited MessagePack records; the JSON form is the reference.

Dependencies:
    hashlib: SHA-256.
    vakr.domain.codec, json_values: Payload encoding and canonical bytes.

Safety:
    Construction verifies the checksum, so an ``ObservationEnvelope`` in
    memory is always intact. Treat ``payload`` as read-only.

Example:
    >>> from datetime import UTC, datetime
    >>> from vakr.domain.enums import CollectorState
    >>> from vakr.domain.ids import BatchId, CollectorId, ObservationId, new_id
    >>> from vakr.domain.observations import CollectorHealthObservation
    >>> h = CollectorHealthObservation(new_id(ObservationId), new_id(CollectorId),
    ...     datetime(2026, 10, 20, tzinfo=UTC), CollectorState.HEALTHY, "0.0.0")
    >>> env = seal(h, new_id(BatchId))
    >>> open_envelope(envelope_from_bytes(envelope_to_bytes(env))) == h
    True
"""

import dataclasses
import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from vakr.domain import codec, json_values
from vakr.domain.checks import require_enum, require_int, require_uuid
from vakr.domain.enums import ObservationType
from vakr.domain.errors import ValidationError
from vakr.domain.ids import BatchId, CollectorId, ObservationId, PollId, parse_id
from vakr.domain.json_values import JsonValue, canonical_json, parse_json_object
from vakr.domain.observations import Observation, observation_type
from vakr.domain.timestamps import require_utc, to_epoch_us

SCHEMA_VERSION = 1
"""The envelope and payload schema version this code reads and writes."""

CHECKSUM_PREFIX = "sha256:"

ENVELOPE_KEYS = frozenset({
    "schema_version", "observation_type", "observation_id", "batch_id", "collector_id",
    "poll_id", "timestamp_us", "payload", "quality", "checksum",
})  # fmt: skip


@dataclass(frozen=True, slots=True)
class ObservationEnvelope:
    """One observation in its cross-process form (6.3).

    Attributes:
        schema_version: Envelope and payload schema version (``1``).
        observation_type: Which payload schema applies.
        observation_id: Observation UUID, the deduplication key (STORE-009).
        batch_id: UUID of the batch the observation was written in.
        collector_id: Collector that made the observation.
        poll_id: ``DevicePoll`` UUID, or ``None`` for collector health.
        timestamp: Observation time (a poll's scheduled time), UTC.
        payload: Type-specific wire fields.
        quality: Quality flags as an integer bit set.
        checksum: ``sha256:`` plus the hex digest described above.
    """

    schema_version: int
    observation_type: ObservationType
    observation_id: ObservationId
    batch_id: BatchId
    collector_id: CollectorId
    poll_id: PollId | None
    timestamp: datetime
    payload: Mapping[str, Any]
    quality: int
    checksum: str

    def __post_init__(self) -> None:
        """Validate the header and verify the checksum.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If the schema version is unsupported, a header
                field is malformed, or the checksum does not match.
        """
        _require_schema(self.schema_version)
        require_enum(self.observation_type, ObservationType, "observation_type")
        for name in ("observation_id", "batch_id", "collector_id"):
            require_uuid(getattr(self, name), name)
        require_uuid(self.poll_id, "poll_id", optional=True)
        require_utc(self.timestamp, "timestamp")
        require_int(self.quality, "quality", minimum=0, maximum=(1 << 63) - 1)
        json_values.require_json_object(self.payload, "payload")
        if self.checksum != _checksum(_unsigned(dataclasses.asdict(self))):
            raise ValidationError("envelope checksum does not match its content", field="checksum")


def _unsigned(fields: Mapping[str, Any]) -> dict[str, JsonValue]:
    """Build the wire dict without ``checksum`` from envelope field values.

    Args:
        fields: Envelope field values by attribute name; ``checksum`` is ignored.

    Returns:
        dict[str, JsonValue]: Every envelope key except ``checksum``.

    Raises:
        None
    """
    poll = fields["poll_id"]
    return {
        "schema_version": fields["schema_version"],
        "observation_type": fields["observation_type"].value,
        "observation_id": str(fields["observation_id"]),
        "batch_id": str(fields["batch_id"]),
        "collector_id": str(fields["collector_id"]),
        "poll_id": None if poll is None else str(poll),
        "timestamp_us": to_epoch_us(fields["timestamp"]),
        "payload": dict(fields["payload"]),
        "quality": fields["quality"],
    }


def _require_schema(version: object) -> None:
    """Refuse any schema version but ``SCHEMA_VERSION``, with an actionable message.

    Args:
        version: The envelope's ``schema_version`` value.

    Returns:
        None

    Raises:
        ValidationError: If ``version`` is not the integer ``SCHEMA_VERSION``.
    """
    if isinstance(version, bool) or version != SCHEMA_VERSION:
        raise ValidationError(
            f"envelope schema version {version!r} is not supported; this vakr reads "
            f"version {SCHEMA_VERSION}. Upgrade vakr to read records from a newer producer",
            field="schema_version",
        )


def _checksum(unsigned: dict[str, JsonValue]) -> str:
    """Compute the envelope checksum.

    Args:
        unsigned: The wire dict without ``checksum``.

    Returns:
        str: ``sha256:`` plus 64 lower-case hex digits.

    Raises:
        ValidationError: If the dict is not JSON-compatible.
    """
    return CHECKSUM_PREFIX + hashlib.sha256(canonical_json(unsigned)).hexdigest()


def seal(observation: Observation, batch_id: BatchId) -> ObservationEnvelope:
    """Wrap an observation in an envelope with a fresh checksum.

    Args:
        observation: Any observation.
        batch_id: The batch it is being written in.

    Returns:
        ObservationEnvelope: The sealed envelope.

    Raises:
        ValidationError: If ``observation`` is not an observation type.
    """
    fields: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "observation_type": observation_type(observation),
        "observation_id": observation.observation_id,
        "batch_id": batch_id,
        "collector_id": observation.collector_id,
        "poll_id": getattr(observation, "poll_id", None),
        "timestamp": observation.timestamp,
        "payload": codec.encode_payload(observation),
        "quality": int(observation.quality),
    }
    return ObservationEnvelope(**fields, checksum=_checksum(_unsigned(fields)))


def open_envelope(envelope: ObservationEnvelope) -> Observation:
    """Rebuild the observation an envelope carries.

    Args:
        envelope: A verified envelope.

    Returns:
        Observation: The observation, equal to the one sealed.

    Raises:
        ValidationError: If the payload does not match its type's schema.
    """
    return codec.decode_observation(
        envelope.observation_type,
        observation_id=envelope.observation_id,
        collector_id=envelope.collector_id,
        poll_id=envelope.poll_id,
        timestamp=envelope.timestamp,
        quality=codec.decode_quality(envelope.quality),
        payload=envelope.payload,
    )


def envelope_to_dict(envelope: ObservationEnvelope) -> dict[str, JsonValue]:
    """Encode an envelope as its JSON-compatible wire dict.

    Args:
        envelope: The envelope.

    Returns:
        dict[str, JsonValue]: Every envelope key, checksum included.

    Raises:
        None
    """
    return {**_unsigned(dataclasses.asdict(envelope)), "checksum": envelope.checksum}


def envelope_from_dict(data: Mapping[str, Any]) -> ObservationEnvelope:
    """Decode and verify a wire dict.

    Args:
        data: Parsed JSON object.

    Returns:
        ObservationEnvelope: The verified envelope.

    Raises:
        ValidationError: If keys are missing or unknown, a value has the
            wrong type, the schema version is unsupported, or the checksum
            does not match.
    """
    _require_schema(data.get("schema_version"))
    if set(data) != ENVELOPE_KEYS:
        raise ValidationError(f"envelope keys must be exactly {sorted(ENVELOPE_KEYS)}")
    kind = codec.parse_observation_type(data["observation_type"])
    header = codec.decode_header(data)
    return ObservationEnvelope(
        schema_version=data["schema_version"],
        observation_type=kind,
        observation_id=header["observation_id"],
        batch_id=parse_id(BatchId, data["batch_id"]),
        collector_id=header["collector_id"],
        poll_id=header["poll_id"],
        timestamp=header["timestamp"],
        payload=data["payload"],
        quality=int(header["quality"]),
        checksum=data["checksum"],
    )


def envelope_to_bytes(envelope: ObservationEnvelope) -> bytes:
    """Encode an envelope as canonical JSON bytes, the golden-fixture form.

    Args:
        envelope: The envelope.

    Returns:
        bytes: Canonical UTF-8 JSON with no trailing newline.

    Raises:
        None
    """
    return canonical_json(envelope_to_dict(envelope))


def envelope_from_bytes(data: bytes) -> ObservationEnvelope:
    """Parse and verify canonical (or any valid) JSON bytes.

    Args:
        data: UTF-8 JSON holding one envelope object.

    Returns:
        ObservationEnvelope: The verified envelope.

    Raises:
        ValidationError: If the bytes are not a valid, intact envelope.
    """
    return envelope_from_dict(parse_json_object(data))
