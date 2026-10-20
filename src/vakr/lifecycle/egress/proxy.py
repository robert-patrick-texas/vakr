"""Choose the HTTPS proxy for a destination from configuration or environment.

Enterprise networks often force outbound HTTPS through an HTTP proxy that
tunnels TLS with ``CONNECT``. Operators set the proxy in configuration, or
rely on the standard ``HTTPS_PROXY``/``NO_PROXY`` variables already used by
apt and curl on the host (EGRESS-001).

Precedence, resolved separately for each of the two settings:

1. a non-empty configured value (``https_proxy`` string, ``no_proxy`` list);
2. otherwise ``HTTPS_PROXY`` then ``https_proxy`` (``NO_PROXY`` then
   ``no_proxy``) from the environment;
3. otherwise no proxy, or no bypass entries.

``no_proxy`` entries are ``*`` (bypass everything), a host or domain
(``example.net`` and ``.example.net`` both match the domain and its
subdomains), an IP address, or a CIDR network matched against IP-literal
hosts. An optional ``:port`` suffix restricts a host entry to that port.

Dependencies:
    ipaddress, urllib.parse: Address and URL parsing.

Safety:
    Proxy URLs containing credentials are rejected: proxy credentials must
    come from the credential provider, never from plain configuration or an
    environment variable that ends up in logs (EGRESS-001).

Example:
    >>> policy = resolve_policy("http://proxy.corp:3128", ["corp"], {})
    >>> str(policy.proxy_for("api.github.com")), policy.proxy_for("git.corp")
    ('http://proxy.corp:3128', None)
"""

import ipaddress
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

from vakr.lifecycle.egress.errors import EgressError


@dataclass(frozen=True, slots=True)
class ProxyEndpoint:
    """An HTTP proxy that accepts ``CONNECT`` tunnels.

    Attributes:
        host: Proxy host name or IP address.
        port: Proxy TCP port.
    """

    host: str
    port: int

    def __str__(self) -> str:
        """Render as a credential-free proxy URL.

        Args:
            None

        Returns:
            str: ``http://host:port`` (IPv6 hosts in brackets).

        Raises:
            None
        """
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{host}:{self.port}"


@dataclass(frozen=True, slots=True)
class ProxyPolicy:
    """Resolved proxy settings for all outbound HTTPS from one process.

    Attributes:
        https_proxy: Proxy to tunnel through, or ``None`` for direct.
        no_proxy: Normalized bypass entries.
        source: Where ``https_proxy`` came from: ``"config"``,
            ``"environment"``, or ``"none"``.
    """

    https_proxy: ProxyEndpoint | None
    no_proxy: tuple[str, ...] = ()
    source: str = "none"

    def proxy_for(self, host: str, port: int = 443) -> ProxyEndpoint | None:
        """Return the proxy to use for ``host:port``, or ``None`` for direct.

        Args:
            host: Destination host name or IP literal.
            port: Destination port.

        Returns:
            ProxyEndpoint | None: The proxy unless no proxy is configured or
            a ``no_proxy`` entry matches the destination.

        Raises:
            None
        """
        if self.https_proxy is None or bypasses(host, port, self.no_proxy):
            return None
        return self.https_proxy


def parse_proxy_url(text: str) -> ProxyEndpoint:
    """Parse ``http://host:port`` (or bare ``host:port``) into an endpoint.

    Args:
        text: Proxy URL from configuration or the environment.

    Returns:
        ProxyEndpoint: Host and explicit port.

    Raises:
        EgressError: Step ``"config"`` for credentials in the URL, a scheme
            other than ``http``, or a missing or invalid port.
    """
    raw = text.strip()
    parts = urlsplit(raw if "://" in raw else f"http://{raw}")
    if parts.username is not None or parts.password is not None:
        raise EgressError("config", "proxy URL must not contain credentials")
    if parts.scheme != "http":
        raise EgressError("config", f"unsupported proxy scheme {parts.scheme!r}; use http://")
    try:
        port = parts.port
    except ValueError as exc:
        raise EgressError("config", "proxy URL has an invalid port") from exc
    if not parts.hostname or port is None:
        raise EgressError("config", "proxy URL must be http://HOST:PORT with an explicit port")
    return ProxyEndpoint(parts.hostname, port)


