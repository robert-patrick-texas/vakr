"""Self-health check for the durable spool and ingestion (OPS-001, OBS-007, 6.4, STORE-007).

``vakr check health`` runs every minute from CheckMK or Nagios, so this
check is read-only: it inspects each collector's spool directory under
``spool.directory`` without taking the collector's lock. Results:

- OK: no state directory (not an installed node), no spool yet, or a
  spool within its limits whose ingestion keeps up;
- WARNING: fill level at ``spool.warning_pct``; the telemetry database is
  refusing writes (polling continues, the spool absorbs it); ingestion is
  behind (oldest unacknowledged record older than six ingest intervals);
  a telemetry-loss incident in the last 24 hours; files in ``quarantine/``;
  a collector that stopped reporting without a clean stop; or a filesystem
  with less free space than the spool may still use;
- CRITICAL: fill level at ``spool.critical_pct`` or above (at
  ``spool.exhausted_pct`` normal polling is paused), or invalid ``[spool]``
  settings that stop the collector from starting.

Storage trouble is reported as the collector's condition, never as targets
being down (invariant 2).

Dependencies:
    vakr.ingest: Settings and the read-only inspection.
    vakr.config.loader, vakr.paths: The configuration and state directory.
    vakr.health.model: Check result types.

Safety:
    Read-only. Summaries carry sizes, ages, counts, and file names only.

Example:
    $ vakr check health
    VAKR OK - ...; spool: 1 MiB (0.0%) used; nothing waiting | spool_bytes=1048576B ...
"""

from dataclasses import dataclass

from vakr.config.loader import load_effective_config
from vakr.domain.errors import ValidationError
from vakr.domain.timestamps import to_epoch_us
from vakr.health.model import CheckResult, PerfDatum, Status
from vakr.ingest.capacity import CapacityState
from vakr.ingest.inspect import SpoolInspection, inspect_spool
from vakr.ingest.settings import MIB, SpoolSettings
from vakr.paths import InstallPaths
from vakr.ports.clock import SystemClock

NAME = "spool"
BEHIND_INTERVALS = 6
"""Oldest unacknowledged record older than this many ingest intervals is WARNING (E.24)."""

STALE_INTERVALS = 3
STALE_FLOOR_S = 60.0
"""``status.json`` older than max(3 ingest intervals, 60 s) means the collector is not reporting."""

_CAPACITY = {
    CapacityState.NORMAL: Status.OK, CapacityState.WARNING: Status.WARNING,
    CapacityState.CRITICAL: Status.CRITICAL, CapacityState.EXHAUSTED: Status.CRITICAL,
}  # fmt: skip


@dataclass(frozen=True, slots=True)
class Finding:
    """One spool's evaluated health. Immutable.

    Attributes:
        status: Severity.
        summary: Operator sentence.
        inspection: What was inspected.
    """

    status: Status
    summary: str
    inspection: SpoolInspection


def check_spool() -> CheckResult:
    """Check every collector spool on this node.

    Args:
        None

    Returns:
        CheckResult: As the module docstring describes, with bytes, fill
        level, oldest unacknowledged age, and recent incidents as perfdata.

    Raises:
        None
    """
    paths = InstallPaths.from_environment()
    if not paths.state_dir.exists():
        return CheckResult(NAME, Status.OK, f"no {paths.state_dir}; not an installed vakr node")
    try:
        settings = SpoolSettings.from_config(load_effective_config(paths.config_dir, strict=False),
                                             paths)  # fmt: skip
    except ValidationError as exc:
        return CheckResult(NAME, Status.CRITICAL, f"invalid [spool] settings: {exc}")
    except (OSError, ValueError) as exc:
        return CheckResult(NAME, Status.UNKNOWN, f"cannot read configuration: {type(exc).__name__}")
    root = settings.root
    spools = sorted(d for d in root.iterdir() if d.is_dir()) if root.is_dir() else []
    if not spools:
        return CheckResult(NAME, Status.OK, f"no spool yet under {root}")
    now_us = to_epoch_us(SystemClock().now_utc())
    findings = [evaluate(inspect_spool(d, settings, now_us), settings, now_us) for d in spools]
    return _combine(findings, now_us)


