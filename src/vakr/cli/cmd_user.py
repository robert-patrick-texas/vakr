"""``vakr user``: manage local web/API accounts (SEC-005, SEC-006, SEC-010, SEC-018).

``list`` shows accounts, roles, states, and last logins; ``add`` creates an
account; ``passwd`` sets a new password; ``disable``/``enable`` lock and
unlock an account; ``role`` makes an account admin or operator;
``revoke-sessions`` signs an account out everywhere. Passwords are asked
for twice at a hidden prompt. A password change, a role change, and
disabling all end the account's sessions. The last enabled admin cannot
be disabled or demoted. Every change is audited with the invoking OS user.

Exit status: 0 success; 1 refused or invalid (unknown account, policy,
last admin); 2 usage error; 4 the metadata database is unavailable.

Dependencies:
    vakr.cli.user_support, inventory_support: Prompts, actor, store access.

Safety:
    Run as root, it first becomes the ``vakr`` service user. No output
    contains a password or hash; ``list --json`` carries no hash field.

Example:
    $ sudo vakr user add bob --role operator
    New password for bob:
    Repeat the password:
    added operator account bob
"""

import argparse
import sys

from vakr.cli.common import SubParsers
from vakr.cli.inventory_support import (
    EXIT_INVALID,
    EXIT_OK,
    EXIT_USAGE,
    emit,
    guarded,
    require_store,
)
from vakr.cli.user_support import (
    cli_actor,
    prompt_new_password,
    user_admin,
    user_lines,
    user_row,
)
from vakr.domain.users import Role, canonical_username

_ROLES = [r.value for r in Role]


def register(subparsers: SubParsers) -> None:
    """Add ``user`` and its subcommands.

    Args:
        subparsers: The top-level sub-parser collection.

    Returns:
        None

    Raises:
        None
    """
    parser = subparsers.add_parser("user", help="manage local web and API accounts")
    parser.set_defaults(func=lambda _args: _usage(parser))
    actions = parser.add_subparsers(title="subcommands", metavar="SUBCOMMAND")
    listing = actions.add_parser("list", help="list accounts")
    listing.add_argument("--json", action="store_true", help="print JSON instead of text")
    listing.set_defaults(func=run_list)
    add = actions.add_parser("add", help="create an account (asks for the password)")
    add.add_argument("username")
    add.add_argument("--role", choices=_ROLES, required=True, help="admin or operator")
    add.add_argument("--display-name", default="", help="name shown in the web UI")
    add.set_defaults(func=run_add)
    simple = (
        ("passwd", run_passwd, "set a new password (ends the account's sessions)"),
        ("disable", run_disable, "lock an account and end its sessions"),
        ("enable", run_enable, "unlock an account"),
        ("revoke-sessions", run_revoke, "sign an account out everywhere"),
    )
    for name, func, text in simple:
        action = actions.add_parser(name, help=text)
        action.add_argument("username")
        action.set_defaults(func=func)
    role = actions.add_parser("role", help="change an account's role (ends its sessions)")
    role.add_argument("username")
    role.add_argument("role", choices=_ROLES)
    role.set_defaults(func=run_role)


def _usage(parser: argparse.ArgumentParser) -> int:
    """Print ``vakr user`` help when no subcommand is given.

    Args:
        parser: The ``user`` parser.

    Returns:
        int: 2, the usage-error status.

    Raises:
        None
    """
    parser.print_help(sys.stderr)
    return EXIT_USAGE


@guarded
def run_list(args: argparse.Namespace) -> int:
    """List accounts by username.

    Args:
        args: ``json``.

    Returns:
        int: Exit status.

    Raises:
        Unavailable: Translated by ``guarded``.
    """
    store = require_store()
    try:
        users = user_admin(store).users()
    finally:
        store.close()
    emit(args, user_lines(users), {"users": [user_row(u) for u in users]})
    return EXIT_OK


@guarded
def run_add(args: argparse.Namespace) -> int:
    """Create an account after asking for its password twice.

    Args:
        args: ``username``, ``role``, and ``display_name``.

    Returns:
        int: Exit status.

    Raises:
        DomainError, Unavailable: Translated by ``guarded``.
    """
    name = canonical_username(args.username)
    store = require_store()
    try:
        admin = user_admin(store)
        if admin.get(name) is not None:
            print(f"vakr: account {name} already exists; nothing changed", file=sys.stderr)
            return EXIT_INVALID
        password = prompt_new_password(admin, name)
        admin.create(
            name, Role(args.role), password, actor=cli_actor(), display_name=args.display_name
        )
    finally:
        store.close()
    print(f"added {args.role} account {name}")
    return EXIT_OK


@guarded
def run_passwd(args: argparse.Namespace) -> int:
    """Set a new password after asking for it twice; ends the account's sessions.

    Args:
        args: ``username``.

    Returns:
        int: Exit status.

    Raises:
        DomainError, Unavailable: Translated by ``guarded``.
    """
    store = require_store()
    try:
        admin = user_admin(store)
        name = canonical_username(args.username)
        if admin.get(name) is None:
            print(f"vakr: no account {name}", file=sys.stderr)
            return EXIT_INVALID
        ended = admin.set_password(name, prompt_new_password(admin, name), actor=cli_actor())
    finally:
        store.close()
    print(f"changed the password of {name}; {ended} session(s) ended")
    return EXIT_OK


def _change(args: argparse.Namespace, what: str) -> int:
    """Apply one state, role, or session change and report it.

    Args:
        args: ``username`` and, for roles, ``role``.
        what: ``disable``, ``enable``, ``role``, or ``revoke``.

    Returns:
        int: Exit status.

    Raises:
        DomainError, Unavailable: For ``guarded`` to translate.
    """
    store = require_store()
    try:
        admin, actor = user_admin(store), cli_actor()
        if what == "revoke":
            ended = admin.revoke_sessions(args.username, actor=actor)
            message = f"ended {ended} session(s)"
        elif what == "role":
            user = admin.set_role(args.username, Role(args.role), actor=actor)
            message = f"role is now {user.role.value}; its sessions ended"
        else:
            user = admin.set_enabled(args.username, what == "enable", actor=actor)
            message = "enabled" if user.enabled else "disabled; its sessions ended"
    finally:
        store.close()
    print(f"{canonical_username(args.username)}: {message}")
    return EXIT_OK


@guarded
def run_disable(args: argparse.Namespace) -> int:
    """Disable an account and end its sessions.

    Args:
        args: ``username``.

    Returns:
        int: Exit status.

    Raises:
        DomainError, Unavailable: Translated by ``guarded``.
    """
    return _change(args, "disable")


@guarded
def run_enable(args: argparse.Namespace) -> int:
    """Enable an account.

    Args:
        args: ``username``.

    Returns:
        int: Exit status.

    Raises:
        DomainError, Unavailable: Translated by ``guarded``.
    """
    return _change(args, "enable")


@guarded
def run_role(args: argparse.Namespace) -> int:
    """Change an account's role and end its sessions.

    Args:
        args: ``username`` and ``role``.

    Returns:
        int: Exit status.

    Raises:
        DomainError, Unavailable: Translated by ``guarded``.
    """
    return _change(args, "role")


@guarded
def run_revoke(args: argparse.Namespace) -> int:
    """End every session of an account.

    Args:
        args: ``username``.

    Returns:
        int: Exit status.

    Raises:
        DomainError, Unavailable: Translated by ``guarded``.
    """
    return _change(args, "revoke")
