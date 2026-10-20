"""Telemetry-loss incidents: the explicit record that observations were lost (STORE-007, 6.4).

vakr never drops telemetry silently (invariant 8). When it cannot keep an
observation, whether from a power cut before the covering fsync, a damaged
segment, a full disk, or a record no release can decode, it writes an
incident naming the affected time range, observation classes, and an exact
or estimated count. Graph gaps in that range are then explained as
collector-side loss, never as the target being down (invariant 2).

Incidents live in ``incidents.jsonl`` in the collector's spool directory
(``vakr.ingest.incident_log`` writes it); this module defines the record
and reads the file back, for recovery's de-duplication and for health.

Dependencies:
    json, uuid: The JSON-lines form and incident IDs.

Safety:
    Incidents hold counts, sequence numbers, times, and file names, never
    observation payloads.

Example:
    >>> inc = LossIncident.new(LossKind.MISSING_RANGE, 1_000, first_seq=5, last_seq=9,
    ...                        estimated=5, exact=True, detail="segment missing")
    >>> LossIncident.from_dict(inc.to_dict()) == inc
    True
"""

import json
import uuid
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

LOG_FILE = "incidents.jsonl"
OLD_LOG_FILE = "incidents.1.jsonl"
MAX_LOG_BYTES = 1 << 20
"""Rotation size of the incident log (E.24); about 3,000 incidents."""


class LossKind(StrEnum):
    """Why observations were lost; stable values for logs and health."""

    UNCLEAN_STOP = "unclean_stop"
    TORN_TAIL = "torn_tail"
    DAMAGED_SEGMENT = "damaged_segment"
    BAD_HEADER = "bad_header"
    MISSING_RANGE = "missing_range"
    REJECTED_FULL = "rejected_full"
    WRITE_FAILED = "write_failed"
    UNDECODABLE = "undecodable"
    REJECTED_BY_STORAGE = "rejected_by_storage"


# Refusals happen once each; every other kind is a finding about data on
# disk, which a restart can find again and must not report twice.
_EVENTS = frozenset({LossKind.REJECTED_FULL, LossKind.WRITE_FAILED})


@dataclass(frozen=True, slots=True)
class LossIncident:
    """One telemetry-loss incident. Immutable.

    Attributes:
        incident_id: UUIDv7 of the incident.
        kind: Why the loss happened.
        detected_us: When vakr noticed, epoch microseconds (UTC).
        first_seq: First affected spool sequence number, or ``None`` if unknown.
        last_seq: Last affected sequence number, or ``None`` if unknown.
        start_us: Start of the affected time range, or ``None`` if unknown.
        end_us: End of the affected time range, or ``None`` if unknown.
        classes: Affected observation types, sorted; empty when unknown.
        estimated: Number of observations lost (an upper estimate unless exact).
        exact: Whether ``estimated`` is an exact count.
        segment: Spool file involved, or ``""``.
        detail: One operator-readable sentence.
    """

    incident_id: str
    kind: LossKind
    detected_us: int
    first_seq: int | None
    last_seq: int | None
    start_us: int | None
    end_us: int | None
    classes: tuple[str, ...]
    estimated: int
    exact: bool
    segment: str
    detail: str

    @classmethod
    def new(cls, kind: LossKind, detected_us: int, **fields: Any) -> LossIncident:
        """Create an incident with a fresh ID; unspecified fields are unknown.

        Args:
            kind: Why the loss happened.
            detected_us: Detection time in epoch microseconds.
            **fields: Any other attribute values.

        Returns:
            LossIncident: The incident.

        Raises:
            TypeError: If an unknown field is given.
        """
        base: dict[str, Any] = {
            "first_seq": None, "last_seq": None, "start_us": None, "end_us": None,
            "classes": (), "estimated": 0, "exact": False, "segment": "", "detail": "",
        }  # fmt: skip
        merged = base | fields
        merged["classes"] = tuple(sorted(set(merged["classes"])))
        return cls(str(uuid.uuid7()), kind, detected_us, **merged)

    @property
    def key(self) -> tuple[str, str, int | None] | None:
        """Identity used to record a finding on disk only once, however often it is seen.

        Args:
            None

        Returns:
            tuple[str, str, int | None] | None: Kind, segment, and first sequence
            number; ``None`` for a refused append, which is a new loss every time.

        Raises:
            None
        """
        if self.kind in _EVENTS:
            return None
        return (self.kind.value, self.segment, self.first_seq)

    def to_dict(self) -> dict[str, Any]:
        """Encode as a JSON-compatible dict.

        Args:
            None

        Returns:
            dict[str, Any]: Every attribute; ``classes`` as a list.

        Raises:
            None
        """
        return asdict(self) | {"kind": self.kind.value, "classes": list(self.classes)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LossIncident:
        """Decode the dict form.

        Args:
            data: Output of ``to_dict``.

        Returns:
            LossIncident: The incident.

        Raises:
            KeyError, TypeError, ValueError: If the dict is malformed.
        """
        fields: dict[str, Any] = dict(data)
        fields["kind"], fields["classes"] = LossKind(data["kind"]), tuple(data["classes"])
        return cls(**fields)


def read_incidents(directory: Path, since_us: int | None = None) -> list[LossIncident]:
    """Read recorded incidents, oldest first, skipping unreadable lines.

    Args:
        directory: The collector's spool directory.
        since_us: Keep only incidents detected at or after this time.

    Returns:
        list[LossIncident]: The incidents from both log generations.

    Raises:
        None
    """
    found: list[LossIncident] = []
    for name in (OLD_LOG_FILE, LOG_FILE):
        try:
            lines = (directory / name).read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                incident = LossIncident.from_dict(json.loads(line))
            except ValueError, KeyError, TypeError:
                continue  # a torn last line from a power cut is not worth failing over
            if since_us is None or incident.detected_us >= since_us:
                found.append(incident)
    return found
