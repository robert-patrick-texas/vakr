"""``vakr self-update``: verified update from signed GitHub releases (UPD-001..008).

Run as root on a monitoring host, it finds the newest release on the
configured channel, verifies the signed checksums, backs up configuration
and databases, installs the package with ``apt-get``, runs post-install
hooks through the new binary, and rolls back if anything fails. The
optional daily timer runs ``vakr self-update --yes --if-newer --unattended``.
This module only binds the portable updater (``vakr.lifecycle.update``) to
vakr's paths, configuration, hooks, and console; it also provides the
``update`` health check.

Dependencies:
    vakr.lifecycle.update: The updater; vakr.cli.cmd_tls: egress settings.
    vakr.paths, vakr.config.loader, vakr.version: Paths, settings, version.

Safety:
    Nothing changes without confirmation or ``--yes``, and nothing changes
    before verification. The health check reads one local file only.

Example:
    $ sudo vakr self-update --check
    $ sudo vakr self-update --file vakr_0.2.0_amd64.deb --sums SHA256SUMS --sig SHA256SUMS.sig
"""

import argparse
import functools
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from vakr.adapters.sqlite.metadata import METADATA_FILE
from vakr.cli.cmd_tls import DIAGNOSIS_TIMEOUT, STRICT_HINT, TRUST_HINT, build_egress_settings
from vakr.cli.common import SubParsers
from vakr.cli.selfupdate_extras import health_hook, new_options_lines
from vakr.cli.tls_strict import FALLBACK
from vakr.config.loader import EffectiveConfig, load_effective_config
from vakr.health.lifecycle_checks import check_update  # noqa: F401 - re-export for callers
from vakr.lifecycle.egress.client import HttpsClient
from vakr.lifecycle.egress.diagnose import diagnose, render
from vakr.lifecycle.egress.errors import EgressError
from vakr.lifecycle.update.console import confirmer
from vakr.lifecycle.update.github import GITHUB_API
from vakr.lifecycle.update.journal import EventSink
from vakr.lifecycle.update.lock import LockBusyError
from vakr.lifecycle.update.model import Hook, Layout, Outcome, Toolkit
from vakr.lifecycle.update.policy import Request
from vakr.lifecycle.update.process import apt_installer, run_command
from vakr.lifecycle.update.releases import CHANNELS, ReleaseError
from vakr.lifecycle.update.sources import Project
from vakr.lifecycle.update.updater import SelfUpdater
from vakr.lifecycle.update.verify import Artifacts, ReleaseTrust
from vakr.lifecycle.update.versions import Version
from vakr.paths import InstallPaths
from vakr.version import get_version

PACKAGE, ARCH, SIGNER = "vakr", "amd64", "vakr-release"
API_BASE = GITHUB_API
SYSTEMCTL = "/usr/bin/systemctl"
INSTALLER = apt_installer
"""Installer factory; tests replace it to avoid touching the host."""
UTC_NOW = functools.partial(datetime.now, UTC)


