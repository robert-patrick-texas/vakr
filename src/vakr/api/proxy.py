"""Client address and scheme behind a trusted reverse proxy (SEC-013, AUTH-006, 15.4).

TLS ends at a reverse proxy in front of ``vakr-api``, so the connection
vakr sees comes from the proxy, over plain HTTP. The proxy says who the
real client was and which scheme it used, in ``Forwarded`` (RFC 7239) or
``X-Forwarded-For`` / ``X-Forwarded-Proto``. Anyone can send those
headers, so they are believed only when the connecting address is in
``api.trusted_proxies``; otherwise they are ignored and the connection
itself is the client.

Behind a trusted proxy the hops are read from the right (the nearest
proxy's entry) leftwards, while each address is another trusted proxy.
The first untrusted address is the client, and the scheme is the one
recorded with it. An entry that cannot be parsed stops the walk at the
proxy that wrote it, so a malformed header can never move the client
further than a trusted proxy vouched for.

When a request carries both kinds, ``X-Forwarded-*`` wins: nginx, the
documented proxy, sets those and passes a client's own ``Forwarded``
through unless told to drop it, so the client's header must not count.

Dependencies:
    ipaddress: Address parsing and network membership.

Safety:
    Pure functions over header text; at most ``MAX_HOPS`` entries are read.
    The result decides cookie flags, throttling, and audit addresses only;
    authorization never depends on it.

Example:
    >>> import ipaddress
    >>> proxies = (ipaddress.ip_network("127.0.0.1/32"),)
    >>> view = resolve_client("127.0.0.1", "http", {"x-forwarded-for": "198.51.100.7",
    ...                       "x-forwarded-proto": "https"}, proxies)
    >>> view.address, view.scheme, view.forwarded
    ('198.51.100.7', 'https', True)
    >>> resolve_client("203.0.113.9", "http", {"x-forwarded-proto": "https"}, proxies).scheme
    'http'
"""

import ipaddress
from collections.abc import Mapping
from dataclasses import dataclass

from vakr.api.settings import Network

type Address = ipaddress.IPv4Address | ipaddress.IPv6Address

MAX_HOPS = 20
"""Forwarded entries read per request; a longer chain is not a real deployment."""
SCHEMES = frozenset({"http", "https"})


@dataclass(frozen=True, slots=True)
class ClientView:
    """Who the client is and how it reached vakr, after proxy handling.

    Attributes:
        address: Client IP address as text, or ``"unknown"``.
        scheme: ``http`` or ``https``: the scheme the client used.
        peer: The connecting address.
        forwarded: True when forwarded headers from a trusted proxy were applied.
        ignored: True when forwarded headers came from an untrusted peer and were ignored.
        loopback: True when the client address is a loopback address.
    """

    address: str
    scheme: str
    peer: str
    forwarded: bool = False
    ignored: bool = False
    loopback: bool = False


@dataclass(frozen=True, slots=True)
class _Hop:
    """One forwarded entry.

    Attributes:
        address: The address it names, or ``None`` when unparseable or obfuscated.
        scheme: The scheme it records, or ``None``.
    """

    address: Address | None
    scheme: str | None


def parse_address(text: str | None) -> Address | None:
    """Parse an address as forwarded headers write it: bare, with a port, or ``[v6]:port``.

    Args:
        text: The text, possibly quoted.

    Returns:
        Address | None: The address, or ``None`` for ``unknown``, obfuscated
        names, and anything else.

    Raises:
        None
    """
    value = (text or "").strip().strip('"').strip()
    if value.startswith("["):
        value = value[1:].partition("]")[0]
    elif value.count(":") == 1:
        value = value.partition(":")[0]
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def _scheme(text: str | None) -> str | None:
    """Normalise a recorded scheme.

    Args:
        text: ``proto`` value.

    Returns:
        str | None: ``http`` or ``https``, else ``None``.

    Raises:
        None
    """
    value = (text or "").strip().strip('"').lower()
    return value if value in SCHEMES else None