def evaluate(found: SpoolInspection, settings: SpoolSettings, now_us: int) -> Finding:
    """Rank one spool's inspection.

    Args:
        found: The inspection.
        settings: Thresholds and intervals.
        now_us: Current time.

    Returns:
        Finding: The worst condition found and its sentence.

    Raises:
        None
    """
    prefix = f"{found.total_bytes // MIB} MiB ({found.used_pct:.1f}%) used"
    if found.state is not CapacityState.NORMAL:
        pause = "; normal polling paused" if found.state is CapacityState.EXHAUSTED else ""
        reason = f"{prefix}, {found.state.value}{pause}; telemetry is waiting for the database"
        return Finding(_CAPACITY[found.state], reason, found)
    if problems := _warnings(found, settings, now_us):
        more = f" (+{len(problems) - 1} more)" if len(problems) > 1 else ""
        return Finding(Status.WARNING, f"{prefix}; {problems[0]}{more}", found)
    return Finding(Status.OK, f"{prefix}; {_backlog(found, now_us)}", found)


def _warnings(found: SpoolInspection, settings: SpoolSettings, now_us: int) -> list[str]:
    """List WARNING conditions, most actionable first.

    Args:
        found: The inspection.
        settings: Thresholds and intervals.
        now_us: Current time.

    Returns:
        list[str]: Sentences; empty when none apply.

    Raises:
        None
    """
    problems, status = [], found.status
    if status is not None and status.consecutive_failures and status.storage_available is False:
        problems.append(f"telemetry database not accepting writes ({status.last_error}); "
                        f"polling continues, {_backlog(found, now_us)}")  # fmt: skip
    age = _age_s(found, now_us)
    if age is not None and age > BEHIND_INTERVALS * settings.ingest_interval_s:
        problems.append(f"ingestion behind: {_backlog(found, now_us)}")
    if found.recent_incidents:
        problems.append(f"{len(found.recent_incidents)} telemetry-loss incident(s) in 24 h; "
                        f"see {found.directory}/incidents.jsonl")  # fmt: skip
    if found.quarantined_files:
        problems.append(f"{found.quarantined_files} file(s) in {found.directory}/quarantine "
                        "need review")  # fmt: skip
    stale = max(STALE_INTERVALS * settings.ingest_interval_s, STALE_FLOOR_S)
    if (
        status is not None
        and found.clean_stop is False
        and (now_us - status.taken_us) / 1e6 > stale
    ):
        problems.append("collector not reporting and did not stop cleanly")
    room = settings.max_bytes - found.total_bytes
    if found.fs_free_bytes is not None and found.fs_free_bytes < room:
        problems.append(f"only {found.fs_free_bytes // MIB} MiB free on the filesystem, less "
                        "than spool.max_size_mib allows")  # fmt: skip
    return problems


def _age_s(found: SpoolInspection, now_us: int) -> float | None:
    """Age of the oldest unacknowledged record.

    Args:
        found: The inspection.
        now_us: Current time.

    Returns:
        float | None: Seconds, or ``None`` when nothing is waiting.

    Raises:
        None
    """
    oldest = found.oldest_unacked_us
    return None if oldest is None else max(0.0, (now_us - oldest) / 1e6)


def _backlog(found: SpoolInspection, now_us: int) -> str:
    """Describe what is waiting for the database.

    Args:
        found: The inspection.
        now_us: Current time.

    Returns:
        str: For example ``oldest unacknowledged record 42s old``.

    Raises:
        None
    """
    age = _age_s(found, now_us)
    return "nothing waiting" if age is None else f"oldest unacknowledged record {age:.0f}s old"


def _combine(findings: list[Finding], now_us: int) -> CheckResult:
    """Merge per-collector findings into one result.

    Args:
        findings: One per spool directory.
        now_us: Current time.

    Returns:
        CheckResult: The worst status; its sentence (prefixed by the
        directory when there are several); summed or worst perfdata.

    Raises:
        None
    """
    order = (Status.OK, Status.UNKNOWN, Status.WARNING, Status.CRITICAL)
    worst = max(findings, key=lambda f: order.index(f.status))
    summary = worst.summary
    if len(findings) > 1:
        summary = f"{worst.inspection.directory.name}: {summary} ({len(findings)} spools)"
    ages = [a for f in findings if (a := _age_s(f.inspection, now_us)) is not None]
    incidents = sum(len(f.inspection.recent_incidents) for f in findings)
    perf = (
        PerfDatum("spool_bytes", float(sum(f.inspection.total_bytes for f in findings)), "B"),
        PerfDatum("spool_pct", round(max(f.inspection.used_pct for f in findings), 2), "%"),
        PerfDatum("spool_oldest_s", round(max(ages), 1) if ages else 0.0, "s"),  # 0: no backlog
        PerfDatum("spool_incidents", float(incidents)),
    )  # fmt: skip
    return CheckResult(NAME, worst.status, summary, perf)
