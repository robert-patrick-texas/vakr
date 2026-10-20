"""Self-health checks for update outcome and trusted-CA expiry (UPD-006, EGRESS-006).

``vakr check health`` runs every minute from CheckMK/Nagios, so these checks
read only local state: the last update record and the extra CA certificates.
They live in ``vakr.health`` rather than beside their CLI commands so the
health registry never imports the CLI layer.

Dependencies:
    vakr.lifecycle.update.state: Reads the last self-update record.
    vakr.lifecycle.egress.expiry: Summarises CA certificate expiry.
    vakr.paths: Locates the state and CA directories.

Safety:
    No network I/O and no writes; summaries never include secrets.

Example:
    >>> check_trusted_cas().name
    'trusted_cas'
"""

from datetime import UTC, datetime

from vakr.health.model import CheckResult, PerfDatum, Status
from vakr.lifecycle.egress.expiry import expiry_report
from vakr.lifecycle.update.state import StateError, load_state
from vakr.paths import InstallPaths

_SEVERITY = {"ok": Status.OK, "warning": Status.WARNING, "critical": Status.CRITICAL}


def check_update() -> CheckResult:
    """Health check: outcome of the last update attempt (UPD-006); no network I/O.

    Args:
        None

    Returns:
        CheckResult: WARNING after a failed or refused update or an
        unreadable record; otherwise OK.

    Raises:
        None
    """
    state_file = InstallPaths.from_environment().state_dir / "update-state.json"
    try:
        state = load_state(state_file)
    except StateError:
        return CheckResult("update", Status.WARNING, f"cannot read {state_file}")
    if state.result == "never":
        return CheckResult("update", Status.OK, "never updated")
    summary = f"last update {state.result} {state.from_version} -> {state.to_version} at {state.at}"
    if state.result in {"failed", "refused"}:
        return CheckResult("update", Status.WARNING, f"{summary}: {state.message[:160]}")
    return CheckResult("update", Status.OK, summary)


def check_trusted_cas() -> CheckResult:
    """Health check: trusted CA expiry (EGRESS-006); no network I/O.

    Args:
        None

    Returns:
        CheckResult: CRITICAL if a CA has expired, WARNING if one expires
        within 30 days or is unreadable, otherwise OK.

    Raises:
        None
    """
    report = expiry_report(InstallPaths.from_environment().ca_dir, datetime.now(UTC))
    days = report.min_days_left
    perf = () if days is None else (PerfDatum("ca_min_days_left", days),)
    return CheckResult("trusted_cas", _SEVERITY[report.severity], report.summary, perf)
