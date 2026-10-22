"""``vakr replication``: the partner's pull token, and this node's replication status (MPOS-006).

``token create LABEL [--lifetime DAYSd]`` issues the token the partner node
installs in its ``/etc/vakr/secrets/replication.token``, and prints it once.
``token list [--json]`` shows records, never tokens or digests. ``token
revoke ID|LABEL`` refuses the partner's next pull. ``status [--json]``
shows this node's role and position and, on a peer, the newest metadata
pull. Run as root; the token commands become the ``vakr`` user first.

Exit status: 0 done; 1 refused or invalid; 2 usage error; 4 the metadata
database is unavailable.

Dependencies:
    vakr.auth.replication_tokens: The token service.
    vakr.replication.status: The pull record.

Safety:
    ``create`` prints the token exactly once; nothing logs it. A token pasted
    into ``revoke`` by mistake is refused without being echoed.

Example:
    $ sudo vakr replication token create west
"""

import argparse
import sys
from dataclasses import asdict
from datetime import datetime
from uuid import UUID

from vakr.adapters.sqlite.metadata import sqlite_uow_factory
from vakr.auth.replication_tokens import ReplicationTokenService
from vakr.auth.tokens import REPLICATION_PREFIX
from vakr.cli.common import SubParsers
from vakr.cli.inventory_support import (
    EXIT_INVALID,
    EXIT_OK,
    EXIT_USAGE,
    emit,
    guarded,
    require_store,
)
from vakr.cli.user_support import cli_actor
from vakr.config.loader import ConfigError, load_effective_config
from vakr.config.node import TOKEN_FILE, NodeSettings, node_settings
from vakr.domain.ids import KioskTokenId
from vakr.domain.replication import ReplicationToken
from vakr.paths import InstallPaths
from vakr.ports.clock import SystemClock
from vakr.replication import observation_status as obs_status
from vakr.replication import status
from vakr.replication.overview import replication_reader as reader_of


def register(subparsers: SubParsers) -> None:
    """Add ``replication`` and its subcommands.

    Args:
        subparsers: The top-level sub-parser collection.

    Returns:
        None

    Raises:
        None
    """
    parser = subparsers.add_parser("replication", help="two-position replication (tokens, status)")
    parser.set_defaults(func=lambda _a: _usage(parser))
    actions = parser.add_subparsers(title="subcommands", metavar="SUBCOMMAND")
    token = actions.add_parser("token", help="the token a partner node pulls with")
    token.set_defaults(func=lambda _a: _usage(token))
    tokens = token.add_subparsers(title="subcommands", metavar="SUBCOMMAND")
    create = tokens.add_parser("create", help="issue a token for the partner node (shown once)")
    create.add_argument("label", help="the partner's name, such as west")
    create.add_argument("--lifetime", metavar="DAYS", help="1d to 730d (default 365d)")
    create.set_defaults(func=run_create)
    listing = tokens.add_parser("list", help="list tokens (never the tokens themselves)")
    listing.add_argument("--json", action="store_true", help="print JSON instead of text")
    listing.set_defaults(func=run_list)
    revoke = tokens.add_parser("revoke", help="revoke a token at once")
    revoke.add_argument("key", metavar="ID|LABEL", help="the token's ID or label")
    revoke.set_defaults(func=run_revoke)
    state = actions.add_parser("status", help="role, position, and the newest metadata pull")
    state.add_argument("--json", action="store_true", help="print JSON instead of text")
    state.set_defaults(func=run_status)


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


def _days(text: str | None) -> int | None:
    """Parse ``--lifetime`` (``90d`` or ``90``).

    Args:
        text: As typed.

    Returns:
        int | None: Days, or ``None`` for the default.

    Raises:
        ValueError: If it is not a whole number of days.
    """
    if text is None:
        return None
    digits = text.strip().removesuffix("d")
    if not digits.isdigit():
        raise ValueError("--lifetime must be a number of days, such as 365d")
    return int(digits)


@guarded
def run_create(args: argparse.Namespace) -> int:
    """Issue a token and print it once with the install steps.

    Args:
        args: ``label`` and ``lifetime``.

    Returns:
        int: Exit status.

    Raises:
        DomainError, ValueError: Translated by ``guarded``.
    """
    from datetime import timedelta

    days = _days(args.lifetime)
    store = require_store()
    try:
        service = ReplicationTokenService(sqlite_uow_factory(store), SystemClock())
        life = None if days is None else timedelta(days=days)
        issued = service.create(args.label, actor=cli_actor(), lifetime=life)
    finally:
        store.close()
    made = issued.record
    print(f"created replication token {made.label}, expires {made.expires_at:%Y-%m-%d} "
          f"(id {made.id}). It is shown once:\n\n    {issued.token.reveal()}\n\n"
          f"On the partner node, put it alone in /etc/vakr/secrets/{TOKEN_FILE} (mode 0640, "
          "owner root:vakr), then restart vakr.target there.")  # fmt: skip
    return EXIT_OK


