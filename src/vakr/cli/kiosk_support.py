"""Plumbing for ``vakr kiosk``: the service over the installed database, and its output (E.12).

``vakr kiosk`` opens the metadata database as the ``vakr`` service user,
like ``vakr user``, builds ``KioskService`` from the effective
configuration (the default lifetime is ``auth.kiosk_token_lifetime``), and
renders token records as a table or JSON. The token itself is rendered
exactly once, by ``created_lines``, right after it is made.

Dependencies:
    vakr.auth: ``KioskService`` and settings.
    vakr.adapters.sqlite.metadata: The installed store.

Safety:
    Records carry no token and no digest, so ``list`` output cannot leak
    one. ``created_lines`` is the only function that sees the token.

Example:
    >>> kiosk_lines((), datetime(2026, 10, 21, tzinfo=UTC))
    ['no kiosk tokens; create one with `sudo vakr kiosk create LABEL`']
"""

import shlex
from datetime import UTC, datetime

from vakr.adapters.sqlite.metadata import SqliteMetadataStore, sqlite_uow_factory
from vakr.auth.kiosk import IssuedKiosk, KioskService
from vakr.auth.settings import auth_settings_from_config
from vakr.config.loader import load_effective_config
from vakr.domain.sessions import KioskToken
from vakr.paths import InstallPaths
from vakr.ports.clock import SystemClock

SETUP_PATH = "/kiosk/setup"
"""The web UI page on which a display takes its token."""

__all__ = ["SETUP_PATH", "created_lines", "kiosk_lines", "kiosk_row", "kiosk_service"]


def kiosk_service(store: SqliteMetadataStore) -> KioskService:
    """Build kiosk token administration over the installed database and configuration.

    Args:
        store: The open metadata store.

    Returns:
        KioskService: The service, with the configured default lifetime.

    Raises:
        ConfigError: If the configuration is invalid.
        ValueError: If the ``[auth]`` settings are inconsistent.
    """
    config = load_effective_config(InstallPaths.from_environment().config_dir)
    return KioskService(sqlite_uow_factory(store), SystemClock(), auth_settings_from_config(config))


def _utc(moment: datetime) -> str:
    """Format a time for the table.

    Args:
        moment: Aware time.

    Returns:
        str: Such as ``2026-11-20 14:03``.

    Raises:
        None
    """
    return f"{moment.astimezone(UTC):%Y-%m-%d %H:%M}"


def kiosk_row(t: KioskToken, now: datetime) -> dict[str, object]:
    """Describe a token record for ``--json`` output, without token or digest.

    Args:
        t: The record.
        now: Current time (UTC).

    Returns:
        dict[str, object]: JSON-ready fields.

    Raises:
        None
    """
    last = t.last_used_at.isoformat() if t.last_used_at else None
    return {
        "id": str(t.id), "label": t.label, "created_by": t.created_by,
        "created_at": t.created_at.isoformat(), "expires_at": t.expires_at.isoformat(),
        "last_used_at": last, "live": t.is_live(now),
    }  # fmt: skip


def kiosk_lines(tokens: tuple[KioskToken, ...], now: datetime) -> list[str]:
    """Render token records as an aligned table, by label.

    Args:
        tokens: The records.
        now: Current time (UTC).

    Returns:
        list[str]: Header and one line per token, or a hint when there are none.

    Raises:
        None
    """
    if not tokens:
        return ["no kiosk tokens; create one with `sudo vakr kiosk create LABEL`"]
    width = max(len("LABEL"), *(len(t.label) for t in tokens))
    head = f"{'LABEL':<{width}}  {'STATE':<7}  {'EXPIRES (UTC)':<16}  {'LAST USED (UTC)':<16}  ID"
    lines = [head]
    for t in tokens:
        state = "live" if t.is_live(now) else "expired"
        last = _utc(t.last_used_at) if t.last_used_at else "never"
        lines.append(
            f"{t.label:<{width}}  {state:<7}  {_utc(t.expires_at):<16}  {last:<16}  {t.id}"
        )
    return lines


def created_lines(issued: IssuedKiosk) -> list[str]:
    """Render a new token with its setup steps; the only output that holds a token.

    Args:
        issued: The new record and its token.

    Returns:
        list[str]: Lines for standard output.

    Raises:
        None
    """
    record = issued.record
    return [
        f"created kiosk token {record.label}, expires {_utc(record.expires_at)} UTC "
        f"(id {record.id})",
        "",
        "This is the only time the token is shown; vakr keeps only its digest:",
        "",
        f"    {issued.token.reveal()}",
        "",
        f"On the display, open <your vakr address>{SETUP_PATH} and paste the token.",
        f"Revoke it with: sudo vakr kiosk revoke {shlex.quote(record.label)}",
    ]
