"""``vakr tls``: trust an enterprise TLS-inspection CA for outbound HTTPS.

Hosts behind a decrypting proxy see GitHub's certificate re-signed by the
enterprise CA, so updates fail until that CA is trusted (blueprint 28.3.1).
``trust-ca`` validates a PEM CA certificate, shows its fingerprint for
out-of-band comparison, and installs it into ``/etc/vakr/tls/ca.d``
(EGRESS-003). This module also binds the portable egress library to vakr's
paths and configuration for the other commands, and provides the CA-expiry
health check (EGRESS-006) and ``doctor --network`` checks (EGRESS-005).

Dependencies:
    vakr.lifecycle.egress: Trust store, CA store, proxies, diagnosis.
    vakr.lifecycle.update.journal: Audit events to journald.
    vakr.paths, vakr.config.loader: Installation paths and settings.

Safety:
    Nothing is installed without confirmation or ``--yes``; leaf, expired,
    and non-PEM certificates are refused. ``--system`` changes trust for
    every program on the host and is therefore opt-in. Every change emits an
    audit event. There is no option to disable verification (EGRESS-004).

Example:
    $ sudo vakr tls trust-ca corp-inspection.pem --name corp-inspection
"""

import argparse
import os
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from vakr.cli.common import SubParsers
from vakr.config.loader import EffectiveConfig, load_effective_config
from vakr.health.lifecycle_checks import check_trusted_cas  # noqa: F401 - re-export for callers
from vakr.health.model import CheckResult, Status
from vakr.lifecycle.egress import castore, certinfo, truststore
from vakr.lifecycle.egress.client import EgressSettings
from vakr.lifecycle.egress.diagnose import diagnose
from vakr.lifecycle.egress.errors import EgressError
from vakr.lifecycle.egress.proxy import resolve_policy
from vakr.lifecycle.update.console import confirmer
from vakr.lifecycle.update.journal import EventSink
from vakr.lifecycle.update.process import run_command
from vakr.paths import InstallPaths
from vakr.version import get_version

TRUST_HINT = "verify its fingerprint out of band, then run: vakr tls trust-ca FILE"
EGRESS_URLS = (
    "https://api.github.com/",
    "https://github.com/",
    "https://release-assets.githubusercontent.com/",
)
"""GitHub API, release page, and release-asset download hosts."""
DIAGNOSIS_TIMEOUT = 10.0
"""Per-step timeout for diagnosis, so a blackholed host cannot stall doctor for minutes."""
SEVERITY = {"ok": Status.OK, "warning": Status.WARNING, "critical": Status.CRITICAL}
OS_BUNDLES: tuple[Path, ...] = truststore.OS_BUNDLES
SYSTEM_STORES: tuple[castore.SystemStore, ...] = castore.SYSTEM_STORES
AUDIT = EventSink("vakr-tls")
"""Trust changes are audited as journald events (EGRESS-003)."""


def register(subparsers: SubParsers) -> None:
    """Add ``tls`` with its ``trust-ca``, ``list``, and ``remove`` actions.

    Args:
        subparsers: The top-level sub-parser collection.

    Returns:
        None

    Raises:
        None
    """
    parser = subparsers.add_parser("tls", help="manage extra trusted CA certificates (EGRESS-003)")
    actions = parser.add_subparsers(title="actions", metavar="ACTION", required=True)
    trust = actions.add_parser("trust-ca", help="validate and trust a CA certificate (PEM)")
    trust.add_argument("file", type=Path, help="PEM CA certificate from your security team")
    trust.add_argument("--name", help="name for the installed CA (default: from the file name)")
    trust.add_argument("--system", action="store_true", help="also add it to the OS trust store")
    trust.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    trust.set_defaults(func=run_trust)
    actions.add_parser("list", help="list trusted CAs and their expiry").set_defaults(func=run_list)
    remove = actions.add_parser("remove", help="stop trusting a CA installed with trust-ca")
    remove.add_argument("name", help="name shown by 'vakr tls list'")
    remove.set_defaults(func=run_remove)


def run_trust(args: argparse.Namespace) -> int:
    """Validate, show, confirm, and install a CA certificate.

    Args:
        args: ``file``, ``name``, ``system``, and ``yes``.

    Returns:
        int: 0 when installed, 1 when refused, cancelled, or failed.

    Raises:
        None
    """
    paths, now = InstallPaths.from_environment(), datetime.now(UTC)
    try:
        data = args.file.read_bytes()
        cert = certinfo.validate_ca(data, now)
        name = castore.check_name(args.name or castore.default_name(args.file))
    except (OSError, certinfo.CertificateRejectedError) as exc:
        return _fail(f"refusing {args.file}: {exc}; nothing was installed")
    summary = certinfo.summarize(cert)
    print(f"subject:  {summary.subject}\nissuer:   {summary.issuer}")
    print(f"SHA-256:  {summary.sha256}\nexpires:  {summary.not_after:%Y-%m-%d %H:%M}Z")
    if not confirmer(args.yes)(f"Trust this CA as {name!r} for vakr outbound HTTPS?"):
        return _fail("not installed")
    try:
        path = castore.install(data, name, paths.ca_dir, now)
        AUDIT("tls.ca.trusted", f"trusted CA {name}: {summary.subject} SHA-256 {summary.sha256}")
        print(f"installed {path}")
        if args.system:
            store = castore.detect_system_store(SYSTEM_STORES)
            if store is None:
                return _fail("no supported OS trust store found; installed for vakr only")
            anchor = castore.install_system(data, name, store, _status)
            AUDIT("tls.ca.system", f"added CA {name} to the OS trust store at {anchor}")
    except (OSError, RuntimeError, certinfo.CertificateRejectedError) as exc:
        return _fail(str(exc))
    return 0