@guarded
def run_list(args: argparse.Namespace) -> int:
    """List token records.

    Args:
        args: ``json``.

    Returns:
        int: Exit status.

    Raises:
        Unavailable: Translated by ``guarded``.
    """
    store = require_store()
    try:
        found = ReplicationTokenService(sqlite_uow_factory(store), SystemClock()).tokens()
    finally:
        store.close()
    now = SystemClock().now_utc()
    rows = [_token_row(t, now) for t in found]
    lines = [f"{t.label}  expires {t.expires_at:%Y-%m-%d}  last used "
             f"{t.last_used_at:%Y-%m-%d %H:%M} UTC" if t.last_used_at else
             f"{t.label}  expires {t.expires_at:%Y-%m-%d}  never used" for t in found]  # fmt: skip
    lines = [f"{line}  id {t.id}" for line, t in zip(lines, found, strict=True)] or [
        "no replication tokens"]  # fmt: skip
    emit(args, lines, {"replication_tokens": rows})
    return EXIT_OK


@guarded
def run_revoke(args: argparse.Namespace) -> int:
    """Revoke a token by ID or label.

    Args:
        args: ``key``.

    Returns:
        int: Exit status.

    Raises:
        Unavailable: Translated by ``guarded``.
    """
    if args.key.startswith(REPLICATION_PREFIX):
        print("vakr: that is a token, not its ID or label; see `vakr replication token list`",
              file=sys.stderr)  # fmt: skip
        return EXIT_INVALID
    store = require_store()
    try:
        service = ReplicationTokenService(sqlite_uow_factory(store), SystemClock())
        match = [t for t in service.tokens() if t.label == args.key or str(t.id) == _uuid(args.key)]
        done = bool(match) and service.revoke(KioskTokenId(match[0].id), actor=cli_actor())
    finally:
        store.close()
    if not done:
        print(f"vakr: no replication token {args.key}; see `vakr replication token list`",
              file=sys.stderr)  # fmt: skip
        return EXIT_INVALID
    print(f"revoked replication token {match[0].label}; the partner's next pull is refused")
    return EXIT_OK


def _token_row(token: ReplicationToken, now: datetime) -> dict[str, object]:
    """Describe one token record for ``--json`` (never the token or its digest).

    Args:
        token: The record.
        now: Current time, for ``live``.

    Returns:
        dict[str, object]: The row.

    Raises:
        None
    """
    used = token.last_used_at
    return {"id": str(token.id), "label": token.label, "created_by": token.created_by,
            "created_at": token.created_at.isoformat(), "expires_at": token.expires_at.isoformat(),
            "last_used_at": used.isoformat() if used else None,
            "live": token.is_live(now)}  # fmt: skip


def _uuid(text: str) -> str:
    """Normalise a typed UUID, or return ``""``.

    Args:
        text: As typed.

    Returns:
        str: The canonical UUID text, or ``""``.

    Raises:
        None
    """
    try:
        return str(UUID(text))
    except ValueError:
        return ""


def _failed(error: str | None) -> str:
    """Render a failed attempt's reason for the status line.

    Args:
        error: The recorded reason, or ``None``.

    Returns:
        str: ``"; failed: <reason>"`` or ``""``.

    Raises:
        None
    """
    return f"; failed: {error}" if error else ""


def run_status(args: argparse.Namespace) -> int:
    """Show role, position, and on a peer the newest metadata pull.

    Args:
        args: ``json``.

    Returns:
        int: 0.

    Raises:
        None
    """
    paths = InstallPaths.from_environment()
    try:
        node = node_settings(load_effective_config(paths.config_dir))
    except ConfigError:
        node = NodeSettings()
    pull = status.read_status(paths.state_dir / status.FILE) if node.is_peer else None
    obs = obs_status.read_status(paths.state_dir / obs_status.FILE)
    data = {"role": node.role, "position": node.position, "primary_url": node.primary_url or None,
            "peer_url": node.peer_url or None, "token_file": str(paths.secrets_dir / TOKEN_FILE),
            "metadata_pull": None if pull is None else vars(pull),
            "observation_pull": None if obs is None else asdict(obs)}  # fmt: skip
    lines = [f"role {node.role}, position {node.position or '(none)'}"]
    if node.is_peer:
        lines.append(f"primary {node.primary_url}")
        lines.append("no metadata pull recorded yet" if pull is None else
                     f"newest success {pull.last_success_at or 'never'}; newest attempt "
                     f"{pull.last_attempt_at}{_failed(pull.last_error)}")  # fmt: skip
    lines += [f"observations: {r.text}"] if (r := reader_of(paths.state_dir, node)()) else []
    emit(args, lines, data)
    return EXIT_OK
