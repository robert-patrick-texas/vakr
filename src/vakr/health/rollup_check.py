"""Self-health check for the 15-minute rollups (RET-009, ROLL-T04, E.10, OPS-001).

Native rows expire only once a rollup covers them, so a rollup job that
stops or falls behind does not lose data: it keeps native data past its
retention and lets ``telemetry.db`` grow. This check, run every minute by
``vakr check health`` and by ``vakr doctor``, reports that from the file:

- OK: no installed node, no telemetry database or schema yet (the
  ``telemetry_db`` check reports those), no complete bucket waiting, or
  rollups at most 3 hours behind the newest complete native data;
- WARNING: rollups more than 3 hours behind (two hourly passes did not keep
  up: the job is catching up after an upgrade or outage, or failing);
- CRITICAL: native data older than ``telemetry.native_retention_days`` is
  still waiting for a rollup, so retention cannot expire it and disk use
  grows past the 27.3 budget.

Performance data: ``rollup_lag_s`` (frontier to the end of the newest
complete bucket with native data) and ``rollup_waiting_age_s`` (age of the
oldest native observation still waiting; 0 when none).

Rollup trouble is vakr's own health, never a target's (invariant 2).

Dependencies:
    vakr.adapters.sqlite.telemetry: The measurement and the schema status.
    vakr.app.collector.rollup_settings: E.10's completeness rule.
    vakr.config.loader, vakr.paths: Retention and the state directory.

Safety:
    Read-only. Summaries carry times, ages, and durations, never rows.

Example:
    $ vakr check health
    VAKR OK - ...; rollups: up to 2026-10-20T11:45:00+00:00, 0.4 h behind | rollup_lag_s=1440.0s ...
"""

from datetime import timedelta

from vakr.adapters.sqlite.telemetry import TELEMETRY_FILE, retention_policy, telemetry_migrations
from vakr.adapters.sqlite.telemetry.rollup_health import RollupVitals, measure_rollups
from vakr.app.collector.rollup_settings import SETTLE, complete_end
from vakr.config.loader import load_effective_config
from vakr.domain.errors import StorageError
from vakr.domain.rollups import ROLLUP_BUCKET_SECONDS
from vakr.domain.timestamps import from_epoch_us, to_epoch_us
from vakr.health.model import CheckResult, PerfDatum, Status
from vakr.paths import InstallPaths
from vakr.ports.clock import SystemClock
from vakr.ports.migrations import SchemaState

NAME = "rollups"
DOC = "see docs/operations/rollups.md"
LAG_WARN = timedelta(hours=3)
"""Rollups further behind than this are WARNING: two hourly passes missed (E.10)."""

_BUCKET_US = ROLLUP_BUCKET_SECONDS * 1_000_000


def check_rollups() -> CheckResult:
    """Check how far the rollups are behind the native data that is ready for them.

    Args:
        None

    Returns:
        CheckResult: As the module docstring describes, with performance data.

    Raises:
        None
    """
    paths = InstallPaths.from_environment()
    if not paths.state_dir.exists(follow_symlinks=False):
        return CheckResult(NAME, Status.OK, f"no {paths.state_dir}; not an installed vakr node")
    path = paths.state_dir / TELEMETRY_FILE
    manager = telemetry_migrations(path)
    try:
        if not path.exists() or manager.status().state is not SchemaState.CURRENT:
            return CheckResult(NAME, Status.OK, "no current telemetry database; see telemetry_db")
        native = retention_policy(load_effective_config(paths.config_dir, strict=False)).native
        now = SystemClock().now_utc()
        vitals = measure_rollups(path, to_epoch_us(complete_end(now, None, SETTLE)))
    except StorageError as exc:
        return CheckResult(NAME, Status.UNKNOWN, f"cannot read {path}: {exc}")
    except (OSError, ValueError) as exc:
        return CheckResult(NAME, Status.UNKNOWN, f"cannot read configuration: {type(exc).__name__}")
    return judge(vitals, to_epoch_us(now), native)


def judge(vitals: RollupVitals, now_us: int, native: timedelta) -> CheckResult:
    """Rank the measured rollup progress.

    Args:
        vitals: The measurements.
        now_us: Current time, microseconds.
        native: Native retention (``telemetry.native_retention_days``).

    Returns:
        CheckResult: OK, WARNING, or CRITICAL as the module docstring describes.

    Raises:
        None
    """
    frontier = "nothing yet" if vitals.frontier_us is None else _iso(vitals.frontier_us)
    lag_s = waiting_s = 0.0
    if vitals.oldest_us is not None and vitals.newest_us is not None:
        start = vitals.frontier_us
        if start is None:
            start = vitals.oldest_us - vitals.oldest_us % _BUCKET_US
        end = vitals.newest_us - vitals.newest_us % _BUCKET_US + _BUCKET_US
        lag_s, waiting_s = (end - start) / 1e6, max(0.0, (now_us - vitals.oldest_us) / 1e6)
    perf = (PerfDatum("rollup_lag_s", round(lag_s, 1), "s"),
            PerfDatum("rollup_waiting_age_s", round(waiting_s, 1), "s"))  # fmt: skip
    summary = f"up to {frontier}, {lag_s / 3600:.1f} h behind"
    if waiting_s > native.total_seconds():
        why = (f"native data {waiting_s / 86_400:.1f} days old has no rollup yet, so retention "
               "cannot expire it and disk use grows")  # fmt: skip
        return CheckResult(NAME, Status.CRITICAL, f"{summary}: {why}; {DOC}", perf)
    if lag_s > LAG_WARN.total_seconds():
        why = ("the hourly rollup job is not keeping up (catching up after an upgrade or an "
               "outage, or failing); native data is kept until it catches up")  # fmt: skip
        return CheckResult(NAME, Status.WARNING, f"{summary}: {why}; {DOC}", perf)
    return CheckResult(NAME, Status.OK, summary, perf)


def _iso(ts_us: int) -> str:
    """Format a timestamp for a summary.

    Args:
        ts_us: Microseconds since the epoch.

    Returns:
        str: ISO 8601, UTC, to the second.

    Raises:
        None
    """
    return from_epoch_us(ts_us).replace(microsecond=0).isoformat()
