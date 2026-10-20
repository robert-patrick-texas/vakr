"""Self-health checks for update outcome, trusted CAs, and TLS policy (UPD-006, EGRESS-006/007).

``vakr check health`` runs every minute from CheckMK/Nagios, so these checks
read only local state: the last update record, the extra CA certificates,
and the configuration. They live in ``vakr.health`` rather than beside their
CLI commands so the health registry never imports the CLI layer. The
strict X.509 option is interpreted here once, for the check and for the
commands that build outbound HTTPS settings.

Dependencies:
    vakr.lifecycle.update.state: Reads the last self-update record.
    vakr.lifecycle.egress.expiry: Summarises CA certificate expiry.
    vakr.config.loader: Reads ``network.tls_strict_x509``.
    vakr.paths: Locates the state, CA, and configuration directories.

Safety:
    No network I/O and no writes; summaries never include secrets.

Example:
    >>> check_trusted_cas().name
    'trusted_cas'
"""

from datetime import UTC, datetime

from vakr.config.loader import EffectiveConfig, load_effective_config
from vakr.health.model import CheckResult, PerfDatum, Status
from vakr.lifecycle.egress.expiry import expiry_report
from vakr.lifecycle.update.state import StateError, load_state
from vakr.paths import InstallPaths

_SEVERITY = {"ok": Status.OK, "warning": Status.WARNING, "critical": Status.CRITICAL}
STRICT_X509_OPTION = "network.tls_strict_x509"
"""Option that relaxes only the strict X.509 profile when false (EGRESS-007)."""


def strict_x509_enabled(config: EffectiveConfig) -> bool:
    """Return whether outbound HTTPS enforces the strict X.509 profile.

    Args:
        config: Effective configuration.

    Returns:
        bool: ``False`` only for an explicit boolean ``false``; anything
        else, including a missing or invalid value, keeps the profile on.

    Raises:
        None
    """
    return config.get(STRICT_X509_OPTION, True) is not False


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


def check_tls_policy() -> CheckResult:
    """Health check: WARNING while the strict X.509 profile is relaxed (EGRESS-007).

    Reads the configuration only; no network I/O.

    Args:
        None

    Returns:
        CheckResult: WARNING when ``network.tls_strict_x509 = false``,
        UNKNOWN when the configuration cannot be read, otherwise OK.

    Raises:
        None
    """
    config_dir = InstallPaths.from_environment().config_dir
    try:
        config = load_effective_config(config_dir, strict=False)
    except (OSError, ValueError) as exc:
        return CheckResult(
            "tls_policy", Status.UNKNOWN, f"cannot read {config_dir}: {type(exc).__name__}"
        )
    if strict_x509_enabled(config):
        return CheckResult(
            "tls_policy", Status.OK, "strict X.509 profile enforced for outbound HTTPS"
        )
    summary = (
        f"strict X.509 checks relaxed for outbound HTTPS ({STRICT_X509_OPTION} = false); "
        "certificate and host name verification still apply"
    )
    return CheckResult("tls_policy", Status.WARNING, summary)
