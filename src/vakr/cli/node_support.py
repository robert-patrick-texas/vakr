"""Side effects of ``vakr node promote`` and ``demote``: the partner, audit, and restarts (E.39).

``cmd_node`` decides with ``vakr.replication.promotion`` and calls these:

- ``partner_clients`` builds the partner client the collector would use:
  the token in ``/etc/vakr/secrets/replication.token`` and the shared
  egress settings (proxy, private CAs, strict TLS), 20 s timeout;
- ``audit_change`` appends the ``node.promote`` or ``node.demote`` audit
  record. The command runs as root (it writes ``/etc/vakr`` and restarts
  units), so the database write runs in a child process that becomes the
  ``vakr`` user first, leaving no root-owned WAL or journal file behind;
- ``restart_services`` runs ``systemctl try-restart`` on the two units
  that read ``node.role`` at start, ``vakr-collector`` (the metadata pull
  and peer inventory) and ``vakr-api`` (the peer refusal), and nothing else.

Dependencies:
    os, subprocess: The child process and ``systemctl`` (argument list, no shell).
    vakr.auth.audit, vakr.replication.client: The record and the client.

Safety:
    The token stays inside the client; nothing here prints or logs it.

Example:
    >>> UNITS
    ('vakr-collector.service', 'vakr-api.service')
"""

import os
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime
from typing import Any

from vakr.adapters.sqlite.metadata import sqlite_uow_factory
from vakr.auth.audit import record
from vakr.cli.inventory_support import StorageUnavailableError, open_store
from vakr.cli.service_user import drop_to_service_user
from vakr.config.loader import EffectiveConfig
from vakr.domain.errors import DomainError
from vakr.paths import InstallPaths
from vakr.replication.client import PartnerClient, PartnerReader
from vakr.replication.token_file import read_token, token_path

SYSTEMCTL = "/usr/bin/systemctl"
UNITS = ("vakr-collector.service", "vakr-api.service")
RESTART_TIMEOUT_S = 120


def partner_clients(paths: InstallPaths, config: EffectiveConfig) -> Callable[[str], PartnerReader]:
    """Return a builder of partner clients with this node's token and egress settings.

    Args:
        paths: The installation layout.
        config: The effective configuration.

    Returns:
        Callable[[str], PartnerReader]: URL to client; reads the token when called.

    Raises:
        None
    """
    from vakr.cli.cmd_tls import build_egress_settings

    def build(url: str) -> PartnerReader:
        """Build one client.

        Args:
            url: The partner's base URL.

        Returns:
            PartnerReader: The client.

        Raises:
            TokenFileError: If the token file is missing or unsafe.
            OSError, ValueError: If the egress settings cannot be built.
        """
        token = read_token(token_path(paths.secrets_dir))
        return PartnerClient(url, token, build_egress_settings(paths, config))

    return build


def as_service_user(work: Callable[[], None]) -> str | None:
    """Run ``work`` as the ``vakr`` user, in a child process when this one is root.

    Args:
        work: The database write.

    Returns:
        str | None: ``None`` when it worked, else why it did not.

    Raises:
        None
    """
    if os.geteuid() != 0 or os.environ.get("VAKR_ROOT"):
        try:
            work()
        except (DomainError, OSError, StorageUnavailableError) as exc:
            return str(exc)
        return None
    sys.stdout.flush()
    child = os.fork()
    if child == 0:  # pragma: no cover - the child of a root process on an installed node
        code = 1
        try:
            if drop_to_service_user() is None:
                work()
                code = 0
        finally:
            os._exit(code)
    _, state = os.waitpid(child, 0)
    return None if os.waitstatus_to_exitcode(state) == 0 else "the audit record was not written"


def audit_change(action: str, summary: str, at: datetime, actor: str) -> str | None:
    """Append the audit record of a role change to this node's metadata database.

    Args:
        action: ``node.promote`` or ``node.demote``.
        summary: Redacted summary: roles and URLs only.
        at: When (UTC).
        actor: ``cli:<login>``.

    Returns:
        str | None: ``None`` when written, else why not.

    Raises:
        None
    """

    def write() -> None:
        """Open the store and append the record.

        Args:
            None

        Returns:
            None

        Raises:
            DomainError: If the database is unavailable or the write fails.
        """
        store = open_store(required=True)
        if store is None:  # open_store raises instead; kept for the type checker
            return
        try:
            with sqlite_uow_factory(store)() as uow:
                record(uow, action, None, summary, at=at, actor=actor)
                uow.commit()
        finally:
            store.close()

    return as_service_user(write)


def restart_services(run: Callable[..., Any] = subprocess.run) -> str:
    """Restart the units that read ``node.role`` at start, if they run.

    Args:
        run: ``subprocess.run``; tests pass a fake.

    Returns:
        str: What happened, for the operator.

    Raises:
        None
    """
    if os.environ.get("VAKR_ROOT"):
        return "not restarted (VAKR_ROOT is set): restart vakr-collector and vakr-api yourself"
    try:
        done = run([SYSTEMCTL, "try-restart", *UNITS], capture_output=True, text=True,
                   timeout=RESTART_TIMEOUT_S, check=False)  # fmt: skip
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"restart failed ({exc}); run `sudo systemctl restart {' '.join(UNITS)}`"
    if done.returncode != 0:
        why = (done.stderr or "").strip().splitlines()[-1:] or ["no message"]
        return f"restart failed ({why[0]}); run `sudo systemctl restart {' '.join(UNITS)}`"
    return f"restarted {' and '.join(UNITS)} (if they were running)"


def confirm(question: str, yes: bool) -> bool:
    """Ask at a terminal unless ``--yes`` was given; elsewhere go ahead.

    Args:
        question: The question.
        yes: ``--yes``.

    Returns:
        bool: Whether to go ahead.

    Raises:
        None
    """
    if yes or not (sys.stdin and sys.stdin.isatty()):
        return True
    try:
        return input(f"{question} [y/N]: ").strip().lower() in ("y", "yes")
    except EOFError:
        return False