def split_no_proxy(value: str | Sequence[str] | None) -> tuple[str, ...]:
    """Normalize ``no_proxy`` from a comma list or a sequence of entries.

    Args:
        value: ``"a.example,10.0.0.0/8"``, ``["a.example"]``, or ``None``.

    Returns:
        tuple[str, ...]: Lower-cased, stripped, non-empty entries.

    Raises:
        None
    """
    if value is None:
        return ()
    items = value.split(",") if isinstance(value, str) else list(value)
    return tuple(e for item in items if (e := str(item).strip().lower()))


def resolve_policy(
    config_proxy: str | None,
    config_no_proxy: str | Sequence[str] | None,
    environ: Mapping[str, str],
) -> ProxyPolicy:
    """Apply the documented precedence to configuration and environment.

    Args:
        config_proxy: ``[network] https_proxy``; empty or ``None`` if unset.
        config_no_proxy: ``[network] no_proxy``; empty or ``None`` if unset.
        environ: Process environment to fall back to.

    Returns:
        ProxyPolicy: The effective proxy and bypass list.

    Raises:
        EgressError: If the chosen proxy URL is invalid.
    """
    env_proxy = environ.get("HTTPS_PROXY") or environ.get("https_proxy") or ""
    env_no_proxy = environ.get("NO_PROXY") or environ.get("no_proxy") or ""
    no_proxy = split_no_proxy(config_no_proxy) or split_no_proxy(env_no_proxy)
    if config_proxy and config_proxy.strip():
        return ProxyPolicy(parse_proxy_url(config_proxy), no_proxy, "config")
    if env_proxy.strip():
        return ProxyPolicy(parse_proxy_url(env_proxy), no_proxy, "environment")
    return ProxyPolicy(None, no_proxy, "none")


def bypasses(host: str, port: int, entries: Sequence[str]) -> bool:
    """Return whether any ``no_proxy`` entry matches ``host:port``.

    Args:
        host: Destination host name or IP literal.
        port: Destination port.
        entries: Normalized bypass entries.

    Returns:
        bool: ``True`` when the destination must be reached directly.

    Raises:
        None
    """
    name = host.strip("[]").lower().rstrip(".")
    return any(_entry_matches(name, port, entry) for entry in entries)


def _entry_matches(host: str, port: int, entry: str) -> bool:
    """Match one normalized ``no_proxy`` entry against a destination.

    Args:
        host: Lower-cased destination without brackets or trailing dot.
        port: Destination port.
        entry: One bypass entry.

    Returns:
        bool: Whether the entry selects the destination.

    Raises:
        None
    """
    if entry == "*":
        return True
    if "/" in entry:
        return _in_network(host, entry)
    pattern, _, entry_port = entry.rpartition(":") if _has_port(entry) else (entry, "", "")
    if entry_port and entry_port != str(port):
        return False
    pattern = pattern.strip("[]").lstrip("*").lstrip(".").rstrip(".")
    return bool(pattern) and (host == pattern or host.endswith(f".{pattern}"))


def _has_port(entry: str) -> bool:
    """Return whether ``entry`` ends in ``:digits`` and is not a bare IPv6 address.

    Args:
        entry: One bypass entry.

    Returns:
        bool: ``True`` for ``host:443`` or ``[::1]:443``.

    Raises:
        None
    """
    head, sep, tail = entry.rpartition(":")
    if not sep or not tail.isdigit():
        return False
    return entry.startswith("[") or ":" not in head


def _in_network(host: str, cidr: str) -> bool:
    """Return whether IP-literal ``host`` lies inside network ``cidr``.

    Args:
        host: Destination; names that are not IP literals never match.
        cidr: Network such as ``10.0.0.0/8``.

    Returns:
        bool: Membership; ``False`` for invalid input.

    Raises:
        None
    """
    try:
        return ipaddress.ip_address(host) in ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return False
