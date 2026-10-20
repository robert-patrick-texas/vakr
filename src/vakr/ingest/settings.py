"""Spool and ingestion settings: the ``[spool]`` options as one checked value (E.8, E.24).

A collector's spool sits between polling and the telemetry database. How
often it fsyncs decides how much telemetry a power cut can cost, its size
limit decides how long a database outage it can ride out, and its batch
interval decides how fresh graphs are. Those numbers come from the
``[spool]`` table of the option catalog, which bounds each one;
``SpoolSettings`` adds the rules that span several options (thresholds in
order, room for at least four segments, never on tmpfs) so a service refuses
to start on a combination that cannot work.

Dependencies:
    vakr.config: The option catalog and the effective configuration.
    vakr.lifecycle.configmerge: Duration parsing.
    vakr.paths: The state directory that holds the default spool root.

Safety:
    Messages name option keys and limits, never other configuration values.

Example:
    >>> from pathlib import Path
    >>> s = SpoolSettings.defaults(Path("/var/lib/vakr/spool"))
    >>> s.fsync_interval_s, s.fsync_records, s.segment_bytes // MIB
    (1.0, 1000, 64)
"""

from dataclasses import dataclass, fields, replace
from pathlib import Path, PurePosixPath
from typing import Any

from vakr.config import project
from vakr.config.loader import EffectiveConfig
from vakr.domain.errors import ValidationError
from vakr.domain.ids import CollectorId
from vakr.lifecycle.configmerge import parse_duration
from vakr.paths import InstallPaths

MIB = 1 << 20
"""Bytes per MiB; spool sizes are configured in MiB."""

MIN_SEGMENTS = 4
"""The size limit must hold this many segments, so acknowledged ones can be freed."""

# tmpfs on every supported distribution: a spool there would vanish with the
# page cache on power loss, which STORE-005 forbids as the only durable copy.
_VOLATILE_ROOTS = (PurePosixPath("/dev/shm"), PurePosixPath("/run"))  # noqa: S108 - refused, not used

_DURATIONS = {"fsync_interval": "fsync_interval_s", "ingest_interval": "ingest_interval_s"}
_MIB_SIZES = {"max_size_mib": "max_bytes", "segment_size_mib": "segment_bytes"}
_INTS = ("fsync_records", "queue_capacity", "warning_pct", "critical_pct", "exhausted_pct",
         "ingest_max_rows")  # fmt: skip


@dataclass(frozen=True, slots=True)
class SpoolSettings:
    """Validated spool and ingestion settings for one node. Immutable.

    Attributes:
        root: Spool root; each collector uses ``root / <collector UUID>``.
        max_bytes: Upper bound on disk use, quarantined files included.
        segment_bytes: Segment size at which the writer rotates.
        fsync_interval_s: Longest wait, in seconds, for a covering fsync.
        fsync_records: Records after which the writer fsyncs early.
        queue_capacity: Observations that may wait in memory for durability.
        warning_pct: Fill percentage that starts ``WARNING``.
        critical_pct: Fill percentage that starts ``CRITICAL``.
        exhausted_pct: Fill percentage that starts ``EXHAUSTED``.
        ingest_interval_s: Seconds between telemetry writes when caught up.
        ingest_max_rows: Most observations per telemetry write.
    """

    root: Path
    max_bytes: int
    segment_bytes: int
    fsync_interval_s: float
    fsync_records: int
    queue_capacity: int
    warning_pct: int
    critical_pct: int
    exhausted_pct: int
    ingest_interval_s: float
    ingest_max_rows: int

    def __post_init__(self) -> None:
        """Check the rules that span options; the catalog bounds each one alone.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If the settings cannot work together.
        """
        if problems := _problems(self):
            raise ValidationError("; ".join(problems), field="spool")

    def collector_dir(self, collector_id: CollectorId) -> Path:
        """Return the spool directory of one collector (6.2: one producer, one consumer).

        Args:
            collector_id: The collector that owns the spool.

        Returns:
            Path: ``root / <collector UUID>``.

        Raises:
            None
        """
        return self.root / str(collector_id)

    @classmethod
    def from_config(cls, config: EffectiveConfig, paths: InstallPaths) -> SpoolSettings:
        """Build settings from the effective configuration.

        Args:
            config: Merged configuration; missing keys take catalog defaults.
            paths: Installation layout, for the default spool root.

        Returns:
            SpoolSettings: The checked settings.

        Raises:
            ValidationError: If a value is malformed or the combination is invalid.
        """
        return cls(**_values(lambda key: config.get(f"spool.{key}"), paths))

    @classmethod
    def defaults(cls, root: Path) -> SpoolSettings:
        """Return the packaged defaults with a chosen spool root, for tools and tests.

        Args:
            root: Spool root directory.

        Returns:
            SpoolSettings: Catalog defaults under ``root``.

        Raises:
            ValidationError: If ``root`` is on a volatile filesystem.
        """
        values = _values(lambda _key: None, InstallPaths.under(Path("/")))
        values["root"] = root
        return cls(**values)

    def with_values(self, **changes: Any) -> SpoolSettings:
        """Return a copy with some fields replaced, re-validated.

        Args:
            **changes: Field names and new values.

        Returns:
            SpoolSettings: The new settings.

        Raises:
            ValidationError: If the result is invalid.
        """
        return replace(self, **changes)