def register(subparsers: SubParsers) -> None:
    """Add the ``self-update`` subcommand and its flags.

    Args:
        subparsers: The top-level sub-parser collection.

    Returns:
        None

    Raises:
        None
    """
    parser = subparsers.add_parser(
        "self-update", help="update vakr from signed GitHub releases (UPD-*)"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="only report whether an update exists")
    mode.add_argument("--version", dest="target", metavar="X", help="install release X")
    mode.add_argument("--file", type=Path, metavar="PKG", help="install a hand-copied package")
    mode.add_argument("--rollback", action="store_true", help="return to the previous version")
    parser.add_argument("--sums", type=Path, metavar="SUMS", help="SHA256SUMS for --file")
    parser.add_argument("--sig", type=Path, metavar="SIG", help="SHA256SUMS.sig for --file")
    parser.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    parser.add_argument("--if-newer", action="store_true", help="only install a newer version")
    parser.add_argument("--unattended", action="store_true", help="timer mode (UPD-003, UPD-006)")
    parser.add_argument("--channel", choices=CHANNELS, help="override update.channel")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Dispatch to check, update, offline install, or rollback.

    Args:
        args: Parsed ``self-update`` arguments.

    Returns:
        int: 0 on success or nothing to do, 1 on failure, 2 on usage errors.

    Raises:
        None
    """
    paths = InstallPaths.from_environment()
    try:
        config = load_effective_config(paths.config_dir, strict=False)
        if config.issues:
            print(
                f"vakr: warning: {len(config.issues)} configuration problem(s); "
                "continuing so an update can fix them (see `vakr config validate`)",
                file=sys.stderr,
            )
        request = _request(args, config)
        current = Version.parse(get_version())
    except (OSError, ValueError) as exc:
        return _fail(str(exc), 2)
    if not args.check and os.geteuid() != 0 and not os.environ.get("VAKR_ROOT"):
        return _fail("self-update changes the system and must run as root")
    # EGRESS-005: --check shows every step to the API host before asking it anything.
    if args.check and not _print_diagnosis(paths, config):
        return _fail("GitHub API is not reachable; see the steps above")
    try:
        updater = _updater(paths, config, args.unattended, offline=bool(args.file or args.rollback))
        outcome = _dispatch(args, updater, current, request)
    except (LockBusyError, ReleaseError, OSError) as exc:
        return _fail(str(exc))
    except EgressError as exc:
        _print_diagnosis(paths, config)
        return _fail(str(exc))
    for line in outcome.summary(PACKAGE):
        print(line)
    if outcome.status == "updated":
        new_vakr = str(paths.opt_dir / "venv/bin/vakr")
        for line in new_options_lines(new_vakr, outcome.from_version, run_command):
            print(line)
    return outcome.exit_code


def _dispatch(
    args: argparse.Namespace, updater: SelfUpdater, current: Version, request: Request
) -> Outcome:
    """Run the operation the flags select.

    Args:
        args: Parsed arguments.
        updater: Configured updater.
        current: Installed version.
        request: Channel, hold, and mode options.

    Returns:
        Outcome: Result to summarize.

    Raises:
        LockBusyError, ReleaseError, EgressError, OSError: From the updater.
    """
    confirm = confirmer(args.yes)
    if args.check:
        return updater.check(current, request)
    if args.rollback:
        return updater.rollback(current, confirm)
    if args.file:
        files = Artifacts(args.file, args.sums, args.sig)
        return updater.install_files(current, request, files, confirm)
    return updater.update(current, request, confirm)


def _request(args: argparse.Namespace, config: EffectiveConfig) -> Request:
    """Combine flags with ``[update]`` configuration.

    Args:
        args: Parsed arguments.
        config: Effective configuration.

    Returns:
        Request: Channel, hold, target, and mode.

    Raises:
        ValueError: For ``--file`` without ``--sums`` and ``--sig``, or an
            unparseable ``--version`` or ``hold_version``.
    """
    if args.file and not (args.sums and args.sig):
        raise ValueError("--file needs --sums and --sig")
    channel = args.channel or config.get("update.channel")
    hold = str(config.get("update.hold_version") or "")
    return Request(
        channel=str(channel),
        hold=Version.parse(hold) if hold else None,
        version=Version.parse(args.target) if args.target else None,
        if_newer=args.if_newer,
        unattended=args.unattended,
    )


def _updater(
    paths: InstallPaths, config: EffectiveConfig, unattended: bool, offline: bool
) -> SelfUpdater:
    """Bind the portable updater to vakr's paths, hooks, and console.

    Args:
        paths: Installation paths.
        config: Effective configuration.
        unattended: Timer mode (events go to journald without echo).
        offline: Whether the operation may work without network access.

    Returns:
        SelfUpdater: Ready to run.

    Raises:
        EgressError: When the trust store is unusable and ``offline`` is false.
    """
    repository = config.get("update.repository")
    trust = ReleaseTrust(paths.release_signers, SIGNER, SIGNER)
    project = Project(str(repository), PACKAGE, ARCH, trust, api_base=API_BASE)
    new_vakr = str(paths.opt_dir / "venv/bin/vakr")
    restart = Hook("restart services", (SYSTEMCTL, "try-restart", "vakr.target"))
    hooks = (
        # Exit 3 means "not implemented yet" until the config upgrade command lands.
        Hook("config upgrade", (new_vakr, "config", "upgrade"), skip_codes=frozenset({3})),
        restart,
        health_hook(new_vakr, int(config.get("update.health_settle_seconds") or 0)),
    )
    layout = Layout(
        cache_root=paths.cache_dir / "updates",
        # Root's own directory: rollback restores what is kept here (UPD-005).
        backups_dir=paths.update_dir / "backups",
        state_file=paths.update_dir / "update-state.json",
        lock_file=paths.run_dir / "self-update.lock",
        config_dir=paths.config_dir,
        db_dir=paths.state_dir,
        # Not telemetry.db: it reaches about 40 GB per node, too much to copy per update (E.14).
        db_names=(METADATA_FILE,),
        legacy_backups_dir=paths.state_dir / "backups",
        legacy_state_file=paths.state_dir / "update-state.json",
    )
    emit = EventSink("vakr-update", echo_always=not unattended)
    kit = Toolkit(layout, hooks, (restart,), INSTALLER(run_command), run_command, emit, UTC_NOW)
    try:
        http = HttpsClient(build_egress_settings(paths, config))
    except EgressError:
        if not offline:
            raise
        http = None
    return SelfUpdater(project, kit, http)


def _print_diagnosis(paths: InstallPaths, config: EffectiveConfig) -> bool:
    """Print the step-by-step egress diagnosis for the API host (EGRESS-005, EGRESS-007).

    Args:
        paths: Installation paths.
        config: Effective configuration.

    Returns:
        bool: Whether every step succeeded.

    Raises:
        None
    """
    try:
        settings = build_egress_settings(paths, config, timeout=DIAGNOSIS_TIMEOUT)
    except EgressError as exc:
        print(f"egress configuration: {exc}")
        return False
    result = diagnose(f"{API_BASE}/", settings, TRUST_HINT, STRICT_HINT)
    for line in render(result):
        print(line)
    if not settings.strict_x509:
        print(f"  note: strict X.509 checks relaxed for outbound HTTPS ({FALLBACK})")
    return result.ok


def _fail(message: str, code: int = 1) -> int:
    """Print an error and return ``code``.

    Args:
        message: Error text.
        code: Exit status.

    Returns:
        int: ``code``.

    Raises:
        None
    """
    print(f"vakr: {message}", file=sys.stderr)
    return code