def _forwarded_hops(header: str) -> list[_Hop]:
    """Parse an RFC 7239 ``Forwarded`` header into hops, left to right.

    Args:
        header: The combined header value.

    Returns:
        list[_Hop]: At most ``MAX_HOPS`` rightmost entries.

    Raises:
        None
    """
    hops = []
    for element in header.split(",")[-MAX_HOPS:]:
        pairs = {}
        for part in element.split(";"):
            name, _, value = part.partition("=")
            pairs[name.strip().lower()] = value
        hops.append(_Hop(parse_address(pairs.get("for")), _scheme(pairs.get("proto"))))
    return hops


def _x_forwarded_hops(addresses: str, protos: str) -> list[_Hop]:
    """Parse ``X-Forwarded-For`` with ``X-Forwarded-Proto`` into hops, left to right.

    When both list the same number of entries they are paired; otherwise
    only the nearest proxy's entry gets the last recorded scheme.

    Args:
        addresses: Combined ``X-Forwarded-For`` value.
        protos: Combined ``X-Forwarded-Proto`` value, possibly empty.

    Returns:
        list[_Hop]: At most ``MAX_HOPS`` rightmost entries.

    Raises:
        None
    """
    names = addresses.split(",")[-MAX_HOPS:]
    schemes = [s for s in protos.split(",") if s.strip()][-MAX_HOPS:]
    if len(schemes) == len(names):
        return [_Hop(parse_address(n), _scheme(s)) for n, s in zip(names, schemes, strict=True)]
    hops = [_Hop(parse_address(n), None) for n in names]
    if hops and schemes:
        hops[-1] = _Hop(hops[-1].address, _scheme(schemes[-1]))
    return hops


def _trusted(address: Address | None, proxies: tuple[Network, ...]) -> bool:
    """Tell whether an address belongs to a trusted proxy.

    Args:
        address: The address.
        proxies: Trusted networks.

    Returns:
        bool: True when it is inside one of them.

    Raises:
        None
    """
    return address is not None and any(address in net for net in proxies)


def _walk(
    hops: list[_Hop], client: Address, scheme: str, proxies: tuple[Network, ...]
) -> tuple[Address, str]:
    """Walk hops from the right while each address is a trusted proxy.

    Args:
        hops: Forwarded entries, left to right.
        client: The connecting (trusted) proxy.
        scheme: The connection's scheme.
        proxies: Trusted networks.

    Returns:
        tuple[Address, str]: The client and the scheme it used.

    Raises:
        None
    """
    for hop in reversed(hops):
        scheme = hop.scheme or scheme
        if hop.address is None:
            break  # unparseable: the proxy that wrote it is as far as anyone vouched
        client = hop.address
        if not _trusted(client, proxies):
            break
    return client, scheme


def resolve_client(
    peer: str | None, scheme: str, headers: Mapping[str, str], proxies: tuple[Network, ...]
) -> ClientView:
    """Work out the client address and scheme of one TCP request.

    Args:
        peer: Connecting address as the server reports it.
        scheme: Scheme of the connection itself (``http``).
        headers: Request headers, lower-case names, repeated headers joined by commas.
        proxies: ``api.trusted_proxies``.

    Returns:
        ClientView: The effective client, scheme, and how they were found.

    Raises:
        None
    """
    peer_address = parse_address(peer)
    peer_text = str(peer_address) if peer_address is not None else "unknown"
    sent = any(h in headers for h in ("forwarded", "x-forwarded-for", "x-forwarded-proto"))
    if peer_address is None or not _trusted(peer_address, proxies):
        loopback = peer_address is not None and peer_address.is_loopback
        return ClientView(peer_text, scheme, peer_text, ignored=sent, loopback=loopback)
    if "x-forwarded-for" in headers or "x-forwarded-proto" in headers or "forwarded" not in headers:
        hops = _x_forwarded_hops(
            headers.get("x-forwarded-for", ""), headers.get("x-forwarded-proto", "")
        )
    else:
        hops = _forwarded_hops(headers["forwarded"])
    client, chosen = _walk(hops, peer_address, scheme, proxies)
    return ClientView(str(client), chosen, peer_text, forwarded=sent, loopback=client.is_loopback)
