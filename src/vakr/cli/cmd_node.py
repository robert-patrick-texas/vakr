"""``vakr node``: promote a peer, demote a primary, and show this node's role (MPOS-003, E.39).

``sudo vakr node promote [--force]`` makes a peer the metadata primary when
the primary is lost. It reports the newest successful metadata pull and its
age, and asks the old primary whether it is still up: if its API still
answers as a primary, or it cannot be told, it refuses without ``--force``.
It then rewrites ``[node]`` through the configuration machinery (backup
first): ``role = "primary"``, no ``primary_url``, and the old primary as
``peer_url``, so observation replication keeps working both ways. It
records the change with the old values in ``node-role.json``, audits it,
and restarts ``vakr-collector`` and ``vakr-api``.

``sudo vakr node demote [--primary-url URL]`` makes a returning old primary a
peer of its successor (``node.peer_url`` by default). Its collector's next
successful metadata pull backs up ``metadata.db``, replaces the replicated
metadata with the new primary's, and reports the edits it discarded.

``vakr node status [--json]`` shows the role, the position, the primary,
the newest pull, two primaries, a pending resync, and the newest report.

Exit status: 0 done; 1 refused or failed; 2 usage error.

Dependencies:
    vakr.replication.promotion, role_record: The decisions and the records.
    vakr.config.node_edit: The configuration change.
    vakr.cli.node_support: The partner client, audit, and restarts.

Safety:
    The token never leaves the partner client. Nothing changes when a check fails.

Example:
    $ sudo vakr node promote
"""

import argparse
import os
import sys
from datetime import UTC, datetime

from vakr.cli import node_support
from vakr.cli.common import SubParsers
from vakr.cli.inventory_support import emit
from vakr.cli.node_apply import apply_change
from vakr.config.loader import ConfigError, EffectiveConfig, load_effective_config
from vakr.config.node import NodeSettings, node_settings
from vakr.paths import InstallPaths
from vakr.replication import promotion, status
from vakr.replication.promotion import PartnerProbe

EXIT_OK, EXIT_REFUSED, EXIT_USAGE = 0, 1, 2
CLIENTS = node_support.partner_clients
"""Builds the partner client; tests replace it with a fake partner."""


def register(subparsers: SubParsers) -> None:
    """Add ``node`` and its subcommands.

    Args:
        subparsers: The top-level sub-parser collection.

    Returns:
        None

    Raises:
        None
    """
    parser = subparsers.add_parser("node", help="this node's role: promote, demote, status")
    parser.set_defaults(func=lambda _a: _usage(parser))
    actions = parser.add_subparsers(title="subcommands", metavar="SUBCOMMAND")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    common.add_argument("--no-restart", action="store_true", help="change files only")
    up = actions.add_parser("promote", parents=[common], help="make this peer the primary")
    up.add_argument("--force", action="store_true",
                    help="promote even if the old primary answers or never was pulled")  # fmt: skip
    up.set_defaults(func=run_promote)
    down = actions.add_parser("demote", parents=[common], help="make this primary a peer")
    down.add_argument("--primary-url", help="the new primary (default: node.peer_url)")
    down.set_defaults(func=run_demote)
    show = actions.add_parser("status", help="role, primary, newest pull, and conflicts")
    show.add_argument("--json", action="store_true", help="print JSON instead of text")
    show.set_defaults(func=run_status)


def _usage(parser: argparse.ArgumentParser) -> int:
    """Print help when no subcommand is given.

    Args:
        parser: The parser.

    Returns:
        int: 2, the usage-error status.

    Raises:
        None
    """
    parser.print_help(sys.stderr)
    return EXIT_USAGE


def _refuse(message: str) -> int:
    """Print a refusal.

    Args:
        message: Why.

    Returns:
        int: 1.

    Raises:
        None
    """
    print(f"vakr: {message}", file=sys.stderr)
    return EXIT_REFUSED


def _load() -> tuple[InstallPaths, EffectiveConfig]:
    """Read the layout and the effective configuration, which must be valid.

    Args:
        None

    Returns:
        tuple: Paths and configuration.

    Raises:
        ConfigError: If the configuration is invalid.
        PermissionError: When not run as root on an installed node.
    """
    if os.geteuid() != 0 and not os.environ.get("VAKR_ROOT"):
        raise PermissionError("this changes /etc/vakr and restarts services: run it with sudo")
    paths = InstallPaths.from_environment()
    return paths, load_effective_config(paths.config_dir)


def run_promote(args: argparse.Namespace) -> int:
    """Promote this peer to primary, if the preconditions hold.

    Args:
        args: ``force``, ``yes``, ``no_restart``.

    Returns:
        int: Exit status.

    Raises:
        None
    """
    try:
        paths, config = _load()
    except (ConfigError, PermissionError) as exc:
        return _refuse(str(exc))
    node, now = node_settings(config), datetime.now(UTC)
    pull = status.read_status(paths.state_dir / status.FILE)
    probe = promotion.probe_partner(node.primary_url, CLIENTS(paths, config)) if node.is_peer \
        else PartnerProbe("", None)  # fmt: skip
    found = promotion.promote_decision(node, pull, probe, force=args.force, now=now)
    extra = {"last_pull_at": None if pull is None else pull.last_success_at,
             "pull_age_s": None if pull is None else pull.success_age_s(now)}  # fmt: skip
    return apply_change(args, paths, node, found, "promote", promotion.promoted(node), probe, extra)


def run_demote(args: argparse.Namespace) -> int:
    """Demote this primary to a peer of the new primary.

    Args:
        args: ``primary_url``, ``yes``, ``no_restart``.

    Returns:
        int: Exit status.

    Raises:
        None
    """
    try:
        paths, config = _load()
    except (ConfigError, PermissionError) as exc:
        return _refuse(str(exc))
    node = node_settings(config)
    url = (args.primary_url or node.peer_url).strip().rstrip("/")
    probe = promotion.probe_partner(url, CLIENTS(paths, config)) if url and not node.is_peer \
        else PartnerProbe(url, None)  # fmt: skip
    found = promotion.demote_decision(node, url, probe)
    return apply_change(args, paths, node, found, "demote", promotion.demoted(url), probe, {})


def run_status(args: argparse.Namespace) -> int:
    """Show this node's role, its primary, the newest pull, conflicts, and resync.

    Args:
        args: ``json``.

    Returns:
        int: 0.

    Raises:
        None
    """
    from vakr.cli.node_status import node_status

    paths = InstallPaths.from_environment()
    try:
        node = node_settings(load_effective_config(paths.config_dir))
    except ConfigError:
        node = NodeSettings()
    lines, data = node_status(paths, node, datetime.now(UTC))
    emit(args, lines, data)
    return EXIT_OK
