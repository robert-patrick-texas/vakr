"""Startup preflight checks of the collector service (16.3, SVC-005, SVC-T02).

Before any discovery or polling, the collector proves it can run safely:
the configuration is valid, the state directory is writable, both
databases are at exactly the schema this release runs on (a newer schema is
refused, MIG-004), the credentials file is safe to read, and the ICMP
capability is checked. Each check either returns what it opened or raises
``PreflightError`` with an operator sentence that says what is wrong and
what to do, and never a secret or a configuration value.

Two conditions only warn, because collection still works without them: no
credentials file yet (ICMP still runs; SNMP polls report a collector-side
credential problem), and ``ping_group_range`` excluding the collector (SNMP
still runs, E.22, E.25 #5).

Dependencies:
    vakr.config: Strict configuration loading and cross-option rules.
    vakr.adapters.sqlite: The two stores, whose constructors are the schema preflight.
    vakr.credentials: The protected-file provider.
    vakr.adapters.icmp.health: The ``ping_group_range`` check.

Safety:
    Blocking: run each check in a worker thread. Messages carry paths, keys,
    and counts only.

Example:
    >>> PreflightError("config", "invalid").check
    'config'
"""

import os
from pathlib import Path

from vakr.adapters.icmp.health import check_icmp, raw_socket_problem, socket_problem
from vakr.adapters.sqlite.metadata import METADATA_FILE, SqliteMetadataStore
from vakr.adapters.sqlite.telemetry import TELEMETRY_FILE, SqliteTelemetryStore, retention_policy
from vakr.config.cross_checks import first_problem
from vakr.config.loader import ConfigError, EffectiveConfig, load_effective_config
from vakr.credentials import (
    CredentialFileError,
    CredentialFileMissingError,
    CredentialFileUnreadableError,
    CredentialPermissionError,
    FileCredentialProvider,
)
from vakr.domain.errors import StorageError
from vakr.ports.migrations import MigrationError, SchemaTooNewError

MAX_ISSUES = 5
"""Configuration problems listed at most; ``vakr config validate`` lists all."""


class PreflightError(Exception):
    """A startup check failed; the collector exits before discovery (16.3).

    Attributes:
        check: Short name of the failed check.
        message: Operator sentence with the fix.
    """

    def __init__(self, check: str, message: str) -> None:
        """Record the failed check.

        Args:
            check: Short name, such as ``config`` or ``metadata_db``.
            message: What is wrong and what to do.

        Returns:
            None

        Raises:
            None
        """
        super().__init__(f"{check}: {message}")
        self.check, self.message = check, message


def check_config(config_dir: Path) -> EffectiveConfig:
    """Load the configuration strictly and run the cross-option rules.

    Args:
        config_dir: ``/etc/vakr``.

    Returns:
        EffectiveConfig: The validated configuration.

    Raises:
        PreflightError: If any file is invalid or a cross-option rule fails.
    """
    try:
        config = load_effective_config(config_dir)
    except ConfigError as exc:
        shown = "; ".join(str(issue) for issue in exc.issues[:MAX_ISSUES])
        more = len(exc.issues) - MAX_ISSUES
        tail = f" (+{more} more)" if more > 0 else ""
        raise PreflightError("config", f"{shown}{tail}; run `vakr config validate`") from None
    except OSError as exc:
        raise PreflightError("config", f"cannot read {config_dir} ({exc.strerror})") from None
    if (problem := first_problem(config)) is not None:
        label, lines = problem
        raise PreflightError("config", f"{label}: {'; '.join(lines)}")
    return config


def check_state_dir(state_dir: Path) -> None:
    """Require a writable state directory (databases, spool, checkpoints).

    Args:
        state_dir: ``/var/lib/vakr``.

    Returns:
        None

    Raises:
        PreflightError: If it is missing or not writable by this user.
    """
    if not state_dir.is_dir():
        raise PreflightError(
            "state_dir",
            f"{state_dir} does not exist; reinstall the package "
            "or run `systemd-tmpfiles --create vakr.conf`",
        )
    if not os.access(state_dir, os.W_OK | os.X_OK):
        raise PreflightError(
            "state_dir", f"{state_dir} is not writable by this user; the collector runs as vakr"
        )


