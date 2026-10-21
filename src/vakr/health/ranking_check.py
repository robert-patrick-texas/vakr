"""Self-health check for the ranking projection (E.28, 12.9, OPS-001).

The collector's projector keeps the last 25 hours of complete 15-minute
buckets projected, so 6-hour and 24-hour rankings answer within the 2 s
dashboard budget. A projector that stops or falls behind never changes a
ranking's values: buckets it has not projected are read from native
samples, exactly, only slower. This check, run every minute by
``vakr check health`` and by ``vakr doctor``, reports that from the file:

- OK: no installed node, no current telemetry database (the
  ``telemetry_db`` check reports that), no native data in the last 25
  hours, or at most 1 hour of complete buckets not projected;
- WARNING: more than 1 hour of complete buckets not projected (four
  15-minute passes did not keep up: the collector is stopped, rebuilding
  after a start, or failing), so long rankings may exceed 2 s.

It is never CRITICAL, because no data is at risk. Performance data:
``ranking_unprojected_s`` (complete native data of the last 25 hours that
rankings read natively). Projection trouble is vakr's own health, never a
target's (invariant 2).

Dependencies:
    vakr.adapters.sqlite.telemetry: The measurement and the schema status.
    vakr.app.collector.rollup_settings: E.10's completeness rule.
    vakr.paths: The state directory.

Safety:
    Read-only. Summaries carry counts and durations, never rows.

Example:
    $ vakr check health
    VAKR OK - ...; rankings: 96 bucket(s) of the last 25 h projected, 0.0 h read natively | ...
"""

from datetime import timedelta

from vakr.adapters.sqlite.telemetry import TELEMETRY_FILE, telemetry_migrations
from vakr.adapters.sqlite.telemetry.ranking_health import ProjectionVitals, measure_projection
from vakr.app.collector.rollup_settings import SETTLE, complete_end
from vakr.domain.errors import StorageError
from vakr.domain.timestamps import to_epoch_us
from vakr.health.model import CheckResult, PerfDatum, Status
from vakr.paths import InstallPaths
from vakr.ports.clock import SystemClock
from vakr.ports.migrations import SchemaState

NAME = "rankings"
DOC = "see docs/operations/rollups.md#ranking-projection"
BEHIND_WARN = timedelta(hours=1)
"""More unprojected complete data than this is WARNING: four 15-minute passes missed (E.28)."""


def check_rankings() -> CheckResult:
    """Check how much complete native data of the last 25 hours the projection misses.

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
    try:
        if not path.exists() or telemetry_migrations(path).status().state is not (
            SchemaState.CURRENT
        ):
            return CheckResult(NAME, Status.OK, "no current telemetry database; see telemetry_db")
        now = SystemClock().now_utc()
        end = to_epoch_us(complete_end(now, None, SETTLE))
        vitals = measure_projection(path, end, to_epoch_us(now))
    except StorageError as exc:
        return CheckResult(NAME, Status.UNKNOWN, f"cannot read {path}: {exc}")
    return judge(vitals)


def judge(vitals: ProjectionVitals) -> CheckResult:
    """Rank the measured projection progress.

    Args:
        vitals: The measurements.

    Returns:
        CheckResult: OK or WARNING as the module docstring describes.

    Raises:
        None
    """
    behind_s = vitals.missing_s
    perf = (PerfDatum("ranking_unprojected_s", float(behind_s), "s"),)
    summary = (f"{vitals.listed} bucket(s) of the last 25 h projected, "
               f"{behind_s / 3600:.1f} h read natively")  # fmt: skip
    if behind_s > BEHIND_WARN.total_seconds():
        why = ("the collector's ranking projector is not keeping up (stopped, rebuilding after "
               "a start, or failing); rankings stay exact but 6-hour and 24-hour ones may take "
               "longer than 2 s")  # fmt: skip
        return CheckResult(NAME, Status.WARNING, f"{summary}: {why}; {DOC}", perf)
    return CheckResult(NAME, Status.OK, summary, perf)
