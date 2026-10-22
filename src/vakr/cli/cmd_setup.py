"""``vakr setup``: first-run setup, which creates the first admin account (SEC-005, 27.4).

A new installation has no accounts. ``sudo vakr setup`` creates the
installation key (``/var/lib/vakr/auth.key``) and the first admin, asking
for the password twice at a hidden prompt, then prints what to do next:
credentials, inventory, the collector, and the web UI that arrives with
``vakr-api``. Once an enabled admin exists it refuses, so a second run
cannot quietly add an administrator; ``--add-admin`` says that is the
intent. Further accounts are managed with ``vakr user``.

Exit status: 0 created; 1 refused (an admin exists, or no acceptable
password was entered); 2 usage error; 4 the metadata database is
unavailable (``sudo vakr db migrate`` creates it).

Dependencies:
    vakr.cli.user_support, inventory_support: Prompts, actor, store access.

Safety:
    Run as root, it first becomes the ``vakr`` service user, so the key file
    and database stay owned by the services. The password is never read
    from the command line or the environment, never echoed, and never
    printed or audited.

Example:
    $ sudo vakr setup
    Username for the first admin [admin]: alice
    New password for alice:
    Repeat the password:
    Created admin account alice.
"""

import argparse
import sys

from vakr.cli.common import SubParsers
from vakr.cli.inventory_support import EXIT_INVALID, EXIT_OK, guarded, require_store
from vakr.cli.setup_node import add_arguments, apply_node_settings
from vakr.cli.user_support import (
    cli_actor,
    ensure_auth_key,
    interactive,
    prompt_new_password,
    user_admin,
)
from vakr.config.loader import ConfigError, load_effective_config
from vakr.config.node import NodeSettings, node_settings
from vakr.domain.users import Role, canonical_username
from vakr.paths import InstallPaths

DEFAULT_ADMIN = "admin"


def register(subparsers: SubParsers) -> None:
    """Add ``setup``.

    Args:
        subparsers: The top-level sub-parser collection.

    Returns:
        None

    Raises:
        None
    """
    parser = subparsers.add_parser("setup", help="first-run setup: create the first admin account")
    parser.add_argument("--username", default=None, help="admin username (default: ask)")
    parser.add_argument("--display-name", default="", help="name shown in the web UI")
    parser.add_argument(
        "--add-admin",
        action="store_true",
        help="create another admin even though one exists",
    )
    add_arguments(parser)
    parser.set_defaults(func=run_setup_entry)


def _current_node(paths: InstallPaths) -> NodeSettings:
    """Return the node settings in force, a primary when the configuration is unreadable.

    Args:
        paths: The install layout.

    Returns:
        NodeSettings: The settings.

    Raises:
        None
    """
    try:
        return node_settings(load_effective_config(paths.config_dir))
    except ConfigError:
        return NodeSettings()


def run_setup_entry(args: argparse.Namespace) -> int:
    """Write the node settings as root, then create the first admin unless this is a peer.

    Args:
        args: ``setup`` arguments, the node flags included.

    Returns:
        int: Exit status.

    Raises:
        None
    """
    paths = InstallPaths.from_environment()
    try:
        written = apply_node_settings(args, paths.config_dir / "config.toml")
    except (ValueError, EOFError) as exc:
        print(f"vakr: {exc or 'input ended'}", file=sys.stderr)
        return EXIT_INVALID
    if written is not None:
        print(f"Set node.role = {written.role}, node.position = {written.position or '(none)'}"
              f"{f', node.primary_url = {written.primary_url}' if written.primary_url else ''}; "
              "restart vakr.target to apply.")  # fmt: skip
    node = written or _current_node(paths)
    if node.is_peer:
        print("\n".join(peer_steps(node, paths)))
        return EXIT_OK
    return run_setup(args)


def peer_steps(node: NodeSettings, paths: InstallPaths) -> list[str]:
    """Say what a peer needs next: accounts and inventory come from the primary.

    Args:
        node: The peer's settings.
        paths: The install layout.

    Returns:
        list[str]: The lines.

    Raises:
        None
    """
    token = paths.secrets_dir / "replication.token"
    return [
        f"This node is a peer of {node.primary_url}: its accounts, inventory, and dashboards",
        "come from the primary (MPOS-003), so setup creates no admin here. Next:",
        f"  1. On the primary: sudo vakr replication token create {node.position or 'this-peer'}",
        f"  2. Here: put that token in {token} (one line, mode 0640, owner root:vakr)",
        "  3. Here: sudo systemctl restart vakr.target, then check with `sudo vakr check health`.",
        "See docs/operations/two-positions.md.",
    ]


@guarded
def run_setup(args: argparse.Namespace) -> int:
    """Create the installation key and the first admin, then print next steps.

    Args:
        args: ``username``, ``display_name``, and ``add_admin``.

    Returns:
        int: Exit status.

    Raises:
        Unavailable, DomainError, ConfigError: Translated by ``guarded``.
    """
    store = require_store()
    try:
        admin = user_admin(store)
        if admin.admin_exists() and not args.add_admin:
            print(
                "vakr: an admin account already exists; nothing changed. Manage accounts "
                "with `sudo vakr user`, or run `sudo vakr setup --add-admin`.",
                file=sys.stderr,
            )
            return EXIT_INVALID
        key, created = ensure_auth_key()
        if created:
            print(f"Created the installation key {key} (mode 0600).")
        name = canonical_username(args.username or _ask_username())
        password = prompt_new_password(admin, name)
        admin.create(name, Role.ADMIN, password, actor=cli_actor(), display_name=args.display_name)
    finally:
        store.close()
    print(f"Created admin account {name}.")
    print("\n".join(next_steps()))
    return EXIT_OK


def _ask_username() -> str:
    """Ask for the admin username, offering the default.

    Args:
        None

    Returns:
        str: What was typed, or ``admin`` for an empty line, no input, or no
        terminal: piped input holds only the passwords, and reading a line
        here would take a password for the username.

    Raises:
        None
    """
    if not interactive():
        return DEFAULT_ADMIN
    try:
        typed = input(f"Username for the first admin [{DEFAULT_ADMIN}]: ")
    except EOFError:
        typed = ""
    return typed.strip() or DEFAULT_ADMIN


def next_steps() -> list[str]:
    """Return the first-run checklist, with this installation's paths.

    Args:
        None

    Returns:
        list[str]: Lines to print.

    Raises:
        None
    """
    paths = InstallPaths.from_environment()
    return [
        "",
        "Next steps:",
        f"  1. Store the SNMP community in {paths.secrets_dir / 'credentials.toml'}, then run "
        "`vakr credentials check`.",
        f"  2. List devices in {paths.config_dir / 'inventory.toml'}, then run "
        "`vakr inventory plan` and `sudo vakr inventory apply`.",
        "  3. Start collecting: `sudo systemctl enable --now vakr-collector.service vakr.target`.",
        "  4. Watch with `vakr status` or `vakr tui`.",
        "The web UI arrives with the vakr-api service in a later release; sign in there with "
        "this account.",
        "Manage accounts with `sudo vakr user list|add|passwd|disable|enable|role|"
        "revoke-sessions`.",
    ]
