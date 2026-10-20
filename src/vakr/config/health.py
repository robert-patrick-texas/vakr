"""Self-health check for the operator configuration.

A configuration that fails validation stops services from starting or
reloading, and a pending ``config upgrade`` means the files on disk still use
an older schema. Both are worth a page through CheckMK or Nagios before the
next restart turns them into an outage (OPS-001, CFGM-003).

Dependencies:
    vakr.config.project: Loads and validates the configuration.
    vakr.health.model: Check result types.
    vakr.paths: Locates ``/etc/vakr``.

Safety:
    Read-only. The summary reports counts and versions, never values.

Example:
    $ vakr check health
    VAKR OK - config: valid (config_version 1, 1 operator file(s)); ...
"""

from vakr.config import project
from vakr.health.model import CheckResult, Status
from vakr.paths import InstallPaths


def check_config() -> CheckResult:
    """Validate the configuration and report whether an upgrade is pending.

    Args:
        None

    Returns:
        CheckResult: CRITICAL when the configuration is invalid, WARNING
        when an operator file's ``config_version`` is older than this
        release's, UNKNOWN when files cannot be read, otherwise OK.

    Raises:
        None
    """
    config_dir = InstallPaths.from_environment().config_dir
    try:
        result = project.inspect(config_dir)
    except OSError as exc:
        return CheckResult(
            "config", Status.UNKNOWN, f"cannot read {config_dir}: {type(exc).__name__}"
        )
    current = project.catalog().config_version
    if result.issues:
        summary = f"{len(result.issues)} validation problem(s); run `vakr config validate`"
        return CheckResult("config", Status.CRITICAL, summary)
    stale = sorted(v for v in result.versions.values() if v < current)
    if stale:
        summary = (
            f"config upgrade pending (config_version {stale[0]} < {current}); "
            "run `vakr config upgrade`"
        )
        return CheckResult("config", Status.WARNING, summary)
    summary = f"valid (config_version {current}, {len(result.versions)} operator file(s))"
    return CheckResult("config", Status.OK, summary)
