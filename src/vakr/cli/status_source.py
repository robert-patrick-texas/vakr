"""Choose where ``vakr status`` and ``vakr tui`` read: the ``vakr-api`` socket, else the databases.

Blueprint 13.1 makes a protected local API the default on the monitoring
node, and Appendix E.29 #10 fixes how the commands choose:

1. ``--local``: the local databases, in-process, as before the API existed.
   Run as root, the command first switches to the ``vakr`` service user.
2. Otherwise, when the API's socket (``api.socket_path``) exists: the
   running ``vakr-api`` over the socket. Nothing switches user; the socket
   serves root and members of ``vakr-admin``, ``vakr-view``, and ``vakr`` (E.30 #2).
3. No socket: ``vakr-api`` is not running (the package installs it
   disabled, PKG-003), so the local databases, and the output says so.

The local path needs the ``vakr`` group's access to the state directory.
A member of ``vakr-view`` only has the socket (E.30 #16); without that check
``metadata.db`` would look missing to them and read as "no data yet".

A socket that exists but does not answer is never quietly replaced by the
databases (13.4): the read fails, saying what to check, and ``--local`` is
the explicit way round it.

Dependencies:
    vakr.api.settings: ``socket_path_of`` (no web framework is imported).
    vakr.client.status: ``SocketStatusSource``.
    vakr.cli.status_stores: The local databases.

Safety:
    Reads configuration from ``/etc/vakr`` but never ``/etc/vakr/secrets``.

Example:
    >>> LOCAL_ASKED
    'local databases (--local)'
"""

import os
from collections.abc import Callable
from dataclasses import dataclass

from vakr.api.settings import socket_path_of
from vakr.cli.service_user import drop_to_service_user
from vakr.cli.status_stores import load_config, open_status
from vakr.client.status import SocketStatusSource
from vakr.config.loader import EffectiveConfig
from vakr.paths import InstallPaths
from vakr.query import SourceUnavailableError, StatusSource

API = "vakr-api socket"
LOCAL_ASKED = "local databases (--local)"
LOCAL_NO_API = "local databases (vakr-api is not running)"


def _nothing() -> None:
    """Close nothing: a socket source holds no connection between reads.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """


def _readable_or_say(paths: InstallPaths, no_api: str | None) -> None:
    """Refuse the local path, saying why, when this account cannot enter the state directory.

    Args:
        paths: Installation layout.
        no_api: The missing socket, when the local path is a fallback; ``None`` for ``--local``.

    Returns:
        None

    Raises:
        SourceUnavailableError: If the directory exists but this account cannot enter it.
    """
    state = paths.state_dir
    if not state.is_dir() or os.access(state, os.X_OK):
        return  # a node without the directory gets "no data yet" from open_status
    if no_api is None:
        raise SourceUnavailableError(
            f"--local reads the databases in {state}, which this account cannot read; "
            "run the command with sudo, or without --local to use vakr-api"
        )
    raise SourceUnavailableError(
        f"vakr-api is not running (no {no_api}), and this account cannot read the databases "
        f"in {state} directly; start it (sudo systemctl enable --now vakr-api.service "
        "vakr.target), or run the command with sudo"
    )


@dataclass(frozen=True, slots=True)
class DataSource:
    """Where the views come from, and how to say so.

    Attributes:
        source: The views' source.
        config: The effective configuration (display zone, refresh interval).
        kind: ``"api"`` or ``"local"``, as ``--json``'s ``source`` field.
        label: The data path in operator words, for the output and the TUI header.
        close: Releases the databases, if any were opened.
    """

    source: StatusSource
    config: EffectiveConfig
    kind: str
    label: str
    close: Callable[[], None]


def open_source(paths: InstallPaths, command: str, *, local: bool) -> DataSource:
    """Pick the data path (module docstring) and open it.

    Args:
        paths: Installation layout (``VAKR_ROOT`` relocates it in tests).
        command: Command name for warnings.
        local: ``--local`` was given.

    Returns:
        DataSource: The source and its description.

    Raises:
        SourceUnavailableError: If the switch to the ``vakr`` user fails, or this
            account cannot read the databases.
        NoDataYetError, MigrationError, StorageError: As ``open_status``, on the local path.
    """
    config = load_config(paths, command)
    label, no_api = LOCAL_ASKED, None
    if not local:
        socket = socket_path_of(config, paths)
        if socket.exists():
            return DataSource(SocketStatusSource(str(socket)), config, "api", API, _nothing)
        label, no_api = LOCAL_NO_API, str(socket)
    if (problem := drop_to_service_user()) is not None:
        raise SourceUnavailableError(problem)
    _readable_or_say(paths, no_api)
    stores = open_status(paths, command, config)
    return DataSource(stores.queries, config, "local", label, stores.close)