def run_list(args: argparse.Namespace) -> int:  # noqa: ARG001 - uniform handler signature
    """Print each trusted CA with its expiry state.

    Args:
        args: Parsed arguments (unused).

    Returns:
        int: Always 0.

    Raises:
        None
    """
    paths, now = InstallPaths.from_environment(), datetime.now(UTC)
    entries = castore.list_installed(paths.ca_dir)
    if not entries:
        print(f"no extra trusted CAs in {paths.ca_dir}")
    for entry in entries:
        if entry.summary is None:
            print(f"{entry.name}: UNREADABLE ({entry.error})")
            continue
        state = certinfo.expiry_state(entry.summary.not_after, now)
        print(f"{entry.name}: {state}, expires {entry.summary.not_after:%Y-%m-%d}")
        print(f"    subject {entry.summary.subject}\n    SHA-256 {entry.summary.sha256}")
    return 0


def run_remove(args: argparse.Namespace) -> int:
    """Remove a trusted CA, and its OS-store copy if this command added one.

    Args:
        args: ``name``.

    Returns:
        int: 0 when removed, 1 otherwise.

    Raises:
        None
    """
    paths = InstallPaths.from_environment()
    try:
        name = castore.check_name(args.name)
        installed = {e.name: e.path for e in castore.list_installed(paths.ca_dir)}
        data = installed[name].read_bytes() if name in installed else b""
        removed = castore.remove(name, paths.ca_dir)
        AUDIT("tls.ca.removed", f"removed trusted CA {name} ({removed})")
        store = castore.detect_system_store(SYSTEM_STORES)
        if store is not None and castore.remove_system(name, data, store, _status):
            AUDIT("tls.ca.system.removed", f"removed CA {name} from the OS trust store")
    except (OSError, RuntimeError, certinfo.CertificateRejectedError) as exc:
        return _fail(str(exc))
    return 0


def build_egress_settings(
    paths: InstallPaths,
    config: EffectiveConfig,
    environ: Mapping[str, str] | None = None,
    timeout: float = 30.0,
) -> EgressSettings:
    """Build the shared outbound HTTPS settings from vakr paths and config (EGRESS-001).

    Args:
        paths: Installation paths (for ``ca_dir``).
        config: Effective configuration.
        environ: Environment for proxy fallback; ``None`` means ``os.environ``.
        timeout: Socket timeout per operation, in seconds.

    Returns:
        EgressSettings: Verifying context, proxy policy, and user agent.

    Raises:
        EgressError: When the trust store or proxy settings are invalid.
    """
    https_proxy = config.get("network.https_proxy")
    no_proxy = config.get("network.no_proxy")
    env = os.environ if environ is None else environ
    policy = resolve_policy(str(https_proxy or ""), no_proxy, env)
    context = truststore.build_context(paths.ca_dir, OS_BUNDLES)
    return EgressSettings(context, policy, timeout=timeout, user_agent=f"vakr/{get_version()}")


def network_checks(urls: Sequence[str] = ()) -> tuple[CheckResult, ...]:
    """Diagnose egress to each URL, one result per step (``doctor --network``).

    Args:
        urls: HTTPS URLs to diagnose; empty means ``EGRESS_URLS``.

    Returns:
        tuple[CheckResult, ...]: OK or CRITICAL per step; the failing step's
        summary carries the hint when an untrusted issuer was presented.

    Raises:
        None
    """
    paths = InstallPaths.from_environment()
    try:
        config = load_effective_config(paths.config_dir, strict=False)
        settings = build_egress_settings(paths, config, timeout=DIAGNOSIS_TIMEOUT)
    except (EgressError, OSError, ValueError) as exc:
        return (CheckResult("egress config", Status.CRITICAL, str(exc)),)
    results: list[CheckResult] = []
    for url in urls or EGRESS_URLS:
        diagnosis = diagnose(url, settings, TRUST_HINT)
        host = urlsplit(url).hostname or url
        for step in diagnosis.steps:
            hint = f"; {diagnosis.hint}" if diagnosis.hint and not step.ok else ""
            status = Status.OK if step.ok else Status.CRITICAL
            results.append(CheckResult(f"egress {host} {step.name}", status, step.detail + hint))
    return tuple(results)


def _status(argv: Sequence[str]) -> int:
    """Run an OS trust-store command and return its exit status.

    Args:
        argv: Argument list.

    Returns:
        int: Exit status.

    Raises:
        None
    """
    return run_command(argv, timeout=120.0).returncode


def _fail(message: str) -> int:
    """Print an error and return the failure status.

    Args:
        message: Error text.

    Returns:
        int: 1.

    Raises:
        None
    """
    print(f"vakr: {message}", file=sys.stderr)
    return 1
