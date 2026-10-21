"""``[api]`` options and the fixed bounds of the ``vakr-api`` service (6.2, SEC-013, E.30).

``vakr-api`` listens in two places. The TCP listener serves plain HTTP on
loopback (``127.0.0.1:8080`` by default) to a reverse proxy on the same
host, which terminates TLS (SEC-013). The Unix socket
``/run/vakr/api.sock`` serves the local TUI and CLI, and authorizes each
connection by the connecting account (Appendix E.12). Forwarded headers
are believed only from ``api.trusted_proxies``.

The operator options are read here; the remaining values are bounds that
keep every connection, body, and loop finite. They are constants listed in
Appendix E.30, because none of them is a site decision.

Dependencies:
    ipaddress: Listener and proxy addresses.
    vakr.config: The effective configuration and option catalog.
    vakr.paths: The run directory, for the default socket path.

Safety:
    Reads configuration only. ``api_problems`` names options and values,
    never secrets.

Example:
    >>> s = ApiSettings()
    >>> s.listen_address, s.port, s.max_body_bytes
    ('127.0.0.1', 8080, 65536)
"""

import ipaddress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from vakr.config import project
from vakr.config.loader import EffectiveConfig
from vakr.paths import InstallPaths

type Network = ipaddress.IPv4Network | ipaddress.IPv6Network

SOCKET_NAME = "api.sock"
"""File name of the local socket inside the run directory (E.12)."""
SOCKET_MODE = 0o666
"""Anyone may connect; SO_PEERCRED decides the role, as PostgreSQL's peer authentication does."""
TCP_CONCURRENCY = 100
SOCKET_CONCURRENCY = 32
"""Connections plus requests in progress per listener; more are answered 503 (E.30)."""
KEEP_ALIVE_S = 5
GRACEFUL_SHUTDOWN_S = 10
"""Idle keep-alive, and the wait for requests in progress at stop (E.30)."""
BACKLOG = 128
PURGE_INTERVAL_S = 300.0
PURGE_MAX_BATCHES = 10
"""Expired sessions are purged every 5 minutes, at most 10 batches of 500 per pass (E.12, E.30)."""


@dataclass(frozen=True, slots=True)
class ApiSettings:
    """Everything the API process reads from ``[api]`` (E.30).

    Attributes:
        listen_address: TCP listener address; empty turns the TCP listener off.
        port: TCP listener port.
        socket_path: The local Unix socket.
        trusted_proxies: Networks whose forwarded headers are believed (SEC-013).
        max_body_bytes: Largest request body accepted.
    """

    listen_address: str = "127.0.0.1"
    port: int = 8080
    socket_path: Path = Path("/run/vakr") / SOCKET_NAME
    trusted_proxies: tuple[Network, ...] = field(default_factory=tuple)
    max_body_bytes: int = 65536


def _get(config: EffectiveConfig, key: str) -> Any:
    """Read one option, falling back to its catalog default (CFGM-001).

    Args:
        config: Effective configuration.
        key: Dotted option key.

    Returns:
        Any: The configured value, or the packaged default.

    Raises:
        KeyError: If the catalog does not declare ``key``.
    """
    option = project.catalog().option(key)
    if option is None:
        raise KeyError(key)
    return config.get(key, option.default)


def parse_networks(entries: list[str] | tuple[str, ...]) -> tuple[Network, ...]:
    """Parse proxy addresses or CIDR networks; a bare address is a single-host network.

    Args:
        entries: Strings such as ``"127.0.0.1"`` or ``"10.0.0.0/8"``.

    Returns:
        tuple[Network, ...]: The networks.

    Raises:
        ValueError: If an entry is not an address or network (host bits set included).
    """
    return tuple(ipaddress.ip_network(str(e).strip(), strict=True) for e in entries)


def api_settings_from_config(config: EffectiveConfig, paths: InstallPaths) -> ApiSettings:
    """Build the API settings from the effective configuration.

    Args:
        config: Effective configuration, validated by the catalog.
        paths: Install layout; an empty ``api.socket_path`` means ``<run dir>/api.sock``.

    Returns:
        ApiSettings: The settings.

    Raises:
        ValueError: If a value breaks a rule ``api_problems`` reports.
    """
    if problems := api_problems(config):
        raise ValueError("; ".join(problems))
    socket_text = str(_get(config, "api.socket_path") or "")
    return ApiSettings(
        listen_address=str(_get(config, "api.listen_address") or "").strip(),
        port=int(_get(config, "api.port")),
        socket_path=Path(socket_text) if socket_text else paths.run_dir / SOCKET_NAME,
        trusted_proxies=parse_networks(list(_get(config, "api.trusted_proxies"))),
        max_body_bytes=int(_get(config, "api.max_body_bytes")),
    )


def api_problems(config: EffectiveConfig) -> list[str]:
    """Return ``[api]`` problems for ``vakr config validate`` and the startup check.

    Args:
        config: Effective configuration; single-key types and ranges are already checked.

    Returns:
        list[str]: One line per problem; empty when consistent.

    Raises:
        None
    """
    problems = []
    address = str(_get(config, "api.listen_address") or "").strip()
    if address:
        try:
            ipaddress.ip_address(address)
        except ValueError:
            problems.append(f"api.listen_address ({address!r}) must be an IP address or empty")
    socket_text = str(_get(config, "api.socket_path") or "")
    if socket_text and not Path(socket_text).is_absolute():
        problems.append(f"api.socket_path ({socket_text!r}) must be an absolute path or empty")
    for entry in _get(config, "api.trusted_proxies"):
        try:
            parse_networks([entry])
        except ValueError:
            problems.append(f"api.trusted_proxies entry {entry!r} is not an address or network")
    return problems
