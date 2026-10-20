"""The ordered list of self-checks run by ``vakr doctor`` and ``vakr check health``.

New subsystems add their check function to ``CHECKS`` here, explicitly, so a
reviewer can see in one place everything that can turn vakr's own health
amber or red. Each check is a zero-argument callable returning a
``CheckResult``; a check that raises is reported as UNKNOWN rather than
crashing the command, because a monitoring plugin that dies prints nothing
useful to the on-call engineer.

Dependencies:
    vakr.health.model: Result types and Nagios rendering.
    vakr.version: Reports which release is running.

Safety:
    Checks run with the caller's privileges and must be read-only.

Example:
    >>> results = run_checks()
    >>> results[0].name
    'version'
"""

import platform
from collections.abc import Callable

from vakr.adapters.icmp.health import check_icmp
from vakr.adapters.sqlite.metadata.health import check_metadata_db
from vakr.config.health import check_config
from vakr.credentials.health import check_credentials
from vakr.health.lifecycle_checks import check_tls_policy, check_trusted_cas, check_update
from vakr.health.model import CheckResult, Status
from vakr.health.spool_check import check_spool
from vakr.version import UNKNOWN_VERSION, get_version

type Check = Callable[[], CheckResult]


def check_version() -> CheckResult:
    """Report the running vakr version so dashboards show what is deployed.

    Args:
        None

    Returns:
        CheckResult: OK with the version, or UNKNOWN when package metadata is
        missing (vakr running from an unbuilt checkout).

    Raises:
        None
    """
    current = get_version()
    if current == UNKNOWN_VERSION:
        return CheckResult("version", Status.UNKNOWN, "package metadata not found")
    return CheckResult("version", Status.OK, f"vakr {current}")


def check_python() -> CheckResult:
    """Report the interpreter vakr runs on (the bundled one in production).

    Args:
        None

    Returns:
        CheckResult: Always OK; the value helps confirm the packaged runtime.

    Raises:
        None
    """
    return CheckResult("python", Status.OK, f"Python {platform.python_version()}")


CHECKS: tuple[Check, ...] = (
    check_version,
    check_python,
    check_config,
    check_credentials,
    check_update,
    check_trusted_cas,
    check_tls_policy,
    check_metadata_db,
    check_icmp,
    check_spool,
)


def run_checks(checks: tuple[Check, ...] = CHECKS) -> tuple[CheckResult, ...]:
    """Evaluate every check, converting unexpected exceptions into UNKNOWN.

    Args:
        checks: Check callables to run, in display order.

    Returns:
        tuple[CheckResult, ...]: One result per check, in the same order.

    Raises:
        None
    """
    results: list[CheckResult] = []
    for check in checks:
        name = getattr(check, "__name__", "check").removeprefix("check_")
        try:
            results.append(check())
        # A failing check must still produce plugin output; the exception type
        # (never its message, which could echo data) tells the operator why.
        except Exception as exc:  # noqa: BLE001
            summary = f"check failed internally ({type(exc).__name__})"
            results.append(CheckResult(name, Status.UNKNOWN, summary))
    return tuple(results)