def _schema_problem(label: str, exc: Exception) -> PreflightError:
    """Turn a store-opening failure into operator advice.

    Args:
        label: ``metadata_db`` or ``telemetry_db``.
        exc: The error.

    Returns:
        PreflightError: With the fix for that kind of failure.

    Raises:
        None
    """
    if isinstance(exc, SchemaTooNewError):
        return PreflightError(
            label,
            f"{exc}: written by a newer vakr release, which this "
            "release refuses to run on (MIG-004); upgrade vakr or restore "
            "the backup taken before the downgrade",
        )
    if isinstance(exc, MigrationError):
        return PreflightError(label, f"{exc}; run `sudo vakr db migrate`")
    return PreflightError(label, f"{exc}; see `vakr db status`")


def open_metadata(state_dir: Path) -> SqliteMetadataStore:
    """Open the metadata database at the current schema.

    Args:
        state_dir: ``/var/lib/vakr``.

    Returns:
        SqliteMetadataStore: The open store.

    Raises:
        PreflightError: If the schema is missing, older, newer, or unreadable.
    """
    try:
        return SqliteMetadataStore(state_dir / METADATA_FILE)
    except (MigrationError, StorageError) as exc:
        raise _schema_problem("metadata_db", exc) from None


def open_telemetry(state_dir: Path, config: EffectiveConfig) -> SqliteTelemetryStore:
    """Open the telemetry database at the current schema.

    Args:
        state_dir: ``/var/lib/vakr``.
        config: For the retention policy.

    Returns:
        SqliteTelemetryStore: The open store.

    Raises:
        PreflightError: If the schema is missing, older, newer, or unreadable.
    """
    try:
        return SqliteTelemetryStore(state_dir / TELEMETRY_FILE, policy=retention_policy(config))
    except (MigrationError, StorageError) as exc:
        raise _schema_problem("telemetry_db", exc) from None


def check_credentials(provider: FileCredentialProvider) -> str | None:
    """Check that the credentials file is safe and valid.

    Args:
        provider: The provider the SNMP adapter will use.

    Returns:
        str | None: A warning when there is no file yet, else ``None``.

    Raises:
        PreflightError: If the file is unsafe, invalid, or unreadable.
    """
    hint = "run `vakr credentials check`"
    try:
        provider.summaries()
    except CredentialFileMissingError:
        return (
            f"no credentials file at {provider.path} yet: ICMP runs, SNMP polls report "
            "a collector-side credential problem; see docs/operations/credentials.md"
        )
    except CredentialFileUnreadableError as exc:
        raise PreflightError(
            "credentials",
            f"cannot read {provider.path}: {exc.reason}; the "
            "collector runs as vakr, which must be in group vakr",
        ) from None
    except CredentialPermissionError as exc:
        raise PreflightError(
            "credentials", f"unsafe {provider.path}: {exc.problems[0]}; {hint}"
        ) from None
    except CredentialFileError as exc:
        raise PreflightError(
            "credentials", f"invalid {provider.path}: {len(exc.issues)} problem(s); {hint}"
        ) from None
    return None


def open_credentials(
    secrets_dir: Path, development: bool
) -> tuple[FileCredentialProvider, str | None]:
    """Create the credential provider and check its file.

    The provider looks up the vakr account through NSS, which may block, so
    this belongs in a worker thread with the file check.

    Args:
        secrets_dir: ``/etc/vakr/secrets``.
        development: Tolerate unsafe permissions, with warnings.

    Returns:
        tuple[FileCredentialProvider, str | None]: The provider and a warning, if any.

    Raises:
        PreflightError: If the file is unsafe, invalid, or unreadable.
    """
    provider = FileCredentialProvider(secrets_dir, development=development)
    return provider, check_credentials(provider)


def check_icmp_capability() -> None:
    """Refuse to start on a host where this process cannot ping (ICMP-012).

    A host that cannot send ICMP Echo is misconfigured, and collecting on it
    would show every device as unknown, so it is a hard stop (owner decision
    2026-10-20). The test is what this process can open: an unprivileged
    ping socket, or a raw socket when the ``CAP_NET_RAW`` drop-in is in place.

    Args:
        None

    Returns:
        None

    Raises:
        PreflightError: If neither socket can be opened; the message carries
            the exact ``ping_group_range`` fix.
    """
    if socket_problem() is None or raw_socket_problem() is None:
        return
    raise PreflightError("icmp", f"this host cannot send ICMP Echo: {check_icmp().summary}")
