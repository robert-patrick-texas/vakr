"""Shared plumbing for ``vakr setup`` and ``vakr user``: prompts, actor, services.

Both commands open the installed metadata database as the ``vakr``
service user (``inventory_support.guarded``), build ``UserAdmin`` from the
effective configuration, name the invoking operator in audit records, and
read passwords only from a hidden prompt, twice. There is deliberately no
``--password`` option and no environment variable: command lines are
visible in ``ps`` and shell history, and environments leak into child
processes and crash reports.

Dependencies:
    getpass: Hidden password prompts.
    vakr.auth: Settings, keys, password hashing, user administration.
    vakr.cli.inventory_support: Store access, error handling, and output.

Safety:
    The typed password goes straight into a ``Secret``. Policy and mismatch
    messages describe the rule, never the text. Without a terminal, each
    password is one line of standard input, so a script can pipe it in; it
    is never taken from arguments or the environment.

Example:
    >>> cli_actor().startswith("cli:")
    True
"""

import getpass
import os
import sys
from pathlib import Path

from vakr.adapters.sqlite.metadata import SqliteMetadataStore, sqlite_uow_factory
from vakr.auth.accounts import UserAdmin
from vakr.auth.keys import KEY_FILE, AuthKeyError, load_or_create_keys
from vakr.auth.passwords import Passwords
from vakr.auth.settings import auth_settings_from_config
from vakr.cli.inventory_support import local_actor
from vakr.config.loader import load_effective_config
from vakr.credentials.secret import Secret
from vakr.domain.errors import ValidationError
from vakr.domain.users import User
from vakr.paths import InstallPaths
from vakr.ports.clock import SystemClock

PASSWORD_ATTEMPTS = 3


def cli_actor() -> str:
    """Name the operator for audit records, seeing through ``sudo`` (SEC-018).

    Args:
        None

    Returns:
        str: ``cli:<sudo user> (sudo)`` under sudo, else ``cli:<login name>``.

    Raises:
        None
    """
    actor = local_actor()
    invoker = "".join(c for c in os.environ.get("SUDO_USER", "") if c.isalnum() or c in "._-")
    if actor == "cli:root" and invoker and invoker != "root":
        return f"cli:{invoker[:64]} (sudo)"
    return actor


def user_admin(store: SqliteMetadataStore) -> UserAdmin:
    """Build user administration over the installed database and configuration.

    Args:
        store: The open metadata store.

    Returns:
        UserAdmin: The service, with the configured password policy.

    Raises:
        ConfigError: If the configuration is invalid.
        ValueError: If the ``[auth]`` settings are inconsistent.
    """
    config = load_effective_config(InstallPaths.from_environment().config_dir)
    settings = auth_settings_from_config(config)
    return UserAdmin(sqlite_uow_factory(store), SystemClock(), Passwords(), settings)


def ensure_auth_key() -> tuple[Path, bool]:
    """Create the installation key file if it is missing (``vakr.auth.keys``).

    Args:
        None

    Returns:
        tuple[Path, bool]: The file and whether it was created now.

    Raises:
        ValidationError: If the file exists but is unsafe or damaged.
        OSError: If the state directory cannot be written.
    """
    path = InstallPaths.from_environment().state_dir / KEY_FILE
    existed = path.exists()
    try:
        load_or_create_keys(path)
    except AuthKeyError as exc:
        raise ValidationError(str(exc)) from None
    return path, not existed


def prompt_new_password(admin: UserAdmin, username: str) -> Secret:
    """Ask for a new password twice with hidden prompts, applying the policy.

    Args:
        admin: Supplies the password policy.
        username: The account, which the password must not equal.

    Returns:
        Secret: The accepted password.

    Raises:
        ValidationError: After ``PASSWORD_ATTEMPTS`` unacceptable or
            mismatched entries, or when input ends or is interrupted.
    """
    for _ in range(PASSWORD_ATTEMPTS):
        first = _ask(f"New password for {username}: ")
        try:
            admin.check_password(username, first)
        except ValidationError as exc:
            print(f"vakr: {exc}", file=sys.stderr)
            continue
        if _ask("Repeat the password: ") == first:
            return first
        print("vakr: the passwords do not match", file=sys.stderr)
    raise ValidationError("no acceptable password entered; nothing changed", field="password")


def interactive() -> bool:
    """Tell whether standard input is a terminal a person types at.

    Args:
        None

    Returns:
        bool: ``True`` for a terminal; ``False`` for a pipe, file, or nothing.

    Raises:
        None
    """
    return sys.stdin is not None and sys.stdin.isatty()


def _ask(prompt: str) -> Secret:
    """Read one password line: a hidden prompt on a terminal, else one line of stdin.

    ``getpass`` alone would open ``/dev/tty`` even when a script pipes the
    password in, so piped input is read here explicitly, without a prompt.

    Args:
        prompt: Prompt text (no secret in it).

    Returns:
        Secret: What was typed.

    Raises:
        ValidationError: If input ends or the operator interrupts.
    """
    try:
        line = getpass.getpass(prompt) if interactive() else _piped_line()
    except EOFError, KeyboardInterrupt:
        line = None
    if line is None:
        print(file=sys.stderr)
        raise ValidationError("cancelled; nothing changed", field="password")
    return Secret(line)


def _piped_line() -> str | None:
    """Read one line of non-terminal standard input.

    Args:
        None

    Returns:
        str | None: The line without its newline, or ``None`` at end of input.

    Raises:
        None
    """
    line = sys.stdin.readline() if sys.stdin is not None else ""
    return line.rstrip("\r\n") if line else None


def user_row(u: User) -> dict[str, object]:
    """Describe an account for ``--json`` output, without its hash.

    Args:
        u: The account.

    Returns:
        dict[str, object]: JSON-ready fields.

    Raises:
        None
    """
    last = u.last_login_at.isoformat() if u.last_login_at else None
    return {
        "username": u.username, "id": str(u.id), "display_name": u.display_name,
        "role": u.role.value, "enabled": u.enabled, "created_at": u.created_at.isoformat(),
        "password_changed_at": u.password_changed_at.isoformat(), "last_login_at": last,
    }  # fmt: skip


def user_lines(users: tuple[User, ...]) -> list[str]:
    """Render accounts as an aligned table.

    Args:
        users: The accounts.

    Returns:
        list[str]: Header and one line per account, or a hint when there are none.

    Raises:
        None
    """
    if not users:
        return ["no accounts; run `sudo vakr setup`"]
    width = max(len("USERNAME"), *(len(u.username) for u in users))
    lines = [f"{'USERNAME':<{width}}  {'ROLE':<8}  {'STATE':<8}  {'LAST LOGIN (UTC)':<16}  NAME"]
    for u in users:
        last = f"{u.last_login_at:%Y-%m-%d %H:%M}" if u.last_login_at else "never"
        state = "enabled" if u.enabled else "disabled"
        row = f"{u.username:<{width}}  {u.role.value:<8}  {state:<8}  {last:<16}  {u.display_name}"
        lines.append(row.rstrip())
    return lines