def _values(lookup: Any, paths: InstallPaths) -> dict[str, Any]:
    """Read every ``[spool]`` option, falling back to the catalog default.

    Args:
        lookup: Returns the configured value of a key inside ``[spool]``, or ``None``.
        paths: Installation layout, for the default root.

    Returns:
        dict[str, Any]: Constructor arguments for ``SpoolSettings``.

    Raises:
        ValidationError: If a duration or number is malformed.
    """

    def raw(key: str) -> Any:
        """Return the configured or default value of one option.

        Args:
            key: Key inside the ``[spool]`` table.

        Returns:
            Any: The value.

        Raises:
            None
        """
        value = lookup(key)
        if value is not None:
            return value
        option = project.catalog().option(f"spool.{key}")
        return None if option is None else option.default

    directory = raw("directory")
    values: dict[str, Any] = {
        "root": Path(directory) if directory else paths.state_dir / "spool",
    }
    try:
        values |= {name: parse_duration(raw(key)) for key, name in _DURATIONS.items()}
    except ValueError as exc:
        raise ValidationError(f"spool durations: {exc}", field="spool") from None
    values |= {name: _int(raw(key), key) * MIB for key, name in _MIB_SIZES.items()}
    values |= {key: _int(raw(key), key) for key in _INTS}
    return values


def _int(value: object, key: str) -> int:
    """Require an integer option value.

    Args:
        value: The configured value.
        key: Key inside ``[spool]``, for the message.

    Returns:
        int: The value.

    Raises:
        ValidationError: If ``value`` is not an integer.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"spool.{key} must be an integer", field=f"spool.{key}")
    return value


def _problems(s: SpoolSettings) -> list[str]:
    """List every cross-option rule the settings break.

    Args:
        s: The settings.

    Returns:
        list[str]: Operator-readable problems naming option keys.

    Raises:
        None
    """
    found: list[str] = []
    numbers = [f.name for f in fields(s) if f.name != "root"]
    if any(not getattr(s, name) > 0 for name in numbers):
        found.append("every spool size, interval, and count must be positive")
    if not 0 < s.warning_pct < s.critical_pct < s.exhausted_pct < 100:
        found.append("spool.warning_pct < spool.critical_pct < spool.exhausted_pct < 100 must hold")
    if s.max_bytes < MIN_SEGMENTS * s.segment_bytes:
        found.append(
            f"spool.max_size_mib must hold at least {MIN_SEGMENTS} segments of "
            "spool.segment_size_mib"
        )
    root = PurePosixPath(s.root)
    if not root.is_absolute():
        found.append("spool.directory must be an absolute path")
    elif any(root == v or v in root.parents for v in _VOLATILE_ROOTS):
        found.append("spool.directory must not be on tmpfs (/dev/shm, /run): STORE-005")
    return found


def spool_problems(config: EffectiveConfig) -> list[str]:
    """Return the cross-option ``[spool]`` problems, for ``vakr config validate``.

    Args:
        config: Effective configuration to check.

    Returns:
        list[str]: Problems; empty when the settings work together.

    Raises:
        None
    """
    try:
        SpoolSettings.from_config(config, InstallPaths.from_environment())
    except ValidationError as exc:
        return [f"spool: {exc}"]
    return []
