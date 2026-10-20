"""Post-install details for ``vakr self-update``: health settle and new options.

Two operator needs sit between "the package installed" and "the update is
done". First, services restarted by the update can look unhealthy for a few
seconds, and a health check that runs too early would roll back a good
release. Second, a release may add configuration options, which operators
should see without having to diff files (CFGM-004).

Dependencies:
    vakr.lifecycle.update.model: The post-install ``Hook`` type.
    vakr.lifecycle.update.process: Runs the newly installed binary.

Safety:
    The settle window travels in an environment variable that older
    releases ignore, so the hook stays compatible when an operator
    explicitly installs an older version. Listing new options is
    informational and never fails an update.

Example:
    >>> health_hook("/opt/vakr/venv/bin/vakr", 30).argv[:2]
    ('/usr/bin/env', 'VAKR_HEALTH_RETRY_FOR=30')
"""

from collections.abc import Callable, Sequence

from vakr.cli.cmd_check import RETRY_ENV
from vakr.lifecycle.update.model import Hook
from vakr.lifecycle.update.process import RunResult

ENV = "/usr/bin/env"


def health_hook(new_vakr: str, settle_seconds: int) -> Hook:
    """Build the final post-install hook: the new binary's health check.

    Args:
        new_vakr: Path of the newly installed ``vakr`` executable.
        settle_seconds: How long to keep re-checking while CRITICAL.

    Returns:
        Hook: Runs ``check health``. WARNING and UNKNOWN count as success,
        because only CRITICAL means the new release is broken; the retry
        variable also tells ``check health`` to count host-setting checks
        (ICMP permission) as warnings, which a rollback could not fix.

    Raises:
        None
    """
    argv = (ENV, f"{RETRY_ENV}={max(0, settle_seconds)}", new_vakr, "check", "health")
    return Hook("health check", argv, ok_codes=frozenset({0, 1, 3}))


def new_options_lines(
    new_vakr: str, from_version: str, run: Callable[[Sequence[str]], RunResult]
) -> list[str]:
    """Describe options the new release added since ``from_version``.

    Args:
        new_vakr: Path of the newly installed ``vakr`` executable.
        from_version: Version that was installed before the update.
        run: Command runner (``run_command`` in production).

    Returns:
        list[str]: Lines to print, or an empty list when nothing was added
        or the listing is unavailable.

    Raises:
        None
    """
    if not from_version:
        return []
    result = run((new_vakr, "config", "new-options", "--since", from_version))
    listing = result.stdout.strip() or "none: 0"
    # The listing starts "options added since X: N"; N == 0 means nothing new.
    header, *body = listing.splitlines()
    if result.returncode != 0 or header.endswith(": 0") or not body:
        return []
    return [
        "",
        f"New configuration options since {from_version} (packaged defaults apply):",
        *body,
        "To add them to your files as commented examples: vakr config new-options --annotate",
    ]
